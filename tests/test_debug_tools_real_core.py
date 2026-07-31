# -*- coding: utf-8 -*-
"""The new debug tools against the REAL core running a REAL cartridge.

Every other test for these modules feeds them a fake machine, which proves the
arithmetic and nothing about the wiring. This one boots the native core with a
real ROM and a real BIOS and asks each tool for its answer -- then checks the
answers against facts the project already knows, not merely that nothing raised.

Skips cleanly when the DLL or the BIOS is absent, like the other native tests.
"""

from __future__ import annotations

import pathlib
import unittest

from core import (cheats, console, coverage_map, hwregs, movie, native, profile,
                  tilemap_view, z80_debug)

REPO = pathlib.Path(__file__).resolve().parent.parent
ROM_PATH = REPO / "tests" / "roms" / "link_probe.ngc"
BIOS_PATH = REPO / "bios.bin"


@unittest.skipUnless(native.available(), "native core not built")
@unittest.skipUnless(ROM_PATH.exists() and BIOS_PATH.exists(), "no ROM/BIOS here")
class DebugToolsOnRealCore(unittest.TestCase):
    FRAMES = 120

    @classmethod
    def setUpClass(cls) -> None:
        cls.rom = ROM_PATH.read_bytes()
        cls.machine = native.NativeMachine(cls.rom, bios=BIOS_PATH.read_bytes())
        # ⚠️ RESET FIRST. A machine that was never reset reads as a machine that
        # ran and did nothing: every register zero, no coverage, no instructions.
        # That looks exactly like a broken tool, and it is a broken harness.
        cls.machine.reset(bios_handoff=True)
        cls.machine.set_coverage(True)
        for _ in range(cls.FRAMES):
            cls.machine.run_frames(1)
        cls.read = staticmethod(lambda a, n=1: cls.machine.read(a, n))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.machine.close()

    # ---------------------------------------------------------------- hwregs
    def test_the_register_map_reads_the_console_the_bios_handed_over(self) -> None:
        """Not "it returned something": the hand-off leaves values this project has
        written down. INTE45 = 0xDC is the BIOS's own (INT4 at level 4, INT5 at 5),
        and if the map were reading the wrong addresses it would not land on it."""
        values = hwregs.read_all(self.read)
        self.assertEqual(len(values), len(hwregs.all_registers()))
        self.assertEqual(values[0x000071], 0xDC, "INTE45 as the BIOS leaves it")
        self.assertEqual(values[0x008118] & 0xC0, 0x80, "BGC enabled")
        self.assertTrue(values[0x000020] & 0x80, "the prescaler is running")

    def test_the_checks_do_not_cry_wolf_on_a_healthy_console(self) -> None:
        """A console that just booted normally must not produce errors. A checker
        that fires on a working machine is a checker nobody reads."""
        problems = hwregs.checks(hwregs.read_all(self.read))
        self.assertEqual([c.title for c in problems if c.severity == "error"], [])

    # ---------------------------------------------------------------- tilemap
    def test_a_plane_renders_and_the_camera_covers_the_screen(self) -> None:
        view = tilemap_view.read_plane(self.read, tilemap_view.SCR1,
                                       k1ge_console=self.machine.k1ge_console)
        self.assertEqual(view.rgb.shape, (256, 256, 3))
        spans = tilemap_view.camera_spans(self.machine.raster_log(),
                                          tilemap_view.SCR1)
        self.assertEqual(len(spans), tilemap_view.SCREEN_H)
        mask = tilemap_view.span_mask(spans)
        self.assertGreater(mask.sum(), 0)
        self.assertEqual(tilemap_view.compose(view, spans, grid=True).shape,
                         (256, 256, 3))

    def test_the_camera_reads_the_scroll_registers_the_core_reports(self) -> None:
        """The one place the viewer could silently disagree with the emulator."""
        log = self.machine.raster_log()
        spans = tilemap_view.camera_spans(log, tilemap_view.SCR1)
        self.assertEqual(spans[0].x, log[0][0x32])
        self.assertEqual(spans[10].y, (10 + log[10][0x33]) & 0xFF)

    # ---------------------------------------------------------------- sound CPU
    def test_the_sound_cpu_view_reads_the_real_aux_state(self) -> None:
        aux = self.machine.aux_state()
        why = z80_debug.stop_reason(aux, self.read)
        self.assertIn(why.title.split(" ")[0],
                      ("running", "halted", "held", "TRAPPED"))
        report = z80_debug.format_report(aux, self.read)
        self.assertIn("Z80 sound CPU", report)
        self.assertIn(f"PC {aux.z80_pc:04X}", report)

    def test_the_z80_map_reaches_the_shared_ram_the_main_cpu_sees(self) -> None:
        """Z80 0x0000 IS main-bus 0x7000. If the map were wrong this would read the
        CPU's own I/O page and still look like data."""
        reader = z80_debug.make_reader(self.read)
        for offset in (0x000, 0x123, 0xFFF):
            self.assertEqual(reader(offset),
                             self.machine.read(0x007000 + offset, 1)[0])

    # ---------------------------------------------------------------- coverage
    def test_coverage_recorded_real_execution(self) -> None:
        hits = self.machine.coverage_hits()
        self.assertGreater(hits, 0, "the cartridge ran, so something must be hot")
        bits = coverage_map.unpack(self.machine.coverage_bitmap(), len(self.rom))
        self.assertEqual(int(bits.sum()), hits,
                         "the unpacked bitmap must agree with the core's own count")

    def test_cold_runs_stay_inside_the_cartridge(self) -> None:
        """The bitmap always covers 2 MiB. A 10 KB ROM must not report the rest of
        the address space as dead code."""
        bits = coverage_map.unpack(self.machine.coverage_bitmap(), len(self.rom))
        for gap in coverage_map.gaps(bits, len(self.rom)):
            self.assertLess(gap.end, coverage_map.COVERAGE_LO + len(self.rom))

    # ---------------------------------------------------------------- profiler
    def test_the_profiler_accounts_for_every_instruction_it_recorded(self) -> None:
        _summary, records = self.machine.run(20_000, record=True)
        self.assertGreater(len(records), 0)
        report = profile.profile(records)
        self.assertEqual(report.total_instructions, len(records))
        self.assertEqual(sum(b.instructions for b in report.buckets), len(records))
        self.assertEqual(sum(b.cycles for b in report.buckets), report.total_cycles)
        self.assertGreater(report.total_cycles, 0, "instructions cost cycles")

    def test_the_profiler_puts_a_running_game_in_the_cartridge(self) -> None:
        """A region split that says otherwise means the addresses are being read
        wrong -- and it would be a very believable-looking wrong."""
        _summary, records = self.machine.run(20_000, record=True)
        report = profile.profile(records)
        cart = report.by_region.get("cartridge", 0)
        self.assertGreater(cart / report.total_cycles, 0.5)

    # ---------------------------------------------------------------- console
    def test_the_console_helpers_agree_with_the_bus(self) -> None:
        c = console.Console()
        c.set_namespace(console.build_namespace(self.machine, None))
        raw = self.machine.read(0x006F80, 2)
        self.assertEqual(c.run("u8(0x006F80)").output.strip(), str(raw[0]))
        self.assertEqual(c.run("u16(0x006F80)").output.strip(),
                         str(raw[0] | (raw[1] << 8)))
        self.assertIn("006F80", c.run("print(hexdump(0x006F80, 16))").output)

    def test_the_console_can_find_bytes_that_are_really_there(self) -> None:
        c = console.Console()
        c.set_namespace(console.build_namespace(self.machine, None))
        needle = self.machine.read(0x200100, 4)
        out = c.run(f"0x200100 in find({needle!r}, 0x200000, 0x200FFF)").output
        self.assertEqual(out.strip(), "True")

    # ---------------------------------------------------------------- cheats
    def test_a_cheat_reaches_work_ram_through_the_real_bus(self) -> None:
        cheat = cheats.Cheat("t", [cheats.Entry(0x004000, 2, 0x1234)], enabled=True)
        self.assertEqual(cheats.validate(cheat), [], "work RAM is a legal target")
        cheat.apply(self.machine)
        self.assertEqual(self.machine.read(0x004000, 2), b"\x34\x12")

    # ---------------------------------------------------------------- movie
    def test_a_movie_carries_a_real_savestate_and_survives_a_round_trip(self) -> None:
        state = (bytes(self.machine.cpu()) + bytes(self.machine.aux_state()))
        rec = movie.Recorder({"rom_name": ROM_PATH.name,
                              "rom_sha": movie.rom_fingerprint(self.rom)}, state)
        for byte in (0x01, 0x10, 0x00, 0x08):
            rec.record(byte)
        back = movie.load(movie.dump(rec.movie))
        self.assertEqual(bytes(back.inputs), b"\x01\x10\x00\x08")
        self.assertEqual(back.state, state)
        self.assertEqual(
            movie.check(back, rom_name=ROM_PATH.name, rom_sha=back.rom_sha,
                        state_len=len(state)),
            [], "a movie of this cartridge must replay on this cartridge")

    def test_a_movie_of_another_cartridge_is_refused(self) -> None:
        rec = movie.Recorder({"rom_name": "other.ngc", "rom_sha": "deadbeefdeadbeef"})
        rec.record(0)
        fatal = [p for p in movie.check(rec.movie,
                                        rom_sha=movie.rom_fingerprint(self.rom))
                 if p.fatal]
        self.assertTrue(fatal)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(native.available(), "native core not built")
