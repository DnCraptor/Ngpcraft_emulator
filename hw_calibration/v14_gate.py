#!/usr/bin/env python3
"""Depouillement de la ROM v14 -- cinq pages de mesures.

    python hw_calibration/v14_gate.py                       # les predictions du coeur
    python hw_calibration/v14_gate.py --p0 141 132 141 165 165 --rasv 198
    python hw_calibration/v14_gate.py --p1 403 233 153 119   # une page a la fois

Chaque page se depouille independamment : tire ce que tu veux, donne ce que tu as.

⛔ RASV DOIT VALOIR 198. Sinon la trame n'est pas celle qu'on croit et AUCUN nombre de
cette ROM ne veut rien dire.

--------------------------------------------------------------------------------
PAGE 0 -- QUEL MODELE DE BRANCHE ?
--------------------------------------------------------------------------------
Trois ROTATIONS du meme corps de 12 octets (ld/ld/mul), donc meme travail, memes
octets, meme nombre de branches ; seul change le credit d'avance que la file a en
main au moment de la branche : ~16 cy (A), ~5 (B), ~0 (C).

⚡ C'EST LE COUPLE (A1, C1) QUI TRANCHE, et il tient en deux nombres :

    pas de vidage          A1 = C1
    vidage CONDITIONNEL    A1 baisse, C1 INTACT   (rien a jeter quand la file est vide)
    surcout INCONDITIONNEL A1 et C1 baissent ENSEMBLE

Le banc balaie `branch_flush_keep` et rend le reglage qui reproduit le MOTIF des cinq
nombres -- pas un seul ecart, le motif. Si aucun reglage ne ferme les cinq a la fois,
c'est que le vidage n'emporte pas un montant fixe mais une FRACTION du credit, et il
faudra un modele de plus : le residu le dira.

--------------------------------------------------------------------------------
PAGES 1 a 4 -- DES GRANDEURS PHYSIQUES, PAS DES NIVEAUX
--------------------------------------------------------------------------------
Chaque page fait varier UNE quantite a enveloppe constante, donc l'enveloppe de
boucle disparait dans la PENTE et il ne reste que la grandeur cherchee :

  page 1  cout d'un OCTET lu          (`fetch_wait_q4`, jamais mesure directement)
  page 2  cout d'une LECTURE RAM      (MEM = +12 %, le pire ecart du corpus)
  page 3  cout d'une DIVISION         (56 cy, cale sous un fetch de 10 cy/mot, faux)
  page 4  cout d'une MULTIPLICATION
  pages 3-4  DIV - MUL : la charge `ld WA,#imm16` est commune aux deux unites, donc
             elle se SIMPLIFIE dans la difference. Ce nombre-la ne suppose rien.
"""

import argparse
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ROM_PATH = ROOT / "hw_calibration" / "a_multi_calib_v14.ngp"
FRAME_CYCLES = 199 * 515
WINDOW = FRAME_CYCLES * 60

PAGES = {0: ("A1", "B1", "C1", "A8", "C8"),
         1: ("F4", "F8", "F12", "F16"),
         2: ("M1", "M2", "M4", "M8"),
         3: ("D1", "D2", "D4", "D8"),
         4: ("P1", "P2", "P4", "P8"),
         5: ("RASV",)}

# Ce que chaque page fait varier, et de combien, par tour de boucle.
UNITS = {1: (12, 16, 20, 24), 2: (4, 8, 12, 16), 3: (1, 2, 4, 8), 4: (1, 2, 4, 8)}
TRIPS = {1: 100, 2: 250, 3: 200, 4: 200}
BYTES_PER_LOAD = 5

J_RIGHT, J_LEFT = 0x08, 0x04


def _machine(keep, extra, flush):
    from core import native
    m = native.NativeMachine(ROM_PATH.read_bytes(),
                             bios=(ROOT / "bios.bin").read_bytes())
    m.set_timing_silicon()
    m.set_branch_flush(flush)
    if flush:
        m.set_branch_flush_keep(keep)
    if extra:
        m.set_branch_taken_extra(extra)
    m.reset(bios_handoff=True)
    return m


def _step(m, n, btn=0):
    for _ in range(n):
        m.write(0x00B0, bytes([btn]))
        m.run_frames(1)


