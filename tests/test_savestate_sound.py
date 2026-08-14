"""A savestate must restore the SOUND, and "CPU + memory" does not.

⛔ THE BUG THIS CONDEMNS. Save, load, and the music is gone -- reported on SNK vs.
Capcom: Card Fighters, seen on several games, and it came back only when the game
happened to change scene. The shell's snapshot was the main CPU struct plus the flat
memory image, and a comment claimed the rest "re-syncs on the next VBlank". It does
not. The sound driver runs on a SECOND CPU: its RAM is inside the image (0x7000) but
its PC, its pointers and the sound chip's own registers are not. A load therefore
dropped a Z80 that was halfway through one piece of music on top of a driver state
from somewhere else, and the main CPU -- believing it had already asked for that
music -- never asked again. Until the game changed scene and issued a fresh command.
That is the whole report, symptom and escape hatch both.

There is a second, sharper edge, and it is why the ORDER of a restore matters:
writing the memory image back goes through the control registers, and 0x00BA is a
DOOR ("fire one NMI at the sound CPU"), not a byte of storage. Restoring memory
alone therefore also rings the driver's doorbell, mid-routine.

WHAT IS ASSERTED, and each one fails without cpp/src/core.cpp's aux state:
  * a saved-then-restored machine has the sound CPU and the chip it was saved with;
  * the AUDIO ITSELF continues identically -- the strong gate, because it is the
    output, not the bookkeeping;
  * the same restore WITHOUT the sound block diverges (the control group: without
    it, this file would be measuring nothing);
  * a restore leaves no phantom NMI behind.
"""

from __future__ import annotations

import collections
import ctypes
import tempfile
import unittest
from pathlib import Path

from core import native

Z80_RESET_REG = 0x0000B9      # HIGH byte of the 16-bit register: 0x55 = Z80 RUN
Z80_SHARED_RAM = 0x007000
Z80_RELEASE = 0x55
STATE_MEM_LEN = 0x00C000      # what ngpc_shell.py snapshots: I/O + RAM + VRAM

# A sound driver, in Z80. It writes a walking byte at the T6W28's right-hand port
# (0x4000) forever, so both the driver's own registers AND the chip's move on every
# pass -- which is exactly the state a snapshot has to carry.
#
#   DI ; LD SP,0x0FF0 ; LD B,0 ; LD HL,0x4000
#   loop: LD A,B ; LD (HL),A ; INC B ; JP loop
DRIVER = bytes([
    0xF3,                    # 0x0000  DI
    0x31, 0xF0, 0x0F,        # 0x0001  LD SP,0x0FF0
    0x06, 0x00,              # 0x0004  LD B,0
    0x21, 0x00, 0x40,        # 0x0006  LD HL,0x4000
    0x78,                    # 0x0009  LD A,B      <- loop
    0x77,                    # 0x000A  LD (HL),A
    0x04,                    # 0x000B  INC B
    0xC3, 0x09, 0x00,        # 0x000C  JP 0x0009
])


def _write_demo_rom(path: Path, entry: int, body: bytes) -> None:
    header = bytearray(0x30)
    header[0x00:0x1C] = b"COPYRIGHT BY SNK CORPORATION"[:0x1C].ljust(0x1C, b" ")
    header[0x1C:0x20] = entry.to_bytes(4, "little")
    header[0x23] = 0x10
    header[0x24:0x30] = b"SNDSTATE    "[:0x0C].ljust(0x0C, b"\x00")
    rom = bytearray(header)
    rom.extend(b"\x00" * (entry - 0x200000 - len(rom)))
    rom.extend(body)
    rom.extend(b"\x00" * 64)
    path.write_bytes(bytes(rom))


