"""Structural + boot regression tests for the clean-room HLE BIOS image.

These guard the contract the emulator core relies on when no real bios.bin is
present (see hle_bios/). They do NOT require the Toshiba toolchain: they read
the committed hle_bios/bios_hle.bin and exercise it through the native core.
If the image is missing (a checkout that never built it) the tests skip.
"""
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
IMAGE = REPO / "hle_bios" / "bios_hle.bin"

try:
    from core.native import NativeMachine, NativeCoreUnavailable
    _NATIVE = True
except Exception:
    _NATIVE = False


def _u32(b, o):
    return b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)


@unittest.skipUnless(IMAGE.exists(), "hle_bios/bios_hle.bin not built")
class HleBiosImageStructure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.img = IMAGE.read_bytes()

    def test_image_is_exactly_64_kib(self):
        self.assertEqual(len(self.img), 0x10000, "ngpc_load_bios requires exactly 65536 bytes")

    def test_reset_and_swi_vectors_point_into_bios(self):
        # 0xFFFF00 = reset / swi0, 0xFFFF04 = swi1, 0xFFFF2C = VBlank (idx 11).
        for name, addr in (("reset", 0xFFFF00), ("swi1", 0xFFFF04), ("vblank", 0xFFFF2C)):
            tgt = _u32(self.img, addr - 0xFF0000)
            self.assertGreaterEqual(tgt & 0xFFFFFF, 0xFF0000, f"{name} vector must target BIOS ROM")

    def test_user_vector_table_anchor_present(self):
        # seed_user_vector_table() scans the image for `ld XIY,imm32`(0x45) then
        # `ld XIX,0x00006FB8`(44 B8 6F 00 00); the imm32 stub must be in BIOS ROM.
        anchor = bytes([0x44, 0xB8, 0x6F, 0x00, 0x00])
        i = self.img.find(anchor)
        self.assertGreaterEqual(i, 5, "0x6FB8 anchor `ld XIX,0x6FB8` not found")
        self.assertEqual(self.img[i - 5], 0x45, "anchor must be preceded by `ld XIY,imm32` (0x45)")
        stub = _u32(self.img, i - 4)
        self.assertGreaterEqual(stub & 0xFFFFFF, 0xFF0000, "default user-vector stub must be in BIOS ROM")

    def test_syscall_table_entries_point_into_bios(self):
        # 0xFFFE00 + idx*4, indices 0..0x1A are the documented BIOS system calls.
        for idx in range(0x1B):
            tgt = _u32(self.img, (0xFFFE00 - 0xFF0000) + idx * 4)
            self.assertGreaterEqual(tgt & 0xFFFFFF, 0xFF0000, f"syscall {idx:#x} stub must be in BIOS ROM")


@unittest.skipUnless(_NATIVE and IMAGE.exists(), "native core or image unavailable")
class HleBiosBoots(unittest.TestCase):
    """A minimal cartridge must boot through the HLE image, deterministically,
    with the user vector table seeded from the image's anchor."""

    @classmethod
    def setUpClass(cls):
        cls.bios = IMAGE.read_bytes()
        # smallest valid cart: header + an infinite loop at the entry point.
        rom = bytearray(0x8000)
        entry = 0x200040
        rom[0x1C:0x20] = entry.to_bytes(4, "little")
        rom[0x23] = 0x10  # colour
        rom[0x40:0x42] = b"\x18\xFE"  # jr $ (tight loop) at the entry
        cls.rom = bytes(rom)

    def _run(self):
        m = NativeMachine(self.rom, bios=self.bios)
        m.reset(bios_handoff=True, real_bios=False)
        m.run_frames(4)
        return m

    def test_user_vector_table_is_seeded_from_the_image(self):
        m = self._run()
        # every 0x6FB8 slot should hold the image's default reti stub (in BIOS ROM),
        # not zero -- otherwise an unhooked interrupt jumps to 0 and powers off.
        slot5 = m.read(0x6FCC, 4)  # VBlank user vector
        val = slot5[0] | (slot5[1] << 8) | (slot5[2] << 16) | (slot5[3] << 24)
        self.assertGreaterEqual(val & 0xFFFFFF, 0xFF0000,
                                "user vector table not seeded (anchor not found by the core)")

    def test_boot_is_deterministic(self):
        a = self._run().framebuffer()
        b = self._run().framebuffer()
        self.assertEqual(a, b, "HLE boot must be deterministic (identical framebuffer)")


if __name__ == "__main__":
    unittest.main()
