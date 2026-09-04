"""LE MODE « FICHIER SEPARE », DE BOUT EN BOUT -- ce que personne ne testait.

⛔ LE TROU. `test_save_persistence.py` couvre la sauvegarde a travers des cycles
d'alimentation, mais TOUTES ses sessions passent `sidecar=False`: elles mesurent le mode
« dans la ROM ». Le reglage « Fichier separe » (`ngpc_settings.SAVE_SIDECAR`) construit
la session autrement -- `save_to_rom=False, sidecar=True` -- et ce chemin-la n'avait
aucune couverture. Signale par un joueur: « quand les sauvegardes in-game sont en
Fichier separe, ni le BIOS ni aucun jeu ne sauvegarde ou ne recharge ses donnees ».

Ce fichier ne reproduit PAS ce symptome (le mecanisme tient sur les trois formes
ci-dessous), et c'est precisement pour ca qu'il existe: sans lui, la prochaine passe
recommencerait a douter du mecanisme au lieu de chercher plus haut.

⚠️ CHAQUE SESSION ECRIT DES OCTETS DIFFERENTS. Reprogrammer la meme charge utile ne peut
pas echouer -- une cellule NOR fait un ET, donc programmer un octet sur lui-meme le laisse
tel quel et la relecture correspond que l'effacement ait marche ou non. C'est comme ca
que le defaut de juillet a tenu quatre sessions « vertes ».
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HLE_IMAGE = REPO / "hle_bios" / "bios_hle.bin"
RETAIL_BIOS = REPO / "bios.bin"

from core import native  # noqa: E402

XWA, XBC, XDE, XHL = 0, 1, 2, 3
CODE, SRC = 0x004000, 0x004100
VECT_FLASHWRITE, VECT_FLASHERS = 6, 8
CART_BASE = 0x200000


def _rom(size: int) -> bytes:
    rom = bytearray(b"\xFF" * size)
    rom[0:28] = b" LICENSED BY SNK CORPORATION"
    rom[0x1C:0x20] = (0x200040).to_bytes(4, "little")
    rom[0x23] = 0x10
    rom[0x40] = 0x05
    return bytes(rom)


@unittest.skipUnless(HLE_IMAGE.exists(), "hle_bios/bios_hle.bin not built")
@unittest.skipUnless(native.available(), "native core not built")
class SeparateFileMode(unittest.TestCase):
    def setUp(self) -> None:
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

    def _session(self, cart, flash, cap, *, bios=None, real_bios=False, boot=0):
        """A session built the way the shell builds one in 'Separate file' mode."""
        from core.native_session import NativeSession
        s = NativeSession(cart, bios_path=bios or HLE_IMAGE, flash_size=cap,
                          autosave=False, save_to_rom=False, sidecar=True,
                          save_path=flash, real_bios=real_bios)
        for _ in range(boot):
            s.machine.run_frames(1)
        return s

    def _write_save(self, s, seed: int) -> bytes:
        payload = bytes((i * seed + 1) & 0xFF for i in range(256))
        s.machine.write(SRC, payload)
        self._call(s.machine, (VECT_FLASHERS << 8) | 0, bc=0)
        self._call(s.machine, (VECT_FLASHWRITE << 8) | 0, bc=1, hl=SRC, de=0)
        return payload

    def test_the_rom_file_is_never_touched_and_the_save_still_comes_back(self):
        """C'est la PROMESSE du mode: la collection du joueur reste intacte, et la
        sauvegarde vit dans `saves/<jeu>.flash`. Les deux moities comptent -- un mode
        qui laisse la ROM tranquille en perdant la sauvegarde n'en est pas un."""
        cart = self.dir / "game.ngc"
        cart.write_bytes(_rom(0x200000))
        pristine = cart.read_bytes()
        flash = self.dir / "game.flash"

        s = self._session(cart, flash, 0x200000)
        payload = self._write_save(s, 7)
        self.assertEqual(s.machine.read(CART_BASE, 256), payload, "l'ecriture n'a pas pris")
        self.assertTrue(s.commit_save(), "rien n'a ete ecrit")
        s.close()

        self.assertEqual(cart.read_bytes(), pristine, "le .ngc a ete modifie")
        self.assertTrue(flash.exists(), "aucun fichier de sauvegarde separe")

        s2 = self._session(cart, flash, 0x200000)
        try:
            self.assertTrue(s2.save_loaded)
            self.assertEqual(s2.machine.read(CART_BASE, 256), payload,
                             "la sauvegarde n'est pas revenue")
        finally:
            s2.close()

    def test_three_sessions_each_keep_the_one_before(self):
        """Trois charges DIFFERENTES: c'est le seul moyen de voir un effacement rate.
        Et la cartouche est sous-remplie (512 Kio sur une puce de 8 Mbit), la forme qui
        avait deja casse les sauvegardes en juillet -- dans l'autre mode."""
        cart = self.dir / "small.ngc"
        cart.write_bytes(_rom(0x80000))
        pristine = cart.read_bytes()
        flash = self.dir / "small.flash"

        previous = None
        for seed in (7, 13, 29):
            s = self._session(cart, flash, 0x100000)
            try:
                if previous is not None:
                    self.assertEqual(s.machine.read(CART_BASE, 256), previous,
                                     f"session seed={seed}: la sauvegarde precedente a disparu")
                payload = self._write_save(s, seed)
                self.assertEqual(s.machine.read(CART_BASE, 256), payload)
                self.assertTrue(s.commit_save())
            finally:
                s.close()
            previous = payload
        self.assertEqual(cart.read_bytes(), pristine, "le .ngc a ete modifie")

    @unittest.skipUnless(RETAIL_BIOS.exists(), "needs the retail bios.bin (gitignored)")
    def test_it_survives_a_real_console_boot(self):
        """⚡ LA COMBINAISON DU RAPPORT: fichier separe ET demarrage console.

        Le BIOS reel tourne pour de bon avant le jeu, ce qui donne a la console des
        centaines de trames pour toucher la cartouche entre la restauration et la
        premiere lecture du joueur. La sauvegarde doit etre la AVANT et APRES ce boot.
        """
        cart = self.dir / "boot.ngc"
        cart.write_bytes(_rom(0x200000))
        flash = self.dir / "boot.flash"

        s = self._session(cart, flash, 0x200000, bios=RETAIL_BIOS, real_bios=True,
                          boot=420)
        payload = self._write_save(s, 11)
        self.assertTrue(s.commit_save())
        s.close()

        s2 = self._session(cart, flash, 0x200000, bios=RETAIL_BIOS, real_bios=True)
        try:
            self.assertEqual(s2.machine.read(CART_BASE, 256), payload,
                             "perdue avant meme que la console demarre")
            for _ in range(420):
                s2.machine.run_frames(1)
            self.assertEqual(s2.machine.read(CART_BASE, 256), payload,
                             "le boot du BIOS a efface la sauvegarde restauree")
        finally:
            s2.close()


if __name__ == "__main__":
    unittest.main()
