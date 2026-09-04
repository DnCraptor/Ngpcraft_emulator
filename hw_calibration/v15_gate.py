#!/usr/bin/env python3
"""Depouillement de la ROM v15 -- profondeur de file, largeur d'acces, ecritures.

    python hw_calibration/v15_gate.py                          # predictions du coeur
    python hw_calibration/v15_gate.py --p0 516 416 269 157 --rasv 198
    python hw_calibration/v15_gate.py --p1 301 301 216 116     # une page a la fois

⛔ RASV DOIT VALOIR 198, sinon aucun nombre ne veut rien dire.

PAGE 0 -- de combien la file prend-elle de l'avance ?
    Corps = `div WA,E` (2 octets, ~32 cy : il laisse la file remplir) + k charges
    `ld XWA,#imm32` (5 octets pour 5 etats : elles DEPENSENT l'avance). k = 1,2,4,8.
    Le prix plein d'une charge est deja mesure (v14 page 1 : 4,03 cy/octet, soit
    20,15 cy). Les charges qui coutent MOINS que ca sont payees avec l'avance ; le
    manque a gagner cumule EST l'avance, en cycles. La pente k=4->k=8 doit retomber
    sur 20,15 : c'est le controle du montage.
    ⇒ Ce nombre est `biu_slack` MESURE, la ou il n'etait que deduit (4 octets x 4 cy).

PAGES 1 et 2 -- un acces memoire coute-t-il par OCTET ou par ACCES ?
    Huit acces par tour, trois largeurs (1, 2, 4 octets), plus un controle de
    linearite a seize. En lecture (page 1) puis en ecriture (page 2).
      par OCTET  ⇒ RB < RW < RL, la part memoire dans un rapport 1:2:4 ;
      par ACCES  ⇒ RB ~ RW, et RL au-dessus seulement de ses 2 etats de plus.
    ⛔ Notre modele actuel dit « par acces » ET « lecture = ecriture » : il rend
    RB = RW = 81,7 et lecture ~ ecriture au cycle pres. Si le silicium separe l'une
    ou l'autre de ces deux egalites, c'est une erreur de FORME, pas de valeur --
    ne pas la corriger en changeant un nombre.
"""

import argparse
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ROM_PATH = ROOT / "hw_calibration" / "a_queue_calib_v15.ngp"
ROM_MD5 = "895e629e7c013aece93df826de3b6190"
WINDOW = 199 * 515 * 60

PAGES = {0: ("Q1", "Q2", "Q4", "Q8"), 1: ("RB", "RW", "RL", "RL16"),
         2: ("WB", "WW", "WL", "WL16"), 3: ("RASV",)}
TRIPS = {0: 200, 1: 250, 2: 250}
LOADS = (1, 2, 4, 8)
FULL_LOAD = 20.15          # v14 page 1 : 4,03 cy/octet x 5 octets
J_RIGHT, J_LEFT = 0x08, 0x04


def read_core():
    from core import native
    m = native.NativeMachine(ROM_PATH.read_bytes(),
                             bios=(ROOT / "bios.bin").read_bytes())
    m.set_timing_silicon()
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
        # ⛔ BIDIRECTIONNELLE. Le pad n'est relu qu'entre deux mesures : des que les
        # blocs sont courts, un appui maintenu franchit PLUSIEURS pages et depasse la
        # cible sans retour possible en n'appuyant que sur DROITE.
        for _ in range(60):
            cur = key.get(tile(11, 1))
            if cur == str(pg):
                break
            step( 20, J_RIGHT if (cur is None or int(cur) < pg) else J_LEFT)
            step( 20, 0)
        else:
            raise SystemExit(f"page {pg} inatteignable")
        step(len(names) * 60 + 260)
        for i, n in enumerate(names):
            s = "".join(key.get(tile(12 + j, 3 + i), " ") for j in range(5)).strip()
            out[n] = int(s) if s.isdigit() else None
    return out


def per_trip(counts, trips):
    return [WINDOW / float(c) / trips for c in counts]


def show_queue(label, counts):
    c = per_trip(counts, TRIPS[0])
    marg = [c[1] - c[0], (c[2] - c[1]) / 2.0, (c[3] - c[2]) / 4.0]
    slack = sum(max(0.0, FULL_LOAD - m) * n
                for m, n in zip(marg, (1, 2, 4)))
    print(f"  {label:10}" + "".join(f"{v:>7}" for v in counts)
          + "   marginal " + " ".join(f"{m:5.1f}" for m in marg)
          + f"   avance ~{slack:5.1f} cy")
    return marg, slack


