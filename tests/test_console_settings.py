"""THE THREE THINGS THE CONSOLE TELLS THE CARTRIDGE: language, clock, which machine.

None of these are the game's own state. They are the console's, the BIOS hands them
over at boot, and on hardware the setup wizard and the coin cell decide them. We skip
that wizard -- so if we do not hand them over, they are zero, and zero is a choice
nobody made.

The language one had exactly that shape: 0x6F87 sat at 0, and 0 is Japanese, so every
bilingual cartridge ran in Japanese by accident.
"""

from __future__ import annotations

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HLE_IMAGE = REPO / "hle_bios" / "bios_hle.bin"
REAL_BIOS = REPO / "bios.bin"
PROBE_ROM = REPO / "tests" / "roms" / "link_probe.ngc"   # a ROM that really executes

from core import native  # noqa: E402

SYS_LANGUAGE = 0x006F87
OS_VERSION = 0x006F91


def _rom(size: int = 0x100000, *, mono: bool = False) -> bytes:
    rom = bytearray(b"\xFF" * size)
    rom[0:28] = b" LICENSED BY SNK CORPORATION"
    rom[0x1C:0x20] = (0x200040).to_bytes(4, "little")
    rom[0x23] = 0x00 if mono else 0x10
    rom[0x40] = 0x05
    return bytes(rom)


def _images():
    out = [("HLE", HLE_IMAGE)] if HLE_IMAGE.is_file() else []
    if REAL_BIOS.is_file():
        out.append(("real", REAL_BIOS))
    return out


@unittest.skipUnless(native.available(), "native core not built")
@unittest.skipUnless(_images(), "no BIOS image to test against")
class ConsoleLanguage(unittest.TestCase):
    """`Language` at 0x6F87 (SDK SysWork.txt): 0 = Japanese, 1 = English, read-only to
    the cartridge. 24 games of the corpus read it, and a bilingual cart has nothing
    else to go on -- four of the six (En,Ja) titles render visibly differently with it
    flipped (Baseball Stars, Puyo Pop, Neo Geo Cup '98)."""

    def test_the_choice_reaches_the_cartridge_on_either_bios(self):
        for tag, path in _images():
            for code in (native.NativeMachine.LANGUAGE_JAPANESE,
                         native.NativeMachine.LANGUAGE_ENGLISH):
                with self.subTest(bios=tag, code=code):
                    with native.NativeMachine(_rom(), bios=path.read_bytes()) as m:
                        m.set_language(code)
                        m.reset(bios_handoff=True)
                        self.assertEqual(m.read(SYS_LANGUAGE, 1)[0], code)

    def test_english_is_the_default(self):
        """Not a preference: the alternative is the zero RAM powers up with, which
        silently means Japanese. A default nobody chose is worse than a wrong one."""
        import ngpc_settings as cfg
        self.assertEqual(cfg.CART_LANG_EN, 1)
        with native.NativeMachine(_rom(), bios=_images()[0][1].read_bytes()) as m:
            m.reset(bios_handoff=True)
            self.assertEqual(m.read(SYS_LANGUAGE, 1)[0], 1)


@unittest.skipUnless(native.available(), "native core not built")
@unittest.skipUnless(_images() and PROBE_ROM.is_file(), "needs a BIOS and the probe ROM")
class RealTimeClock(unittest.TestCase):
    """⚠️ A CLOCK ONLY TICKS WHILE THE MACHINE RUNS. Testing this against a synthetic
    ROM that halts at its first instruction reports a dead clock -- 300 frames retired
    33 instructions and 433 cycles, and the "stopped RTC" that looked like was the
    harness. Use a ROM that actually executes."""

    def test_the_clock_advances_with_emulated_time(self):
        for tag, path in _images():
            with self.subTest(bios=tag):
                with native.NativeMachine(PROBE_ROM.read_bytes(), bios=path.read_bytes()) as m:
                    m.reset(bios_handoff=True)
                    before = m.rtc()
                    summary = m.run_frames(300)                 # five seconds
                    after = m.rtc()
                    self.assertGreater(summary.total_cycles, 20_000_000,
                                       "the machine barely ran; the clock is not what is being tested")
                    self.assertNotEqual((after.hour, after.minute, after.second),
                                        (before.hour, before.minute, before.second),
                                        "five seconds of emulated time moved no clock")

    def test_the_seconds_carry_the_way_bcd_does(self):
        with native.NativeMachine(PROBE_ROM.read_bytes(),
                                  bios=_images()[0][1].read_bytes()) as m:
            m.reset(bios_handoff=True)
            st = m.rtc()
            st.second, st.minute = 0x58, 0x00
            m.set_rtc(st)
            m.run_frames(300)
            after = m.rtc()
            self.assertEqual(after.minute, 0x01, "0x59 + 1 must carry into the minute")
            self.assertLessEqual(after.second, 0x59, "seconds must stay packed BCD")


