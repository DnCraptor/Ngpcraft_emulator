"""THE SAVE ACROSS POWER CYCLES -- the whole chain, on a real file.

`test_bios_flash_syscall.py` proves a save reaches the chip. `test_flash_file.py`
proves the sidecar format. Neither could catch what sits BETWEEN them: what size the
cartridge image is written back at, and what that does to the NEXT session.

⚠️ EVERY SESSION HERE SAVES DIFFERENT BYTES. Writing the same payload twice cannot
fail -- a NOR cell ANDs, so programming a byte over itself leaves it alone and the
read-back matches whether or not the erase worked. That is how this defect hid: the
first version of this test reported four green sessions while the file was being
corrupted from the second one on.
"""

from __future__ import annotations

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HLE_IMAGE = REPO / "hle_bios" / "bios_hle.bin"

from core import native  # noqa: E402

XWA, XBC, XDE, XHL = 0, 1, 2, 3
CODE, SRC = 0x004000, 0x004100
VECT_FLASHWRITE, VECT_FLASHERS = 6, 8


def _rom(size: int) -> bytes:
    rom = bytearray(b"\xFF" * size)
    rom[0:28] = b" LICENSED BY SNK CORPORATION"
    rom[0x1C:0x20] = (0x200040).to_bytes(4, "little")
    rom[0x23] = 0x10
    rom[0x40] = 0x05
    return bytes(rom)


@unittest.skipUnless(HLE_IMAGE.exists(), "hle_bios/bios_hle.bin not built")
@unittest.skipUnless(native.available(), "native core not built")
class SaveSurvivesPowerCycles(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    @staticmethod
    def _call(m, wa, bc=0, de=0, hl=0):
        m.write(CODE, bytes([0xF9, 0x05]))
        st = m.cpu(); st.pc = CODE; m.set_cpu(st)
        st = m.cpu()
        b3 = st.regs if st.rfp == 3 else st.banks[3]
        b3[XWA], b3[XBC], b3[XDE], b3[XHL] = wa, bc, de, hl
        m.set_cpu(st)
        m.run(4_000_000, record=False)

    def _session(self, path, seed, *, block, offset, card=0):
        from core.native_session import NativeSession
        payload = bytes((i * seed + 1) & 0xFF for i in range(256))
        s = NativeSession(path, bios_path=HLE_IMAGE, flash_size=0x200000,
                          autosave=False, sidecar=False, save_path=self.dir / "s.flash")
        try:
            base = 0x800000 if card else 0x200000
            s.machine.write(SRC, payload)
            self._call(s.machine, (VECT_FLASHERS << 8) | card, bc=block << 8)
            self._call(s.machine, (VECT_FLASHWRITE << 8) | card, bc=1, hl=SRC, de=offset)
            stuck = s.machine.read(base + offset, 256) == payload
            card_byte = s.machine.read(0x006C58 + card, 1)[0]
            s.commit_save()
        finally:
            s.close()
        return stuck, card_byte, payload

    def test_an_under_filled_cart_saves_every_session_not_just_the_first(self):
        """THE ONE THAT WAS BROKEN. A 512 KiB image on an 8 Mbit chip (the Delta Warp
        shape). Session 1 worked and grew the file to the size we had GUESSED (2 MiB);
        from session 2 the padding read as image, the cart's correction was refused as
        "smaller than the ROM", the erase went to a 16 Mbit address and the program to
        an 8 Mbit one, and every save after the first was ANDed into an unerased slot.
        """
        path = self.dir / "underfilled.ngc"
        path.write_bytes(_rom(0x080000))
        for n, seed in enumerate((7, 29, 53, 101), 1):
            with self.subTest(session=n):
                stuck, card, payload = self._session(path, seed, block=17, offset=0x0FA000)
                self.assertTrue(stuck, f"session {n} did not stick")
                self.assertEqual(card, 2, "the cart is an 8 Mbit card and must stay one")
        self.assertEqual(path.stat().st_size, 0x100000,
                         "the file should be exactly one 8 Mbit chip, not the guess")
        self.assertEqual(path.read_bytes()[0x0FA000:0x0FA100], payload,
                         "the last save is not the one on disk")

    def test_a_cart_that_really_uses_the_bigger_chip_still_grows(self):
        """The opposite case, and it must NOT be broken by the fix: a small image whose
        save lives in a 16 Mbit chip's top block (the StarGunner shape). Here growing
        the file is right -- that is where the save is."""
        path = self.dir / "stargunner.ngc"
        path.write_bytes(_rom(0x100000))
        stuck, _, payload = self._session(path, 13, block=33, offset=0x1FA000)
        self.assertTrue(stuck)
        self.assertEqual(path.stat().st_size, 0x200000)
        self.assertEqual(path.read_bytes()[0x1FA000:0x1FA100], payload)

    def test_a_file_that_already_grew_heals_itself(self):
        """The shape an earlier version left on disk: 512 KiB of game, padded out to
        2 MiB, with a save in it. That padding is ERASED FLASH, not image -- reading it
        as image is what kept the cart from correcting us, so the floor under a
        capacity is the DATA on the die, not the file's length. The first load after
        the fix must recognise the 8 Mbit card and write the file back at one chip."""
        path = self.dir / "grown.ngc"
        img = bytearray(b"\xFF" * 0x200000)
        img[0:0x80000] = bytes((i * 13 + 7) & 0xFF for i in range(0x80000))
        img[0:28] = b" LICENSED BY SNK CORPORATION"
        img[0x1C:0x20] = (0x200040).to_bytes(4, "little")
        img[0x23] = 0x10
        img[0x40] = 0x05
        img[0x0FA000:0x0FA100] = bytes((i * 3 + 9) & 0xFF for i in range(256))  # an old save
        path.write_bytes(bytes(img))

        stuck, card, payload = self._session(path, 41, block=17, offset=0x0FA000)
        self.assertTrue(stuck)
        self.assertEqual(card, 2, "the erased tail was counted as image again")
        self.assertEqual(path.stat().st_size, 0x100000, "the file did not come back down")
        self.assertEqual(path.read_bytes()[0x0FA000:0x0FA100], payload)

    def test_a_two_die_cart_keeps_both_dies(self):
        """4 MiB is two chips. Persisting only what the first one presents would cut
        the cartridge in half."""
        path = self.dir / "twodie.ngc"
        path.write_bytes(_rom(0x400000))
        stuck, _, payload = self._session(path, 17, block=33, offset=0x1FA000, card=1)
        self.assertTrue(stuck)
        self.assertEqual(path.stat().st_size, 0x400000, "the second die was dropped")
        self.assertEqual(path.read_bytes()[0x200000 + 0x1FA000:0x200000 + 0x1FA100], payload)


if __name__ == "__main__":
    unittest.main()