def show_width(label, counts, trips):
    c = per_trip(counts, trips)
    print(f"  {label:10}" + "".join(f"{v:>7}" for v in counts)
          + f"   1o->2o {c[1]-c[0]:+6.1f}   2o->4o {c[2]-c[1]:+6.1f}"
          + f"   8->16 {(c[3]-c[2])/8:5.2f} cy/acces")
    return c


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    for pg in range(3):
        ap.add_argument(f"--p{pg}", type=int, nargs="*", default=None, metavar="N",
                        help=f"tir silicium page {pg} : " + " ".join(PAGES[pg]))
    ap.add_argument("--rasv", type=int, default=None)
    args = ap.parse_args()

    if args.rasv is not None and args.rasv != 198:
        print(f"[STOP] RASV = {args.rasv}, attendu 198 : aucun nombre n'est exploitable.")
        return 2

    print(f"ROM  {ROM_PATH.name}  md5 attendu {ROM_MD5}")
    core = read_core()
    print(f"  (RASV du coeur : {core['RASV']})")

    print("\n=== PAGE 0 -- avance de la file ===")
    print(f"  prix plein d'une charge, deja mesure (v14 page 1) : {FULL_LOAD} cy")
    print("  " + f"{'':10}" + "".join(f"{n:>7}" for n in PAGES[0]))
    _, slack_core = show_queue("coeur", [core[n] for n in PAGES[0]])
    if args.p0:
        marg_si, slack_si = show_queue("SILICIUM", args.p0)
        print(f"\n  => avance mesuree {slack_si:.1f} cy contre {slack_core:.1f} modelisee"
              f"  ({(slack_core/slack_si-1)*100:+.0f} %)")
        if abs(marg_si[2] - FULL_LOAD) > 2.0:
            print(f"  [STOP] La marge k4->k8 vaut {marg_si[2]:.1f} et non ~{FULL_LOAD} :")
            print("     la boucle n'est pas saturee, le montage ne mesure pas ce qu'on croit.")

    for pg, lbl in ((1, "LECTURE"), (2, "ECRITURE")):
        print(f"\n=== PAGE {pg} -- {lbl} : par octet ou par acces ? ===")
        print("  " + f"{'':10}" + "".join(f"{n:>7}" for n in PAGES[pg]))
        c_core = show_width("coeur", [core[n] for n in PAGES[pg]], TRIPS[pg])
        si = getattr(args, f"p{pg}")
        if si:
            c_si = show_width("SILICIUM", si, TRIPS[pg])
            d12, d24 = c_si[1] - c_si[0], c_si[2] - c_si[1]
            print()
            if abs(d12) < 2.0 and abs(d24 - 2 * max(d12, 0.0)) > 4.0:
                print("  => PAR ACCES. Doubler les octets ne change rien tant que le")
                print("     nombre d'acces ne bouge pas ; seul l'ecart 2o->4o apparait,")
                print("     et il vaut les 2 etats de plus de la forme longue.")
            elif d12 > 2.0 and d24 > 2.0:
                print("  => PAR OCTET. Le cout suit les octets deplaces, pas les acces.")
                print(f"     ~{d12:.1f} cy pour un octet de plus, ~{d24/2:.1f} pour les suivants.")
            else:
                print("  => MIXTE : un cout fixe par acces PLUS un cout par octet.")
                print(f"     Les deux termes se lisent dans {d12:+.1f} et {d24:+.1f}.")

    if args.p1 and args.p2:
        c1 = per_trip(args.p1, TRIPS[1])
        c2 = per_trip(args.p2, TRIPS[2])
        diff = [b - a for a, b in zip(c1, c2)]
        print("\n=== ECRITURE - LECTURE (meme largeur, meme compte) ===")
        print("  " + "  ".join(f"{n[1:] or '1o':>6}" for n in PAGES[1])
              + "\n  " + "  ".join(f"{d:+6.1f}" for d in diff))
        if max(abs(d) for d in diff) < 2.0:
            print("  => lecture et ecriture coutent PAREIL (ce que notre modele suppose).")
        else:
            print("  => elles DIFFERENT : notre modele les facture a l'identique, c'est")
            print("     une erreur de forme. L'ecriture n'a jamais ete mesuree jusqu'ici.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
