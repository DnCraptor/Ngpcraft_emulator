#!/usr/bin/env python3
"""Depouillement de la ROM v18 -- decomposer le cout d'une interruption.

    python hw_calibration/v18_gate.py                       # predictions du coeur
    python hw_calibration/v18_gate.py --p0 W0 N0 N8 N24 --p1 L0 L2 L4 L8 --rasv 198

⛔ RASV DOIT VALOIR 198.

CE QUE CETTE ROM SEPARE. La v16 mesurait le cout COMPLET d'une IRQ en un seul nombre --
114,2 cy sur console contre 132,4 chez nous. Impossible de savoir si les 18 cycles
manquants sont dans l'ENTREE (materiel + aiguillage du BIOS + retour) ou dans la facon
dont on facture le CODE du gestionnaire. Ici le gestionnaire est le notre et sa taille
varie, donc les deux se separent :

    pente    = cout d'une instruction executee DANS un gestionnaire
    ordonnee = cout FIXE d'une interruption, extrapole a un gestionnaire vide

⇒ Un seul des deux peut etre faux, et on saura lequel.

PAGE 0 -- echelle de `nop` (0, 8, 24). Le `nop` est a l'equilibre bus/execution : cette
page mesure surtout le cout FIXE proprement.

PAGE 1 -- echelle de charges `ld XWA,#imm32` (0, 2, 4, 8). Une interruption VIDE la file
d'instructions, donc les premieres instructions du gestionnaire payent leur fetch plein
tarif -- ce qu'un `nop` ne montre pas et qu'une charge de 5 octets montre. Cette page dit
si notre modele redemarre la file dans le bon etat apres une interruption.

⚡ ET ELLE DISCRIMINE AUSSI LES DEUX MODELES DE RECOUVREMENT : le cout fixe predit est
135,3 cy avec le credit en cycles, 180,0 avec la file en octets. Le tir tranchera les deux
questions d'un coup.
"""

import argparse
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ROM_PATH = ROOT / "hw_calibration" / "a_irqdec_calib_v18.ngp"
ROM_MD5 = "f5488eda1c3bd14127100c2bce974fb6"
WINDOW = 199 * 515 * 60
TRIPS = 60
IRQ_PER_WINDOW = 60 * 152          # une impulsion TI0 par ligne
PAGES = {0: ("W0", "N0", "N8", "N24"), 1: ("L0", "L2", "L4", "L8"), 2: ("RASV",)}
NOPS = (0, 8, 24)                  # N0, N8, N24
LOADS = (0, 2, 4, 8)               # L0, L2, L4, L8
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


def per_irq(w0, counts):
    """Cout d'une interruption, gestionnaire compris, pour chaque taille."""
    base = WINDOW / w0
    out = []
    for c in counts:
        irq = IRQ_PER_WINDOW / c          # interruptions subies par bloc
        out.append((WINDOW / c - base) / irq)
    return out


def fit(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    sl = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    it = my - sl * mx
    w = max(abs(y - (it + sl * x)) for x, y in zip(xs, ys))
    return sl, it, w


def show(label, p0, p1):
    cost_n = per_irq(p0[0], p0[1:])
    sl_n, fixe_n, res_n = fit([float(x) for x in NOPS], cost_n)
    cost_l = per_irq(p0[0], p1)
    sl_l, fixe_l, res_l = fit([float(x) for x in LOADS], cost_l)
    print(f"  {label:16}" + "".join(f"{v:>7}" for v in p0)
          + f"   |{sl_n:6.2f} cy/nop   FIXE {fixe_n:6.1f} cy  (res {res_n:.1f})")
    print(f"  {'':16}" + "".join(f"{v:>7}" for v in p1)
          + f"   |{sl_l:6.2f} cy/charge FIXE {fixe_l:6.1f} cy  (res {res_l:.1f})")
    return sl_n, fixe_n, sl_l, fixe_l


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
    print("  " + f"{'':16}" + "".join(f"{n:>7}" for n in PAGES[0]) + "   (puis page 1)")
    refs = {}
    for lbl, q in (("credit (courant)", 0), ("file 4 octets", 4)):
        c = read_core(q)
        refs[lbl] = show(lbl, [c[n] for n in PAGES[0]], [c[n] for n in PAGES[1]])

    if not (args.p0 and args.p1):
        print("\nPas de tir fourni. Flashe, note les neuf nombres, puis relance :")
        print("  python hw_calibration/v18_gate.py --p0 W0 N0 N8 N24"
              " --p1 L0 L2 L4 L8 --rasv 198")
        return 0

    print()
    sl_n, fixe_n, sl_l, fixe_l = show("SILICIUM", args.p0, args.p1)
    print()
    print(f"  Rappel v16 : cout total d'une IRQ mesure a 114,2 cy (gestionnaire quasi vide).")
    print()
    ok_nop = abs(sl_n - 4.0) < 1.0
    if ok_nop:
        print(f"  => LE CODE D'UN ISR EST FACTURE JUSTE ({sl_n:.2f} cy/nop, contre ~4 hors ISR).")
        print("     Tout l'ecart est donc dans le cout FIXE de l'interruption :")
        for lbl, (a, b, c2, d) in refs.items():
            print(f"       {lbl:16} {b:6.1f} cy   contre {fixe_n:6.1f} mesures"
                  f"   ({(b/fixe_n - 1)*100:+.0f} %)")
        print("     => Une seule constante a corriger, et elle ne depend plus du modele")
        print("       de recouvrement : c'est l'entree + l'aiguillage du BIOS + le retour.")
    else:
        print(f"  [STOP] Un `nop` coute {sl_n:.2f} cy DANS un ISR contre ~4 ailleurs.")
        print("     Ce n'est pas l'entree qui est fausse, c'est la facon dont on facture")
        print("     le code d'un gestionnaire -- chercher la AVANT de toucher a l'entree.")

    print()
    print(f"  Page 1 : {sl_l:.2f} cy par charge dans l'ISR (hors ISR : 20,15 cy mesures v14).")
    if abs(sl_l - 20.15) > 2.0:
        print("     => La file ne redemarre PAS dans le bon etat apres une interruption :")
        print("       les premieres instructions du gestionnaire ne payent pas ce qu'elles")
        print("       devraient. C'est la piece que le modele en octets ne facture pas.")
    else:
        print("     => La file redemarre dans le bon etat : rien a chercher de ce cote.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
