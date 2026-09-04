#!/usr/bin/env python3
"""Depouillement de la ROM v19 -- le vidage de file au RETOUR d'interruption.

    python hw_calibration/v19_gate.py                                   # predictions
    python hw_calibration/v19_gate.py --p0 W1 W2 W3 W5 --p1 I1 I2 I3 I5 --rasv 198

⛔ RASV DOIT VALOIR 198.

⚠️ ELLE NE TESTE PLUS CE POUR QUOI ELLE A ETE ECRITE. L'idee de depart -- « le cout
d'une IRQ depend de la LARGEUR du code ou elle revient » -- a ete REFUTEE en emulation
avant tout tir : nos deux modeles rendent un cout PLAT sur 2/3/5 octets (une instruction
large cale plus MAIS recharge plus, ca se compense). La ROM a donc ete mise de cote.

⚡ ELLE DISCRIMINE POURTANT, SUR UN AUTRE AXE : LA RISTOURNE. Nos deux modeles laissent
les cycles de l'ISR RECHARGER la file du flot interrompu -- une interruption y rend donc
le code interrompu moins cher. L'effet n'existe que si la boucle est LIMITEE PAR LE BUS :

    boucle de `nop` (1 octet)          file toujours pleine -> RIEN a regagner
    boucle de `ld XWA,#imm32` (5 o.)   file toujours vide   -> ristourne maximale

⇒ nos modeles predisent que **l'interruption coute PLUS CHER dans une boucle de `nop`**
que dans une boucle de charges. C'est contre-intuitif, c'est mesurable, et c'est
exactement la signature de la ristourne.

⛔ CE QUI TRANCHE, ET CE QUE CHAQUE REPONSE VEUT DIRE :

  cout PLAT (~111 partout)  -> la ristourne est un ARTEFACT de nos deux modeles. Alors
                               le chemin vaut 110 (annexe B) partout, et notre
                               sur-facturation (`data_access_cycles` sur les `push`,
                               `branch_taken_extra` sur le `reti`) est REELLE.
  `nop` plus CHER (+10-20)  -> la ristourne EXISTE sur silicium. Alors le depouillement
                               est le bon juge, notre credit en cycles est deja bon, et
                               c'est le modele en octets qu'il faut corriger.

⚖️ Les deux lectures sont aujourd'hui coherentes avec TOUT ce qu'on a mesure. Aucune
mesure de debit moyenne ne peut les separer -- il faut CE contraste-la.

    cout par IRQ (largeur k) = (FEN/I_k - FEN/W_k) / (IRQ_PAR_FEN / I_k)

Les deux moities partagent le meme lot de travail : le cout propre de la boucle se
simplifie dans la difference. Le gestionnaire est VIDE et identique aux quatre largeurs.
"""

import argparse
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ROM_PATH = ROOT / "hw_calibration" / "a_retq_calib_v19.ngp"
ROM_MD5 = "0a9cffdf080d17947cddb814ad313e4e"
WINDOW = 199 * 515 * 60            # une fenetre de 60 trames, en cycles
IRQ_PER_WINDOW = 60 * 152          # une impulsion TI0 par ligne
PAGES = {0: ("W1", "W2", "W3", "W5"), 1: ("I1", "I2", "I3", "I5"), 2: ("RASV",)}
J_RIGHT, J_LEFT = 0x08, 0x04


def read_core(queue=0):
    from core import native
    m = native.NativeMachine(ROM_PATH.read_bytes(),
                             bios=(ROOT / "bios.bin").read_bytes())
    m.set_timing_silicon()
    if queue:
        m.set_queue_bytes(queue)
        m.set_muldiv_word(15, 47)
    m.reset(bios_handoff=True)

    def step(n, b=0):
        for _ in range(n):
            m.write(0x00B0, bytes([b]))
            m.run_frames(1)

    step(400)

    def tile(c, r):
        return struct.unpack("<H", m.read(0x9000 + (r * 32 + c) * 2, 2))[0] & 0x01FF

    key = {tile(1 + i, 17): str(i) for i in range(10)}
    if len(key) != 10:
        raise SystemExit("cle de chiffres illisible")
    out = {}
    for pg, names in PAGES.items():
        for _ in range(60):
            cur = key.get(tile(11, 1))
            if cur == str(pg):
                break
            step(20, J_RIGHT if (cur is None or int(cur) < pg) else J_LEFT)
            step(20, 0)
        else:
            raise SystemExit(f"page {pg} inatteignable")
        step(len(names) * 60 + 280)
        for i, n in enumerate(names):
            s = "".join(key.get(tile(12 + j, 3 + i), " ") for j in range(5)).strip()
            out[n] = int(s) if s.isdigit() else None
    return out


