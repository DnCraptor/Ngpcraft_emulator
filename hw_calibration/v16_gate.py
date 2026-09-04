#!/usr/bin/env python3
"""Depouillement de la ROM v16 -- avance de la file, et cout reel d'une interruption.

    python hw_calibration/v16_gate.py                            # predictions du coeur
    python hw_calibration/v16_gate.py --p0 228 209 193 180 --rasv 198
    python hw_calibration/v16_gate.py --p1 180 144 161 170 175

⛔ RASV DOIT VALOIR 198, sinon aucun nombre ne veut rien dire.

PAGE 0 -- le bus peut-il travailler pendant un calcul long ?
    Huit charges `ld XWA,#imm32` (limitees par le bus : 160 cy de fetch contre 80
    d'execution) dans lesquelles on insere k divisions (2 octets, ~26 cy d'execution).
    La boucle reste limitee par le bus jusqu'a k=3, donc le cout MARGINAL d'une
    division dit tout :
        ~8 cy   recouvrement TOTAL (seuls ses 2 octets se payent)
        ~34 cy  recouvrement NUL   (8 de fetch + 26 d'execution, serialises)
        entre   recouvrement BORNE ; la borne est l'avance reelle : 34 - pente.
    ⇒ `biu_slack` MESURE au lieu d'etre deduit (nous : 16 cy).

PAGE 1 -- ce que coute une interruption, a quatre cadences
    Le meme lot de travail sous cinq regimes (aucune IRQ, puis une toutes les 1, 2, 4
    et 8 lignes). Le cout d'un bloc contre le NOMBRE d'interruptions qu'il subit est
    une droite dont la pente est le cout COMPLET d'une IRQ prise -- entree, stub BIOS
    et `reti` compris.
    ⚠️ W0 est le CONTROLE : aucune interruption. S'il derive du lot de travail nu, le
    reglage du timer a fuit et les quatre autres ne veulent rien dire.
    ⛔ Ce n'est pas le cout de l'ENTREE seule : aucun gestionnaire n'est installe.
"""

import argparse
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ROM_PATH = ROOT / "hw_calibration" / "a_slack_calib_v16.ngp"
ROM_MD5 = "d81c580d6c632137f7ed3e6191b37708"
WINDOW = 199 * 515 * 60

PAGES = {0: ("L8", "D1", "D2", "D3"), 1: ("W0", "W1", "W2", "W4", "W8"), 2: ("RASV",)}
TRIPS = {0: 150, 1: 100}
DIVS = (0, 1, 2, 3)
PERIODS = (0, 1, 2, 4, 8)          # 0 = interruptions interdites
IRQ_PER_WINDOW = 60 * 152          # une impulsion TI0 par ligne, 152 lignes utiles
FULL_SERIAL = 34.0                 # 8 cy de fetch + ~26 d'execution, sans recouvrement
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
        step(len(names) * 60 + 280)
        for i, n in enumerate(names):
            s = "".join(key.get(tile(12 + j, 3 + i), " ") for j in range(5)).strip()
            out[n] = int(s) if s.isdigit() else None
    return out


def fit(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    inter = my - slope * mx
    worst = max(abs(y - (inter + slope * x)) / y * 100.0 for x, y in zip(xs, ys))
    return slope, inter, worst


def show_slack(label, counts):
    ys = [WINDOW / c / TRIPS[0] for c in counts]
    slope, _, worst = fit([float(d) for d in DIVS], ys)
    slack = FULL_SERIAL - slope
    print(f"  {label:10}" + "".join(f"{v:>7}" for v in counts)
          + f"   {slope:6.1f} cy/division   avance {slack:5.1f} cy"
          + f"   (droite {worst:.2f} %)")
    return slope, slack, worst


def show_irq(label, counts):
    """Cout d'un bloc contre le nombre d'IRQ qu'il subit. W0 (0 IRQ) est l'origine."""
    xs, ys = [], []
    for p, c in zip(PERIODS, counts):
        cost = WINDOW / c
        irq = 0.0 if p == 0 else (IRQ_PER_WINDOW / p) / c
        xs.append(irq)
        ys.append(cost)
    slope, inter, worst = fit(xs, ys)
    print(f"  {label:10}" + "".join(f"{v:>7}" for v in counts)
          + f"   {slope:6.1f} cy/interruption   (droite {worst:.2f} %)")
    return slope, inter, worst


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
    core = read_core()
    print(f"  (RASV du coeur : {core['RASV']})")

    print("\n=== PAGE 0 -- avance de la file pendant un calcul long ===")
    print(f"  reperes : ~8 cy = recouvrement TOTAL, ~{FULL_SERIAL:.0f} cy = NUL")
    print("  " + f"{'':10}" + "".join(f"{n:>7}" for n in PAGES[0]))
    s_core, slack_core, _ = show_slack("coeur", [core[n] for n in PAGES[0]])
    if args.p0:
        if len(args.p0) != 4:
            raise SystemExit("page 0 attend 4 nombres")
        s_si, slack_si, worst = show_slack("SILICIUM", args.p0)
        print()
        if worst > 3.0:
            print(f"  [STOP] Les points ne ferment pas une droite ({worst:.2f} %) :")
            print("     ne rien conclure de cette page.")
        elif s_si > FULL_SERIAL - 4.0:
            print("  => AUCUNE AVANCE. Le bus ne travaille pas pendant le calcul :")
            print("     execution et fetch se serialisent. `biu_slack` doit tomber a 0,")
            print("     et `branch_taken_extra` sera a reprendre AVEC lui -- il a ete")
            print("     cale avec l'avance actuelle en place.")
        elif s_si < 12.0:
            print("  => RECOUVREMENT TOTAL. La file n'est pas la borne : le bus suit")
            print("     l'execution aussi longtemps qu'elle dure.")
        else:
            print(f"  => AVANCE BORNEE a ~{slack_si:.1f} cy par division"
                  f"  (nous : {slack_core:.1f}).")
            print(f"     Ecart du modele : {(slack_core/slack_si - 1)*100:+.0f} %.")

    print("\n=== PAGE 1 -- cout complet d'une interruption prise ===")
    print("  " + f"{'':10}" + "".join(f"{n:>7}" for n in PAGES[1]))
    s_core, _, _ = show_irq("coeur", [core[n] for n in PAGES[1]])
    if args.p1:
        if len(args.p1) != 5:
            raise SystemExit("page 1 attend 5 nombres")
        s_si, _, worst = show_irq("SILICIUM", args.p1)
        print()
        print(f"  rappel : la ROM v8 (deux cadences seulement) avait donne 111 cy.")
        if worst > 3.0:
            print(f"  [STOP] Les cinq points ne ferment pas une droite ({worst:.2f} %).")
            print("     C'est justement ce que deux cadences ne pouvaient pas montrer :")
            print("     ne rien conclure, et chercher ce qui varie avec la cadence.")
        else:
            print(f"  => {s_si:.1f} cy par interruption contre {s_core:.1f} chez nous"
                  f"  ({(s_core/s_si - 1)*100:+.0f} %).")
            print("     L'entree est deja au minimum documente (18 etats) : si l'ecart")
            print("     est significatif, il est dans ce que le stub BIOS fait, et il")
            print("     faudra une ROM qui installe SON gestionnaire pour les separer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