@unittest.skipUnless(native.available(), "native core not built (cmake --build cpp/build)")
class SavestateSoundTests(unittest.TestCase):
    """The snapshot pair, byte for byte what ngpc_shell.py does."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        rom = Path(self._tmp.name) / "snd.ngc"
        # Main CPU: `nop ; jr -2`. It only has to burn cycles; the sound CPU is the
        # subject here, and on the console it would be the driver the game uploaded.
        _write_demo_rom(rom, 0x00200040, b"\x00\x68\xFD")
        self.m = native.NativeMachine(rom.read_bytes())
        self.m.reset(bios_handoff=True)
        self.m.write(Z80_SHARED_RAM, DRIVER)
        self.m.write(Z80_RESET_REG, bytes([Z80_RELEASE]))
        self.m.run_frames(30)

    def tearDown(self) -> None:
        self.m.close()
        self._tmp.cleanup()

    # --- the two halves of a snapshot, exactly as the shell writes them ----------
    def _capture(self) -> bytes:
        return (bytes(self.m.cpu())
                + bytes(self.m.aux_state())
                + self.m.read(0, STATE_MEM_LEN))

    def _apply(self, body: bytes, aux: bool = True) -> None:
        cpu_len = ctypes.sizeof(type(self.m.cpu()))
        aux_len = ctypes.sizeof(native.AuxState)
        cpu = type(self.m.cpu()).from_buffer_copy(body[:cpu_len])
        self.m.write(0, body[cpu_len + aux_len:cpu_len + aux_len + STATE_MEM_LEN])
        self.m.set_cpu(cpu)
        if aux:   # AFTER the image -- see the note in core/native.py
            self.m.set_aux_state(
                native.AuxState.from_buffer_copy(body[cpu_len:cpu_len + aux_len]))

    def _drain(self) -> bytes:
        # `audio()` hands over at most 8192 stereo frames a call, and twenty emulated
        # frames are ~14 700 of them. Draining ONCE leaves a tail behind, and that tail
        # shifts the next measurement by exactly the samples it kept -- a comparison
        # that fails on an offset rather than on the content. Drain until it is empty.
        out = bytearray()
        while True:
            chunk = self.m.audio()
            if not chunk:
                return bytes(out)
            out += chunk

    def _audio_after(self, frames: int) -> bytes:
        self._drain()                     # drop whatever the ring already held
        self.m.run_frames(frames)
        return self._drain()

    # --- the driver has to be alive, or every assertion below is vacuous ---------
    def test_the_harness_actually_makes_sound(self) -> None:
        z = self.m.aux_state()
        self.assertTrue(z.z80_running)
        self.assertFalse(z.z80_trapped)
        self.assertGreater(z.z80_executed, 1000)
        audio = self._audio_after(10)
        self.assertGreater(len(audio), 0)
        self.assertTrue(any(audio), "the chip produced pure silence: nothing to lose")

    def test_a_restore_puts_the_sound_cpu_and_the_chip_back(self) -> None:
        saved = self._capture()
        before = self.m.aux_state()
        self.m.run_frames(20)             # let the driver and the chip move on
        # NOT the PC: a four-instruction loop lands back on the same address all the
        # time, so "the PC differs" would be a coin toss. The instruction COUNT and the
        # driver's own counter cannot go backwards.
        self.assertGreater(self.m.aux_state().z80_executed, before.z80_executed)

        self._apply(saved)
        after = self.m.aux_state()
        self.assertEqual(after.z80_pc, before.z80_pc)
        self.assertEqual(after.z80_b, before.z80_b)
        self.assertEqual(after.z80_sp, before.z80_sp)
        self.assertEqual(after.z80_executed, before.z80_executed)
        self.assertEqual(list(after.square_period), list(before.square_period))
        self.assertEqual(after.latch_right, before.latch_right)
        self.assertEqual(list(after.timer_count), list(before.timer_count))

    def test_the_audio_continues_where_it_was_saved(self) -> None:
        # THE STRONG GATE: the output, not the bookkeeping. Play the same twenty
        # frames twice -- once live, once from the restored state. Identical bytes
        # mean the sound genuinely resumed rather than merely not crashing.
        saved = self._capture()
        live = self._audio_after(20)
        self.assertTrue(any(live))

        self._apply(saved)
        replayed = self._audio_after(20)
        self.assertEqual(live, replayed)

    def test_without_the_sound_block_it_diverges(self) -> None:
        # THE CONTROL GROUP. This is the old snapshot -- CPU + memory -- and it must
        # NOT reproduce the audio, or the test above would pass for free and prove
        # nothing about the fix.
        saved = self._capture()
        live = self._audio_after(20)

        self._apply(saved, aux=False)
        self.assertNotEqual(live, self._audio_after(20))

    def test_a_restore_leaves_no_phantom_nmi(self) -> None:
        # 0x00BA is a door, not storage: writing the image back rings the driver's
        # doorbell. Restoring the sound CPU's own state afterwards is what cancels it.
        saved = self._capture()
        self.m.run_frames(5)

        self._apply(saved, aux=False)
        self.assertTrue(self.m.aux_state().z80_nmi_pending,
                        "the memory image no longer forges an NMI -- retire this test")

        self._apply(saved)
        self.assertFalse(self.m.aux_state().z80_nmi_pending)

    def test_a_blob_from_another_build_is_refused_not_half_applied(self) -> None:
        st = self.m.aux_state()
        st.version = 0xDEAD
        self.assertFalse(self.m.set_aux_state(st))

    def test_the_two_sides_agree_on_the_layout(self) -> None:
        # ctypes and the C struct must be the same size, or every field past the first
        # mismatch is read as its neighbour -- silently. The core writes its own
        # sizeof() into the blob; compare it with ours.
        self.assertEqual(self.m.aux_state().size, ctypes.sizeof(native.AuxState))


class ShellSavestateReaderTests(unittest.TestCase):
    """The OTHER readers of the player's file, and the shift the new block can cause.

    `core.savestate.load_shell_savestate` and `ngpc_native.load_state` both parse the
    file the shell writes. The sound block sits between the CPU struct and the image,
    so a reader that does not know about it reads the image one struct too early and
    hands every downstream tool a memory map shifted by a few hundred bytes -- wrong,
    plausible, and silent. Every generation is checked here, with a byte planted at a
    known address as the witness. No DLL needed: the ctypes structs are declarations.

    ⛔ AND THIS CLASS NAMED A READER IT NEVER RAN. `ngpc_native.load_state` was in the
    paragraph above from the day it was written, and only `load_shell_savestate` was
    ever called -- so when v3 shipped in the shell and that reader was not updated, the
    file it exists to protect stayed green while `--state` REFUSED every state a player
    produces. A second reader is only covered if the test actually opens it, so both go
    through `_check` now, and adding a generation means adding it to `MAGICS`.
    """

    MARKER_ADDR = 0x004242
    MARKER = 0x5A
    # Newest first. `ngpc_shell.STATE_MAGIC` must be the head of this list.
    MAGICS = (b"NGPCST03", b"NGPCST02", b"NGPCST01")

    def _blob(self, magic: bytes) -> bytes:
        from core.native import AuxState, CpuState, LinkState

        cpu = CpuState()
        cpu.pc = 0x00201234
        body = bytes(cpu)
        if magic in (b"NGPCST03", b"NGPCST02"):
            aux = AuxState()
            aux.version = native.AUX_STATE_VERSION
            aux.size = ctypes.sizeof(AuxState)
            body += bytes(aux)
        if magic == b"NGPCST03":
            link = LinkState()
            link.version = native.LINK_STATE_VERSION
            link.size = ctypes.sizeof(LinkState)
            body += bytes(link)
        mem = bytearray(STATE_MEM_LEN)
        mem[self.MARKER_ADDR] = self.MARKER
        return magic + body + bytes(mem)

    def _check(self, magic: bytes) -> None:
        from core import savestate

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.bin"
            path.write_bytes(self._blob(magic))

            doc = savestate.load_shell_savestate(path)
            self.assertEqual(doc.cpu.pc, 0x00201234, f"{magic.decode()}: CPU misread")
            self.assertEqual(doc.writable_overlay.get(self.MARKER_ADDR), self.MARKER,
                             f"{magic.decode()}: the image was read at the wrong offset")

            self._check_headless_reader(magic, path)

    def _check_headless_reader(self, magic: bytes, path: Path) -> None:
        """`ngpc_native.load_state` on the same file, with a recording stand-in for the
        machine -- it only has to prove it ACCEPTS the generation and lands the image at
        the right offset, which is precisely what a missed magic breaks."""
        import ngpc_native
        from core.native import CpuState

        written: dict[int, bytes] = {}

        class Recorder:
            def cpu(self):
                return CpuState()

            def write(self, addr, data):
                written[addr] = bytes(data)

            def set_cpu(self, cpu):
                self.restored_pc = cpu.pc

            def set_aux_state(self, st):
                pass

            def set_link_state(self, st):
                pass

        rec = Recorder()
        try:
            ngpc_native.load_state(rec, path)
        except SystemExit as e:                       # pragma: no cover -- the failure
            self.fail(f"ngpc_native.load_state refused {magic.decode()}: {e}")
        self.assertEqual(rec.restored_pc, 0x00201234,
                         f"{magic.decode()}: headless reader misread the CPU")
        image = written.get(0)
        self.assertIsNotNone(image, f"{magic.decode()}: headless reader wrote no image")
        self.assertEqual(len(image), STATE_MEM_LEN,
                         f"{magic.decode()}: headless reader wrote a short image")
        self.assertEqual(image[self.MARKER_ADDR], self.MARKER,
                         f"{magic.decode()}: headless reader read the image at the "
                         f"wrong offset -- it does not know about a block")

    def test_it_reads_a_v3_state_at_the_right_offset(self) -> None:
        self._check(b"NGPCST03")

    def test_it_reads_a_v2_state_at_the_right_offset(self) -> None:
        self._check(b"NGPCST02")

    def test_it_still_reads_a_v1_state(self) -> None:
        self._check(b"NGPCST01")

    def test_the_shells_own_magic_is_one_the_readers_know(self) -> None:
        """The drift alarm. The shell decides the format; a generation it starts writing
        and the readers do not know is a door closing on every downstream tool."""
        import ngpc_native
        import ngpc_shell

        self.assertEqual(ngpc_shell.STATE_MAGIC, self.MAGICS[0],
                         "the shell writes a generation this file does not cover")
        self.assertIn(ngpc_shell.STATE_MAGIC, ngpc_native.SHELL_MAGICS,
                      "ngpc_native.load_state cannot read what the shell now writes")
        from core import savestate
        self.assertIn(ngpc_shell.STATE_MAGIC, savestate.SHELL_SAVESTATE_MAGICS,
                      "core.savestate cannot read what the shell now writes")

    def test_an_unknown_magic_is_refused(self) -> None:
        from core import savestate

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.bin"
            path.write_bytes(b"NGPCST99" + bytes(STATE_MEM_LEN))
            with self.assertRaises(ValueError):
                savestate.load_shell_savestate(path)


class ShellSaveLoadPathTests(unittest.TestCase):
    """The SHIPPED path: `ngpc_shell.PlayPage.save_state` / `load_state`, on disk.

    The tests above prove the core can carry the sound across a snapshot; this one
    proves the buttons the player presses actually do it. The methods are called
    unbound on a duck-typed stand-in, so it is the shell's own code that runs -- a
    copy of it here would pass while the app stayed broken.
    """

    def setUp(self) -> None:
        import pytest
        pytest.importorskip("PyQt6")
        if not native.available():
            self.skipTest("native core not built")

        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        import ngpc_settings as cfg
        import ngpc_shell

        self.shell = ngpc_shell
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        rom = tmp / "snd.ngc"
        _write_demo_rom(rom, 0x00200040, b"\x00\x68\xFD")

        self._old_dir = ngpc_shell.STATE_DIR
        ngpc_shell.STATE_DIR = tmp / "savestates"     # never the user's own slots

        m = native.NativeMachine(rom.read_bytes())
        m.reset(bios_handoff=True)
        m.write(Z80_SHARED_RAM, DRIVER)
        m.write(Z80_RESET_REG, bytes([Z80_RELEASE]))
        m.run_frames(30)
        self.m = m

        P = ngpc_shell.PlayPage

        class _StubBar:
            visible = True

            def setVisible(self, on): self.visible = bool(on)

        class _Page:      # the slice of PlayPage these methods touch -- the methods
            machine = m   # themselves are the SHELL'S OWN, borrowed, not re-written
            _rom_path = rom
            _slot = 0
            _settings = cfg.make_settings()
            _state_path = P._state_path
            _capture_state = P._capture_state
            _apply_state = P._apply_state
            save_state = P.save_state
            load_state = P.load_state

            # ⚡ This double borrows the REAL save_state/load_state, so it has to carry
            # every attribute they read. `_mirror` is one of them now: mirror netplay
            # refuses a savestate, because restoring THIS console alone would put the
            # two PCs in different states (see PlayPage._mirror_blocks). None = not in a
            # mirror session, which is what this test is.
            _mirror = None
            _mirror_blocks = P._mirror_blocks

            def __init__(self):
                # ...and `_prerun` likewise: loading a state drops the frames a link
                # peer had run ahead for this console, because they describe the
                # timeline being overwritten (see PlayPage._run_frame_interleaved).
                self._prerun = collections.deque()
                self._rewind = []
                self._rw_pos = None
                self.messages = []
                # ...and the rewind strip: loading a state starts a new timeline,
                # so the strip drawing the old one is put away. A stub, because
                # this test is about the SAVESTATE -- but it has to be here, or
                # the borrowed method reads an attribute that is not there.
                self.rewind_bar = _StubBar()

            def _flash(self, msg): self.messages.append(msg)
            def _blit(self): pass

        self.page = _Page()

    def tearDown(self) -> None:
        self.shell.STATE_DIR = self._old_dir
        self.m.close()
        self._tmp.cleanup()

    def test_a_saved_slot_carries_the_sound_and_loads_it_back(self) -> None:
        P = self.shell.PlayPage
        before = self.m.aux_state()
        P.save_state(self.page)
        path = P._state_path(self.page, 0)
        self.assertTrue(path.is_file())
        self.assertTrue(path.read_bytes().startswith(self.shell.STATE_MAGIC))

        self.m.run_frames(20)
        self.assertGreater(self.m.aux_state().z80_executed, before.z80_executed)

        P.load_state(self.page)
        after = self.m.aux_state()
        self.assertEqual(after.z80_pc, before.z80_pc)
        self.assertEqual(after.z80_executed, before.z80_executed)
        self.assertFalse(after.z80_nmi_pending)

    def test_a_v1_slot_still_loads(self) -> None:
        # The player already has NGPCST01 files on disk. They must not become garbage
        # just because the format grew -- they simply load without the sound block.
        P = self.shell.PlayPage
        P.save_state(self.page)
        path = P._state_path(self.page, 0)
        body = path.read_bytes()[len(self.shell.STATE_MAGIC):]
        aux_len = ctypes.sizeof(native.AuxState)
        cpu_len = ctypes.sizeof(type(self.m.cpu()))
        path.write_bytes(self.shell.STATE_MAGIC_V1
                         + body[:cpu_len] + body[cpu_len + aux_len:])

        self.m.run_frames(20)
        P.load_state(self.page)
        self.assertNotIn("bad state", self.page.messages)


if __name__ == "__main__":
    unittest.main()