def per_irq(off, on):
    """Cout marginal d'une interruption, largeur par largeur."""
    out = []
    for w, i in zip(off, on):
        if not w or not i:
            out.append(None)
            continue
        irq = IRQ_PER_WINDOW / i          # interruptions subies par bloc
        out.append((WINDOW / i - WINDOW / w) / irq)
    return out


def show(label, p0, p1):
    cost = per_irq(p0, p1)
    txt = "".join(f"{c:7.1f}" if c is not None else f"{'?':>7}" for c in cost)
    ok = [c for c in cost if c is not None]
    span = (max(ok) - min(ok)) if len(ok) > 1 else 0.0
    print(f"  {label:18}" + "".join(f"{v:>7}" for v in p0)
          + " |" + "".join(f"{v:>7}" for v in p1) + f"   |{txt}   pente {span:+6.1f}")
    return cost, span


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--p0", type=int, nargs="*", default=None, metavar="N",
                    help="tir silicium page 0 : " + " ".join(PAGES[0]))
    ap.add_argument("--p1", type=int, nargs="*", default=None, metavar="N",
                    help="tir silicium page 1 : " + " ".join(PAGES[1]))
    ap.add_argument("--rasv", type=int, default=None)
    args = ap.parse_args()

    if args.rasv is not None and args.rasv != 198:
        print(f"[STOP] RASV = {args.rasv}, attendu 198 : rien n'est exploitable.")
        return 2

    print(f"ROM  {ROM_PATH.name}  md5 attendu {ROM_MD5}")
    print("  " + f"{'':18}" + "".join(f"{n:>7}" for n in PAGES[0])
          + " |" + "".join(f"{n:>7}" for n in PAGES[1])
          + "   | cout par IRQ (1/2/3/5 octets)")
    refs = {}
    for lbl, q in (("credit (courant)", 0), ("file 4 octets", 4)):
        c = read_core(q)
        refs[lbl] = show(lbl, [c[n] for n in PAGES[0]], [c[n] for n in PAGES[1]])

    if not (args.p0 and args.p1):
        print("\nPas de tir fourni. Flashe, note les huit nombres, puis relance :")
        print("  python hw_calibration/v19_gate.py --p0 W1 W2 W3 W5"
              " --p1 I1 I2 I3 I5 --rasv 198")
        return 0

    print()
    cost, span = show("SILICIUM", args.p0, args.p1)
    print()
    ok = [c for c in cost if c is not None]
    if len(ok) < 2:
        print("  [STOP] tir incomplet.")
        return 2
    # ⚡ LE CONTRASTE QUI COMPTE EST `nop` CONTRE LES CHARGES, pas la pente 2->5.
    nop, wide = ok[0], sum(ok[1:]) / len(ok[1:])
    print(f"  `nop` (1 o.) {nop:6.1f} cy   charges (2/3/5 o.) {wide:6.1f} cy"
          f"   contraste {nop - wide:+6.1f}")
    print("  nos modeles predisent  credit +18,0   file +9 a +12")
    print()
    if abs(nop - wide) < 5.0:
        print("  => COUT PLAT : LA RISTOURNE EST UN ARTEFACT.")
        print("     Une interruption ne regagne rien sur le flot interrompu. Le chemin")
        print("     vaut donc les 110 cy de l'annexe B, et notre sur-facturation est")
        print("     REELLE : `data_access_cycles` sur un `PUSH (mem)` et")
        print("     `branch_taken_extra` sur un `reti` facturent ce que les etats")
        print("     tabules contiennent deja. => rendre cette regle coherente sur toute")
        print("     la table, puis rejouer le corpus.")
    else:
        print("  => LA RISTOURNE EXISTE SUR SILICIUM.")
        print("     Le depouillement en debit est alors le bon juge, notre credit en")
        print("     cycles est deja proche, et c'est le modele en octets qu'il faut")
        print("     corriger. => NE PAS toucher a `data_access_cycles`.")
    print()
    print(f"  (amplitude : silicium {nop - wide:+.1f} contre credit +18,0 et file"
          f" +9 a +12 -- si le signe est bon mais l'amplitude autre, c'est la TAILLE")
    print("   de la file qui est en cause, pas son principe.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