def read_core(pages, keep=0, extra=0, flush=False):
    """Relit les nombres DANS LE PLAN de tuiles -- la ROM n'a pas de variable globale.

    ⛔ La navigation est ASSERVIE au numero de page affiche par la ROM. Un appui
    maintenu franchit plusieurs pages (le pad n'est relu qu'entre deux mesures), donc
    naviguer en aveugle fait lire les nombres d'une autre page sans le savoir --
    c'est arrive pendant la mise au point et deux pages ont ete interpretees a
    l'envers avant que le chiffre de page existe.
    """
    m = _machine(keep, extra, flush)
    _step(m, 400)

    def tile(c, r):
        return struct.unpack("<H", m.read(0x9000 + (r * 32 + c) * 2, 2))[0] & 0x01FF

    key = {tile(1 + i, 17): str(i) for i in range(10)}
    if len(key) != 10:
        raise SystemExit("cle de chiffres illisible : la ROM n'a pas fini de demarrer")

    out = {}
    for pg in sorted(pages):
        # ⛔ BIDIRECTIONNELLE. Le pad n'est relu qu'entre deux mesures : des que les
        # blocs sont courts, un appui maintenu franchit PLUSIEURS pages et depasse la
        # cible sans retour possible en n'appuyant que sur DROITE.
        for _ in range(60):
            cur = key.get(tile(11, 1))
            if cur == str(pg):
                break
            _step(m, 20, J_RIGHT if (cur is None or int(cur) < pg) else J_LEFT)
            _step(m, 20, 0)
        else:
            raise SystemExit(f"page {pg} inatteignable")
        _step(m, len(PAGES[pg]) * 60 + 260)
        for i, name in enumerate(PAGES[pg]):
            s = "".join(key.get(tile(12 + j, 3 + i), " ") for j in range(5)).strip()
            out[name] = int(s) if s.isdigit() else None
    return out


def cycles(count):
    return WINDOW / float(count)


def slope_per_unit(counts, units, trips):
    """Cout d'UNE unite supplementaire, par tour de boucle. Moindres carres."""
    xs = [float(u) for u in units]
    ys = [cycles(c) / trips for c in counts]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    inter = my - slope * mx
    worst = max(abs(y - (inter + slope * x)) / y * 100.0 for x, y in zip(xs, ys))
    return slope, inter, worst


def show_page0(si, models):
    print("\n=== PAGE 0 -- quel modele de branche ? ===")
    names = PAGES[0]
    print("  " + f"{'':10}" + "".join(f"{n:>7}" for n in names))
    for label, vals in models:
        print(f"  {label:<10}" + "".join(f"{vals[n]:>7}" for n in names))
    if si is None:
        return
    print(f"  {'SILICIUM':<10}" + "".join(f"{v:>7}" for v in si))

    a1, b1, c1, a8, c8 = si
    print(f"\n  A1 - C1 = {a1 - c1:+d}   (branche file pleine contre file vide, "
          f"640 branches)")
    print(f"  A8 - C8 = {a8 - c8:+d}   (le meme, 80 branches)")

    # [!] ON NOTE LA FORME, PAS LE NIVEAU. La premiere version comparait les cinq
    # nombres bruts : le tir du 27/08 etant 12 % plus lent que TOUS nos modeles sur
    # cette page, l ecart quadratique etait domine par ce decalage commun et la porte
    # a elu `extra=6` -- le seul modele qui baissait tout -- alors que sa FORME est
    # justement celle que le silicium contredit. Un modele de branche ne se juge que
    # sur les ECARTS ENTRE ROTATIONS, qui sont insensibles au niveau.
    def shape(v):
        a1, b1, c1, a8, c8 = ([v[n] for n in names] if isinstance(v, dict) else list(v))
        return ((cycles(a1) - cycles(c1)) / 640.0,
                (cycles(b1) - cycles(c1)) / 640.0,
                (cycles(a8) - cycles(c8)) / 80.0)

    def triple(v):
        return [v[n] for n in names] if isinstance(v, dict) else list(v)

    si_shape = shape(si)
    print()
    print("  Cout par branche prise, RELATIF a la rotation C (file vide) :")
    print(f"    {chr(32)*10}{chr(65)+chr(49)+chr(45)+chr(67)+chr(49):>9}"
          f"{chr(66)+chr(49)+chr(45)+chr(67)+chr(49):>9}"
          f"{chr(65)+chr(56)+chr(45)+chr(67)+chr(56):>9}   la plus lente")
    for label, v in list(models) + [("SILICIUM", si)]:
        d = shape(v)
        a1, b1, c1 = triple(v)[:3]
        slowest = min((a1, "A"), (b1, "B"), (c1, "C"))[1]
        print(f"    {label:10}{d[0]:9.2f}{d[1]:9.2f}{d[2]:9.2f}        {slowest}")

    print()
    print("  [!] A8-C8 ne porte que 80 branches : deux comptes d ecart y valent"
          " ~6 cy/branche,")
    print("      donc un jitter de +-1 le rend inexploitable. C est A1-C1 qui porte.")

    best = None
    print()
    for label, v in models:
        d = shape(v)
        err = ((d[0] - si_shape[0]) ** 2 + (d[1] - si_shape[1]) ** 2) ** 0.5
        if best is None or err < best[1]:
            best = (label, err)
        print(f"    {label:<10} ecart de forme {err:5.2f} cy/branche")
    print()
    print(f"  => forme la plus proche : {best[0]}  ({best[1]:.2f} cy/branche)")

    a1, b1, c1 = triple(si)[:3]
    if a1 < c1 and b1 >= c1 - 1:
        print()
        print("  => VIDAGE CONDITIONNEL CONFIRME. La rotation A -- branche prise file")
        print(f"     PLEINE -- coute {si_shape[0]:.2f} cy/branche de plus que C, file")
        print("     VIDE, a instructions et branches IDENTIQUES. Et B ~ C : des qu une")
        print("     charge a consomme le credit, il n y a deja plus rien a jeter.")
    if best[1] > 2.0:
        print()
        print("  [STOP] Aucun de nos reglages ne reproduit la FORME. Regarde quelle")
        print("     rotation est la plus lente : si le silicium dit A et nous disons B,")
        print("     le desaccord n est pas dans le vidage mais dans la facon dont le")
        print("     credit se sature d une instruction a l autre. Ne pas caler un")
        print("     reglage de branche par-dessus ce desaccord-la.")