@unittest.skipUnless(ROM_PATH.exists() and BIOS_PATH.exists(), "no ROM/BIOS here")
class DebugWindowOnRealCore(unittest.TestCase):
    """The window itself, fed by the real core.

    Everything else here tests the modules; this tests the WIRING. A panel that
    reads real data of an unexpected shape -- a numpy dtype, an empty log, a table
    with thousands of rows -- fails here and nowhere else.
    """

    @classmethod
    def setUpClass(cls) -> None:
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PyQt6.QtWidgets import QApplication
        except ImportError:                     # pragma: no cover
            raise unittest.SkipTest("PyQt6 is not installed")
        cls.app = QApplication.instance() or QApplication([])
        cls.machine = native.NativeMachine(ROM_PATH.read_bytes(),
                                           bios=BIOS_PATH.read_bytes())
        cls.machine.reset(bios_handoff=True)
        for _ in range(60):
            cls.machine.run_frames(1)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.machine.close()

    def _window(self):
        import ngpc_debug
        import ngpc_settings as cfg

        machine = self.machine

        class _Play:
            """The slice of PlayPage the debug window reads, over a real core."""
            paused = False

            def __init__(self):
                self.machine = machine
                self.symbols = None
                self.breaks = type("B", (), {"items": []})()
                self.frame_hooks = []
                self.access_probe = None
                self.cheats = cheats.CheatSet()
                self.watches = None
                self._rom_path = ROM_PATH

            def apply_debug(self): pass
            def save_cheats(self): pass

        win = ngpc_debug.DebugWindow(None, cfg.make_settings())
        win.attach(_Play())
        return win

    def test_every_panel_refreshes_against_a_running_console(self) -> None:
        win = self._window()
        try:
            for index in range(win._tabs.count()):
                name = win._tabs.tabText(index)
                with self.subTest(panel=name):
                    win._tabs.setCurrentIndex(index)
                    win.refresh()
                    win.refresh()      # twice: the second pass hits the "changed
                                       # since last time" paths the first cannot
                    # ⚠️ `refresh` CATCHES panel errors on purpose -- a panel that
                    # throws must not kill the emulator. So "it did not raise" is
                    # no longer evidence of anything; the marker is.
                    self.assertIsNone(win.last_refresh_error,
                                      f"{name} failed: {win.last_refresh_error}")
        finally:
            win.close()

    def test_the_panels_that_read_the_machine_actually_show_something(self) -> None:
        """Refreshing without raising is not the same as producing an answer -- a
        panel that silently renders nothing passes the loop above."""
        win = self._window()
        try:
            names = [win._tabs.tabText(i) for i in range(win._tabs.count())]

            win._tabs.setCurrentIndex(names.index("HW Regs"))
            win.refresh()
            self.assertGreater(win._hw_table.rowCount(), 20)

            win._tabs.setCurrentIndex(names.index("Tilemap"))
            win.refresh()
            self.assertIsNotNone(win._tm_arr)
            self.assertIn("distinct tiles", win._tm_note.text())

            win._tabs.setCurrentIndex(names.index("Sound CPU"))
            win.refresh()
            self.assertIn("PC", win._z80_regs.toPlainText())
            self.assertTrue(win._z80_text.toPlainText().strip())

            win._tabs.setCurrentIndex(names.index("Profiler"))
            win._prof_count.setValue(5000)
            win._prof_capture()
            self.assertGreater(win._prof_table.rowCount(), 0)
            self.assertIn("instructions", win._prof_head.text())

            win._tabs.setCurrentIndex(names.index("Coverage"))
            win._cov_on.setChecked(True)
            self.machine.run_frames(2)
            win.refresh()
            self.assertIn("reached", win._cov_head.text())
        finally:
            win.close()
