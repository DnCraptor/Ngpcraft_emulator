#!/usr/bin/env python3
"""Depouillement de la ROM v13 -- le vidage de file a la branche prise.

    python hw_calibration/flush_gate.py                 # les deux predictions du coeur
    python hw_calibration/flush_gate.py 141 155 163 167 # + le tir silicium

CE QUE LE SCRIPT MESURE. Les quatre blocs de la ROM font le MEME travail (640
unites de corps) avec 640 / 320 / 160 / 80 branches prises. Le cout d'un bloc
vaut donc `ordonnee + pente x nombre_de_branches`, et c'est la PENTE -- le cout
d'une branche prise -- qui repond a la question. L'ordonnee absorbe tout le cout
du travail : une erreur sur `mul` ou sur `ld` deplace l'ordonnee et laisse la
pente intacte. C'est pour ca que la ROM lit une pente et pas des niveaux.

⛔ LES REFERENCES NE SONT PAS EN DUR. Les deux predictions sont recalculees en
faisant tourner le coeur sur la MEME ROM, drapeau desarme puis arme. Une valeur
figee dans un script se perime en silence a la premiere recalibration ; celle-ci
ne peut pas.
"""

import argparse
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ROM_PATH = ROOT / "hw_calibration" / "a_flush_calib_v13.ngp"
ROM_MD5 = "c535123425392f9c4ae163e692f2f421"

# 199 lignes x 515 clocks x 60 trames -- la base de temps que la ROM compte.
FRAME_CYCLES = 199 * 515
WINDOW_CYCLES = FRAME_CYCLES * 60

# Branches prises par bloc, dans l'ordre u1, u2, u4, u8.
BRANCHES = (640, 320, 160, 80)

MAP1 = 0x9000          # plan de tuiles SCR1
KEY_ROW = 13           # ligne "0123456789" ecrite par la ROM
RESULT_ROWS = (3, 4, 5, 6)
RESULT_COL = 12


def fit_slope(counts):
    """Moindres carres de (cout du bloc) contre (nombre de branches).

    Rend (pente cy/branche, ordonnee, ecart max des points a la droite en %).
    Le residu est la garde : quatre points qui ne ferment pas une droite veulent
    dire que le montage a bouge, pas que la pente est interessante.
    """
    xs = [float(b) for b in BRANCHES]
    ys = [WINDOW_CYCLES / float(c) for c in counts]
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    inter = my - slope * mx
    worst = max(abs(y - (inter + slope * x)) / y * 100.0 for x, y in zip(xs, ys))
    return slope, inter, worst


def read_core(flush, keep=0, frames=760):
    """Fait tourner le coeur sur la ROM et relit les nombres DANS LE PLAN.

    La ROM n'a pas de variable globale (cf. l'en-tete de cpu_calib_v13.c : c'est
    ce motif qui faisait planter la v6 sur console). On lit donc l'ecran, en se
    servant de la ligne-cle "0123456789" que la ROM ecrit pour cela -- la table
    tuile->chiffre vient de la ROM elle-meme, jamais d'une supposition sur la
    fonte du BIOS.
    """
    from core import native

    bios_path = ROOT / "bios.bin"
    machine = native.NativeMachine(ROM_PATH.read_bytes(), bios=bios_path.read_bytes())
    machine.set_timing_silicon()
    machine.set_branch_flush(flush)
    if flush:
        machine.set_branch_flush_keep(keep)
    machine.reset(bios_handoff=True)
    summary = None
    for _ in range(frames):
        machine.write(0x00B0, bytes([0]))
        summary = machine.run_frames(1)

    def tile(col, row):
        addr = MAP1 + (row * 32 + col) * 2
        return struct.unpack("<H", machine.read(addr, 2))[0] & 0x01FF

    key = {tile(1 + i, KEY_ROW): str(i) for i in range(10)}
    if len(key) != 10:
        raise SystemExit("la ligne-cle n'a pas 10 tuiles distinctes : la ROM n'a "
                         "pas fini de tourner, ou le plan a bouge")

    def number(row):
        text = "".join(key.get(tile(RESULT_COL + i, row), " ") for i in range(5))
        return int(text.strip())

    counts = [number(r) for r in RESULT_ROWS]
    rasv = "".join(key.get(tile(RESULT_COL + i, 8), " ") for i in range(3)).strip()
    return counts, int(rasv), native.status_name(summary.stop_status)


