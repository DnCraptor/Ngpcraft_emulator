"""The BIOS-fingerprint check some games run on char RAM.

Metal Slug 2nd Mission sweeps 0xA000..0xC000 for 64 bytes of the retail BIOS's
boot-time char RAM, of which it carries its own copy. Miss, and it silently wipes
its magic and zeroes the key configuration -- the game runs, looks perfect, and
shoot and jump are dead forever. Our clean-room HLE image cannot leave SNK's
glyphs there, so core.bios_fingerprint moves the bytes out of the cartridge the
player owns.

The synthetic ROM here is not Metal Slug: it is a ROM wearing that header, with a
made-up fingerprint at the offset the real game keeps its copy. That is enough to
pin the mechanism, and it keeps the suite runnable with no commercial ROM present.
"""

from __future__ import annotations

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

from core import bios_fingerprint as bf  # noqa: E402

MS2 = bf.FINGERPRINTS[0]


def _rom(title: bytes = MS2.title, size: int = 0x400000) -> bytearray:
    rom = bytearray(b"\x00" * size)
    rom[0:28] = b" LICENSED BY SNK CORPORATION"
    rom[0x24:0x34] = title
    # a distinctive stand-in for the 64 bytes the real game keeps here. Slice
    # assignment past the end of a bytearray GROWS it, which would quietly undo the
    # truncation the short-ROM test is built on -- so only stamp what fits.
    if size >= MS2.src + MS2.length:
        rom[MS2.src:MS2.src + MS2.length] = bytes(range(64))
    return rom


class _FakeCharRam:
    """Just enough machine to answer read/write over char RAM."""

    def __init__(self, fill: bytes = b"\x00"):
        self.mem = bytearray(fill * bf.CHAR_RAM_SIZE)

    def read(self, addr: int, count: int) -> bytes:
        off = addr - bf.CHAR_RAM_BASE
        return bytes(self.mem[off:off + count])

    def write(self, addr: int, data: bytes) -> None:
        off = addr - bf.CHAR_RAM_BASE
        self.mem[off:off + len(data)] = data


class Fingerprint(unittest.TestCase):
    def test_a_matching_game_gets_its_own_bytes_put_in_char_ram(self):
        rom, ram = _rom(), _FakeCharRam()
        hit = bf.restore(bytes(rom), ram.read, ram.write)
        self.assertIsNotNone(hit)
        want = bytes(rom[MS2.src:MS2.src + MS2.length])
        self.assertIn(want, bytes(ram.mem))
        self.assertEqual(ram.read(MS2.dest, MS2.length), want)

    def test_nothing_happens_when_the_check_is_already_satisfied(self):
        """This is the real-BIOS path: it left the data there itself. The whole
        no-regression argument for bios.bin rests on this staying true."""
        rom, ram = _rom(), _FakeCharRam()
        want = bytes(rom[MS2.src:MS2.src + MS2.length])
        ram.write(0x00B400, want)               # as if a BIOS had produced it
        before = bytes(ram.mem)
        self.assertIsNone(bf.restore(bytes(rom), ram.read, ram.write))
        self.assertEqual(bytes(ram.mem), before)

    def test_another_game_is_left_alone(self):
        rom, ram = _rom(title=b"SONICPOCKET\0\0\0\0\0"), _FakeCharRam()
        self.assertIsNone(bf.restore(bytes(rom), ram.read, ram.write))
        self.assertEqual(bytes(ram.mem), b"\x00" * bf.CHAR_RAM_SIZE)

    def test_a_truncated_rom_cannot_supply_the_bytes(self):
        """A 2 MiB dump wearing the header has nothing at 0x8DCC4 + 64 to copy --
        better to do nothing than to write whatever is at the end of the file."""
        rom, ram = _rom(size=MS2.src + 8), _FakeCharRam()
        self.assertIsNone(bf.restore(bytes(rom), ram.read, ram.write))

    def test_the_destination_lands_inside_the_scanned_window(self):
        for fp in bf.FINGERPRINTS:
            self.assertGreaterEqual(fp.dest, bf.CHAR_RAM_BASE)
            self.assertLessEqual(fp.dest + fp.length,
                                 bf.CHAR_RAM_BASE + bf.CHAR_RAM_SIZE)


@unittest.skipUnless((REPO / "hle_bios" / "bios_hle.bin").exists(),
                     "hle_bios/bios_hle.bin not built")
class WiredIntoReset(unittest.TestCase):
    """The hook has to be on the hand-off reset -- the path the emulator boots a
    game on -- and NOT on a real-BIOS power-up, where the BIOS produces the data
    itself and char RAM is legitimately blank at reset time."""

    def test_handoff_reset_applies_it(self):
        from core import native
        if not native.available():
            self.skipTest("native core not built")
        rom = bytes(_rom())
        bios = (REPO / "hle_bios" / "bios_hle.bin").read_bytes()
        m = native.NativeMachine(rom, bios=bios)
        m.reset()
        self.assertIn(bytes(range(64)), m.read(bf.CHAR_RAM_BASE, bf.CHAR_RAM_SIZE))

    def test_raw_reset_does_not(self):
        from core import native
        if not native.available():
            self.skipTest("native core not built")
        rom = bytes(_rom())
        m = native.NativeMachine(rom, bios=(REPO / "hle_bios" / "bios_hle.bin").read_bytes())
        m.reset(bios_handoff=False)
        self.assertNotIn(bytes(range(64)), m.read(bf.CHAR_RAM_BASE, bf.CHAR_RAM_SIZE))


if __name__ == "__main__":
    unittest.main()
