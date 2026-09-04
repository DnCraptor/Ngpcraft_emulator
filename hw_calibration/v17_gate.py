#!/usr/bin/env python3
"""Depouillement de la ROM v17 -- `mul` et `div` en forme MOT.

    python hw_calibration/v17_gate.py                        # predictions du coeur
    python hw_calibration/v17_gate.py --p0 D1 D2 D4 D8 --p1 P1 P2 P4 P8 --rasv 198

⛔ RASV DOIT VALOIR 198.

CE QUE CETTE ROM DEBLOQUE. Le modele de file EN OCTETS (`queue_bytes = 4`) reproduit
le montage v16 page 0 au dixieme de cycle -- 26,6 contre 26,5 mesures -- avec la taille
documentee de la file et aucun parametre libre. Mais arme, il laisse trois cases du
corpus tres au-dessus : MUL -9,0 %, DIV -6,0 %, WORK1 -12,4 %. Les deux premieres sont
la forme MOT de `mul`/`div`, heritee du fetch a 10 cy/mot et jamais remesuree -- la v14
n'a exerce que la forme OCTET.

=> Ce banc cherche les couts MOT **avec le modele de file arme**. Les chercher sous
l'ancien modele ne servirait a rien : c'est precisement le couple qu'on veut resoudre.

LE MONTAGE. Unite = `ld XWA,#imm32` + l'operation, k unites par tour (k = 1,2,4,8).
Les deux pages ne different que par UN OCTET (0x50 contre 0x40), donc `DIV - MUL` est
le surcout de la division sans hypothese sur le reste : c'est le nombre le plus solide.
"""

import argparse
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ROM_PATH = ROOT / "hw_calibration" / "a_word_calib_v17.ngp"
ROM_MD5 = "e71b75bbcd52284fad95ff5233c796d5"
WINDOW = 199 * 515 * 60
PAGES = {0: ("D1", "D2", "D4", "D8"), 1: ("P1", "P2", "P4", "P8"), 2: ("RASV",)}
UNITS = (1, 2, 4, 8)
TRIPS = 60
J_RIGHT, J_LEFT = 0x08, 0x04

# Les couts MOT actuels, herites du fetch a 10 cy/mot (reg_family.cpp).
MUL_WORD_NOW, DIV_WORD_NOW = 19, 56


def read_core(queue=4, mul_word=None, div_word=None):
    from core import native
    m = native.NativeMachine(ROM_PATH.read_bytes(),
                             bios=(ROOT / "bios.bin").read_bytes())
    m.set_timing_silicon()
    if queue:
        m.set_queue_bytes(queue)
    if mul_word is not None or div_word is not None:
        m.set_muldiv_word(mul_word or 0, div_word or 0)
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
        # ⛔ NAVIGATION BIDIRECTIONNELLE, ET C'EST NECESSAIRE. Le pad n'est relu
        # qu'entre deux mesures ; des que les blocs sont courts, un seul appui
        # maintenu franchit PLUSIEURS pages et depasse la cible sans retour possible
        # en n'appuyant que sur DROITE. On corrige donc dans les deux sens.
        for _ in range(60):
            cur = key.get(tile(11, 1))
            if cur == str(pg):
                break
            btn = J_RIGHT if (cur is None or int(cur) < pg) else J_LEFT
            step(20, btn)
            step(20, 0)
        else:
            raise SystemExit(f"page {pg} inatteignable")
        step(len(names) * 60 + 280)
        for i, n in enumerate(names):
            s = "".join(key.get(tile(12 + j, 3 + i), " ") for j in range(5)).strip()
            out[n] = int(s) if s.isdigit() else None
    return out


def slope(counts):
    xs = [float(u) for u in UNITS]
    ys = [WINDOW / c / TRIPS for c in counts]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    sl = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    it = my - sl * mx
    w = max(abs(y - (it + sl * x)) / y * 100.0 for x, y in zip(xs, ys))
    return sl, w


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--p0", type=int, nargs="*", default=None, metavar="N")
    ap.add_argument("--p1", type=int, nargs="*", default=None, metavar="N")
    ap.add_argument("--rasv", type=int, default=None)
    ap.add_argument("--queue", type=int, default=4,
                    help="taille de file en octets pour la recherche (0 = ancien modele)")
    args = ap.parse_args()

    if args.rasv is not None and args.rasv != 198:
        print(f"[STOP] RASV = {args.rasv}, attendu 198 : rien n'est exploitable.")
        return 2

    print(f"ROM  {ROM_PATH.name}  md5 attendu {ROM_MD5}")
    print(f"  modele de file : {args.queue} octets"
          + ("  (le modele mesure par la v16)" if args.queue else "  (ancien credit)"))

    base = read_core(args.queue)
    sd, wd = slope([base[n] for n in PAGES[0]])
    sp, wp = slope([base[n] for n in PAGES[1]])
    print(f"\n  {'coeur':10}" + "".join(f"{base[n]:>7}" for n in PAGES[0])
          + f"   div {sd:6.2f} cy  (droite {wd:.2f} %)")
    print(f"  {'':10}" + "".join(f"{base[n]:>7}" for n in PAGES[1])
          + f"   mul {sp:6.2f} cy  (droite {wp:.2f} %)")
    print(f"  {'':10}{'':28}   DIV - MUL {sd - sp:6.2f} cy")

    if not (args.p0 and args.p1):
        print("\nPas de tir complet fourni. Flashe, note les neuf nombres, puis relance :")
        print("  python hw_calibration/v17_gate.py --p0 D1 D2 D4 D8 --p1 P1 P2 P4 P8 --rasv 198")
        return 0

    sdi, wdi = slope(args.p0)
    spi, wpi = slope(args.p1)
    print(f"\n  {'SILICIUM':10}" + "".join(f"{v:>7}" for v in args.p0)
          + f"   div {sdi:6.2f} cy  (droite {wdi:.2f} %)")
    print(f"  {'':10}" + "".join(f"{v:>7}" for v in args.p1)
          + f"   mul {spi:6.2f} cy  (droite {wpi:.2f} %)")
    print(f"  {'':10}{'':28}   DIV - MUL {sdi - spi:6.2f} cy")

    if max(wdi, wpi) > 3.0:
        print(f"\n  [STOP] Les points ne ferment pas une droite ({max(wdi, wpi):.2f} %) :")
        print("     ne rien conclure.")
        return 2

    print(f"\n  Ecart du modele : div {(sd/sdi - 1)*100:+.1f} %, "
          f"mul {(sp/spi - 1)*100:+.1f} %.")
    print("\n  Recherche des couts MOT qui reproduisent les deux pentes :")
    best = {}
    for name, pg, target, cur in (("div", 0, sdi, DIV_WORD_NOW), ("mul", 1, spi, MUL_WORD_NOW)):
        lo, hi = max(2, cur - 24), cur + 8
        found = None
        for v in range(lo, hi + 1):
            r = read_core(args.queue,
                          mul_word=(v if name == "mul" else None),
                          div_word=(v if name == "div" else None))
            s, _ = slope([r[n] for n in PAGES[pg]])
            if found is None or abs(s - target) < abs(found[1] - target):
                found = (v, s)
        best[name] = found
        unit = "etats" if name == "mul" else "cycles"
        print(f"    {name} MOT : {found[0]} {unit}  (pente {found[1]:.2f} contre "
              f"{target:.2f} mesuree)   -- actuel {cur}")
    print("\n  => Reporter ces deux valeurs dans reg_family.cpp, PUIS rejouer le corpus")
    print("     avec le modele de file arme : c'est le couple qui se valide, pas chacun.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