def show_slope_page(pg, title, unit_name, si, core, per=1.0):
    print(f"\n=== PAGE {pg} -- {title} ===")
    names = PAGES[pg]
    s_core, i_core, w_core = slope_per_unit([core[n] for n in names], UNITS[pg], TRIPS[pg])
    print(f"  coeur     " + "".join(f"{core[n]:>7}" for n in names)
          + f"   {s_core / per:7.2f} cy/{unit_name}  (ecart droite {w_core:.2f} %)")
    if si is None:
        return
    s_si, i_si, w_si = slope_per_unit(si, UNITS[pg], TRIPS[pg])
    print(f"  SILICIUM  " + "".join(f"{v:>7}" for v in si)
          + f"   {s_si / per:7.2f} cy/{unit_name}  (ecart droite {w_si:.2f} %)")
    if w_si > 3.0:
        print(f"  [STOP] Les points silicium ne ferment pas une droite ({w_si:.2f} %) :")
        print("     ne rien conclure de cette page.")
        return None
    print(f"  => ecart du modele : {(s_core / s_si - 1.0) * 100:+.1f} %")
    return s_si / per


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    for pg in range(5):
        ap.add_argument(f"--p{pg}", type=int, nargs="*", default=None,
                        metavar="N", help=f"tir silicium page {pg} : "
                                          + " ".join(PAGES[pg]))
    ap.add_argument("--rasv", type=int, default=None)
    ap.add_argument("--keep", type=int, nargs="*", default=[4, 6, 8],
                    help="reglages de branch_flush_keep a comparer")
    args = ap.parse_args()

    si = {pg: getattr(args, f"p{pg}") for pg in range(5)}
    for pg, v in si.items():
        if v is not None and len(v) != len(PAGES[pg]):
            raise SystemExit(f"page {pg} attend {len(PAGES[pg])} nombres : "
                             + " ".join(PAGES[pg]))

    if args.rasv is not None and args.rasv != 198:
        print(f"[STOP] RASV = {args.rasv}, attendu 198. La trame n'est pas celle qu'on")
        print("   croit : aucun nombre de cette ROM ne veut rien dire.")
        return 2

    wanted = {pg for pg, v in si.items() if v is not None} or set(range(5))
    print(f"ROM  {ROM_PATH.name}")

    core_off = read_core(wanted)
    models = [("desarme", core_off)]
    if 0 in wanted:
        for k in args.keep:
            models.append((f"keep={k}", read_core({0}, keep=k, flush=True)))
        models.append(("extra=6", read_core({0}, extra=6)))
        show_page0(si[0], models)

    for pg, title, unit, per in (
            (1, "cout d'un OCTET lu", "octet", float(BYTES_PER_LOAD)),
            (2, "cout d'une LECTURE RAM", "lecture", 1.0),
            (3, "cout d'une DIVISION (charge comprise)", "div", 1.0),
            (4, "cout d'une MULTIPLICATION (charge comprise)", "mul", 1.0)):
        if pg in wanted:
            show_slope_page(pg, title, unit, si[pg], core_off, per)

    if si[3] and si[4]:
        d, _, _ = slope_per_unit(si[3], UNITS[3], TRIPS[3])
        p, _, _ = slope_per_unit(si[4], UNITS[4], TRIPS[4])
        dc, _, _ = slope_per_unit([core_off[n] for n in PAGES[3]], UNITS[3], TRIPS[3])
        pc, _, _ = slope_per_unit([core_off[n] for n in PAGES[4]], UNITS[4], TRIPS[4])
        print("\n=== DIV - MUL (la charge se simplifie : ce nombre ne suppose rien) ===")
        print(f"  silicium {d - p:7.2f} cy       coeur {dc - pc:7.2f} cy"
              f"       ecart {(dc - pc) - (d - p):+.2f} cy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