@unittest.skipUnless(native.available(), "native core not built")
@unittest.skipUnless(_images(), "no BIOS image to test against")
class MonochromeConsoleMode(unittest.TestCase):
    """Which machine a black-and-white cartridge thinks it is in. `OS_Version` (0x6F91)
    is what it asks: 0x10 says "colour console", so a colour-aware mono game -- Samurai
    Shodown is the one -- runs its colourisation path. On an original NGP it reads the
    cart's own header byte instead and that path never runs."""

    def test_the_console_identity_byte_follows_the_setting(self):
        for tag, path in _images():
            for k1ge, expect in ((False, 0x10), (True, 0x00)):
                with self.subTest(bios=tag, k1ge=k1ge):
                    with native.NativeMachine(_rom(mono=True), bios=path.read_bytes()) as m:
                        m.set_k1ge_console(k1ge)
                        m.reset(bios_handoff=True)
                        self.assertEqual(m.read(OS_VERSION, 1)[0], expect)

    def test_a_colour_cartridge_is_told_which_console_it_is_actually_in(self):
        """This asserted the OPPOSITE until a user reported the bug: that a colour
        cartridge always reads 0x10 because its own header says so.

        0x6F91 belongs to the CONSOLE, not to the cartridge. Disassembled, each BIOS
        stamps its own machine's id there while booting -- the NGPC's runs
        `ld (0x6F91), 0x10`, the mono NGP's runs `ld (0x6F91), 0x00` -- and neither
        one consults the cartridge header to do it. Seeding the byte from the header
        meant a colour game in our "mono NGP" was told it was in an NGPC and behaved
        exactly as it does in one: SNK vs. Capcom, which shows a different screen on
        mono hardware, showed the colour one."""
        for k1ge, expect in ((False, 0x10), (True, 0x00)):
            with self.subTest(k1ge=k1ge):
                with native.NativeMachine(_rom(mono=False),
                                          bios=_images()[0][1].read_bytes()) as m:
                    m.set_k1ge_console(k1ge)
                    m.reset(bios_handoff=True)
                    self.assertEqual(m.read(OS_VERSION, 1)[0], expect)



@unittest.skipUnless(native.available(), "native core not built")
@unittest.skipUnless(_images(), "no BIOS image to test against")
class ConsoleLanguageOwnership(unittest.TestCase):
    """WHICH boot mode the emulator's language setting speaks for.

    The report that started this: "set the BIOS to English and the game still launches
    in Japanese; skip the BIOS and it starts in English". The stamp lived inside the
    hand-off branch of the reset, so it was true -- but the fix is not to stamp in both
    modes. A console boot runs the real BIOS, which HAS a setup screen; that screen is
    the console's control panel and its choice is kept in the coin cell. The setting is
    the stand-in for consoles that have no such screen (our HLE image) or have never
    been configured -- see LegacyBiosOwnsItsOwnSettings for the other half."""

    def test_the_hand_off_takes_the_setting(self):
        """No BIOS screen ran, so nothing else can answer -- and the power-on value is
        0, which is Japanese by accident rather than by anyone's choice."""
        for tag, path in _images():
            for code in (0, 1):
                with self.subTest(bios=tag, language=code):
                    with native.NativeMachine(_rom(), bios=path.read_bytes()) as m:
                        m.set_language(code)
                        m.reset(bios_handoff=True)
                        self.assertEqual(m.read(SYS_LANGUAGE, 1)[0], code)

    def test_the_console_boot_leaves_the_byte_to_the_bios(self):
        """Stamping here would overrule the BIOS's own setup screen: the player would
        set the language on it and watch the emulator undo the choice at the next
        launch. The reset writes nothing; the coin cell that was handed over answers."""
        for tag, path in _images():
            with self.subTest(bios=tag):
                with native.NativeMachine(_rom(), bios=path.read_bytes()) as m:
                    m.set_language(1)
                    m.reset(real_bios=True)
                    self.assertEqual(m.read(SYS_LANGUAGE, 1)[0], 0,
                                     "the setting must not be stamped over a real boot")

    def test_a_saved_coin_cell_does_not_outrank_the_setting(self):
        """The second half of the same report. The console's settings page is restored
        from the coin cell after the hand-off -- and it used to bring the language back
        with it, on top of the value the reset had just stamped. Whichever language the
        console was first configured in then stuck forever, whatever the user chose."""
        from core import native_session as ns

        self.assertIn((0x006F87, 0x006F88), ns.NativeSession._SETTINGS_SKIP,
                      "the language must be excluded from the coin-cell restore")