def show(label, counts, slope, inter, worst, extra=""):
    nums = " ".join(f"{c:>5}" for c in counts)
    print(f"  {label:<26} {nums}   pente {slope:6.1f} cy/branche"
          f"   (ordonnee {inter:8.0f}, ecart max {worst:.2f} %){extra}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("silicon", nargs="*", type=int, metavar="N",
                    help="les quatre nombres lus sur console (u1 u2 u4 u8)")
    ap.add_argument("--rasv", type=int, default=None,
                    help="RAS.V lu sur console ; doit valoir 198")
    args = ap.parse_args()

    print(f"ROM  {ROM_PATH.name}  md5 attendu {ROM_MD5}")
    print(f"branches prises par bloc : {BRANCHES}\n")

    refs = {}
    for flag, label in ((False, "coeur, drapeau DESARME"), (True, "coeur, drapeau ARME")):
        counts, rasv, status = read_core(flag)
        slope, inter, worst = fit_slope(counts)
        refs[flag] = slope
        show(label, counts, slope, inter, worst, f"  [RASV {rasv}, {status}]")

    off, on = refs[False], refs[True]
    print(f"\n  ecart entre les deux modeles : {off:.1f} contre {on:.1f} cy/branche "
          f"(facteur {on / off:.2f})")

    if not args.silicon:
        print("\nPas de tir silicium fourni. Flashe la ROM, note les cinq nombres,\n"
              "puis relance :  python hw_calibration/flush_gate.py u1 u2 u4 u8 --rasv 198")
        return 0

    if len(args.silicon) != 4:
        raise SystemExit("il faut exactement quatre nombres : u1 u2 u4 u8")

    print()
    slope, inter, worst = fit_slope(args.silicon)
    show("SILICIUM", args.silicon, slope, inter, worst)

    print()
    if args.rasv is not None and args.rasv != 198:
        print(f"  [STOP] RASV = {args.rasv}, attendu 198. La trame n'est pas celle qu'on\n"
              "     croit : les quatre nombres ne veulent rien dire. Ne rien conclure.")
        return 2
    if worst > 2.0:
        print(f"  [STOP] Les quatre points ne ferment pas une droite (ecart {worst:.2f} %).\n"
              "     NE RIEN CONCLURE -- chercher d'abord ce qui a bouge dans le montage.\n"
              "     Un point isole qui contredit le modele est SUSPECT avant d'etre\n"
              "     revelateur (lecon v10 : le BASE = 281 que trois ROM n'ont jamais revu).")
        return 2

    # ⛔ PAS DE VERDICT BINAIRE. La premiere version de cette porte comparait la
    # pente a la mediane des deux reglages extremes et concluait toujours -- si bien
    # que le tir du 27/08, tombe pile au milieu, basculait d'un verdict a l'autre
    # selon le cote du jitter (17,5 -> "pas de vidage", 18,8 -> "vidage"). La regle
    # ecrite dans l'en-tete de la ROM disait pourtant "pente entre les deux => ne
    # rien conclure" ; elle n'etait pas implementee. Elle l'est maintenant, et la
    # porte rend le CREDIT CONSERVE plutot qu'un oui/non.
    if slope < off * 0.95:
        print(f"  [STOP] Pente {slope:.1f}, SOUS le reglage le plus rapide ({off:.1f}).")
        print("     Hors du domaine encadre par le modele : ne rien conclure.")
        return 2
    if slope > on * 1.05:
        print(f"  [STOP] Pente {slope:.1f}, AU-DESSUS du vidage total ({on:.1f}).")
        print("     Hors du domaine encadre par le modele : ne rien conclure.")
        return 2

    # Balayage du credit conserve : quel reglage reproduit la pente mesuree ?
    best = None
    for keep in range(0, 20, 2):
        counts, _, _ = read_core(True, keep)
        s_k, _, _ = fit_slope(counts)
        if best is None or abs(s_k - slope) < abs(best[1] - slope):
            best = (keep, s_k)
    keep, s_k = best

    band = abs(slope - off) / (on - off)
    print(f"  Credit d'avance conserve par une branche prise : ~{keep} cycles"
          f" (pente modelisee {s_k:.1f} contre {slope:.1f} mesuree).")
    print(f"  Position dans le domaine : {band * 100:.0f} % du chemin entre le"
          " vidage nul et le vidage total.")
    print()

    if band < 0.15:
        print("  => PAS DE VIDAGE. La branche prise ne coute que sa ligne de table.")
    elif band > 0.85:
        print("  => VIDAGE TOTAL. La file entiere est perdue a chaque branche prise.")
    else:
        print("  => NI L'UN NI L'AUTRE, ET C'EST LE RESULTAT.")
        print(f"     Une branche prise coute REELLEMENT plus que sa ligne de table"
              f" (+{slope - off:.1f} cy),")
        print("     mais environ la moitie d'un vidage complet. Les deux reglages")
        print("     extremes sont faux tous les deux : le drapeau booleen a la")
        print("     mauvaise FORME.")
        print()
        print("  [STOP] NE PAS ARMER SUR CE SEUL TIR. La valeur reproduit CETTE ROM,")
        print("     elle n'est pas derivee -- un mot de file vaudrait ~8 cy et donne")
        print("     une pente hors bande. Avant d'armer : rejouer les ROM v1-v12")
        print("     contre leurs tirs silicium enregistres, sinon c'est un nombre cale")
        print("     sur une seule boucle, exactement ce que la v2 avait attrape sur")
        print("     cart_data_wait.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