if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(native.available() and REAL_BIOS.is_file(), "needs a real BIOS image")
class LegacyBiosOwnsItsOwnSettings(unittest.TestCase):
    """WHO CONFIGURES THE CONSOLE. A real BIOS has a setup screen; that screen is the
    console's control panel, and what the player sets on it belongs in the coin cell.
    The emulator's own settings exist because the HLE image has no such screen -- they
    are a stand-in, not an override, and treating them as an override made the BIOS
    screen a decoration: a choice made there was undone at the next launch."""

    def setUp(self):
        import tempfile
        from core import native_session as ns
        self.ns = ns
        self.tmp = Path(tempfile.mkdtemp())
        self._saved = (ns.SYSTEM_RAM_PATH, ns.SYSTEM_RTC_PATH)
        ns.SYSTEM_RAM_PATH = self.tmp / "system.ram"
        ns.SYSTEM_RTC_PATH = self.tmp / "system.rtc"
        self.rom = self.tmp / "cart.ngc"
        self.rom.write_bytes(_rom())

    def tearDown(self):
        self.ns.SYSTEM_RAM_PATH, self.ns.SYSTEM_RTC_PATH = self._saved

    def _session(self, **kw):
        return self.ns.NativeSession(self.rom, bios_path=REAL_BIOS, save_to_rom=False, **kw)

    def test_what_the_bios_screen_sets_is_saved_and_then_obeyed(self):
        # An unconfigured console has no control panel but ours.
        with self._session(autosave=False, real_bios=False, language=1) as s:
            self.assertEqual(s.machine.read(SYS_LANGUAGE, 1)[0], 1)

        # The BIOS setup screen picks English; switching off must remember it. (Driving
        # the screen itself needs input; writing the byte it writes is the same event.)
        s = self._session(autosave=True, real_bios=True, language=0)
        s.machine.run_frames(600)
        s.machine.write(SYS_LANGUAGE, b"\x01")
        s.close()
        cell = self.ns.SYSTEM_RAM_PATH.read_bytes()
        self.assertEqual(cell[SYS_LANGUAGE - native.RAM_START], 1,
                         "the BIOS screen's choice must reach the coin cell")

        # ...and from now on it outranks the emulator's setting, in BOTH boot modes.
        for real in (True, False):
            with self.subTest(real_bios=real):
                with self._session(autosave=False, real_bios=real, language=0) as s:
                    s.machine.run_frames(600)
                    self.assertEqual(s.machine.read(SYS_LANGUAGE, 1)[0], 1)

    def test_the_hle_image_has_no_screen_so_the_setting_answers(self):
        """Our clean-room BIOS cannot ask the player anything yet, so there the UI is
        the console's only control panel -- even with a cell configured otherwise."""
        s = self._session(autosave=True, real_bios=True, language=0)
        s.machine.run_frames(600)
        s.machine.write(SYS_LANGUAGE, b"\x01")           # cell says English
        s.close()
        with self.ns.NativeSession(self.rom, bios_path=HLE_IMAGE, save_to_rom=False,
                                   autosave=False, language=0, hle_bios=True) as s:
            self.assertEqual(s.machine.read(SYS_LANGUAGE, 1)[0], 0)


@unittest.skipUnless(native.available(), "native core not built")
class ManualClock(unittest.TestCase):
    """Setting the console's clock BY HAND.

    On hardware the BIOS setup screen is where the date is set, and the clean-room HLE
    image has no such screen — so with it there was no way to choose a date at all,
    only to follow the PC's. This is that screen's replacement, and it works with either
    BIOS: nothing about it is HLE-specific."""

    WHEN = "1999-07-14T08:30:00"

    def _rtc(self, mode, manual=None, bios=None):
        from core import native_session as ns
        m = native.NativeMachine(_rom(), bios=(bios.read_bytes() if bios else None))
        try:
            m.reset(bios_handoff=True)
            ns.apply_saved_clock(m, Path("no-such-file.rtc"), mode, manual)
            return m.rtc()
        finally:
            m.close()

    def test_the_clock_starts_where_you_set_it(self):
        from core import native_session as ns
        for tag, path in _images():
            with self.subTest(bios=tag):
                st = self._rtc(ns.CLOCK_MANUAL, self.WHEN, path)
                # packed BCD, exactly as the chip's registers hold it
                self.assertEqual((st.year, st.month, st.day), (0x99, 0x07, 0x14))
                self.assertEqual((st.hour, st.minute, st.second), (0x08, 0x30, 0x00))
                self.assertTrue(st.enable)

    def test_a_value_it_cannot_read_leaves_the_clock_alone(self):
        """Better a clock nobody set than a date nobody chose."""
        from core import native_session as ns
        for bad in ("", "not a date", "1999-13-45T99:99", None):
            with self.subTest(value=bad):
                self.assertIsNone(ns.manual_clock_state(bad))

    def test_the_mode_is_offered_and_stored(self):
        import ngpc_settings as cfg
        from core import native_session as ns
        self.assertIn(ns.CLOCK_MANUAL, ns.CLOCK_MODES)
        s = cfg.make_settings()
        s.setValue("bios/clock_mode", ns.CLOCK_MANUAL)
        self.assertEqual(cfg.clock_mode(s), ns.CLOCK_MANUAL)
        s.setValue("bios/clock_manual", self.WHEN)
        self.assertEqual(cfg.clock_manual(s), self.WHEN)
        s.remove("bios/clock_mode"); s.remove("bios/clock_manual")
