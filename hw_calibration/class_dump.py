#!/usr/bin/env python3
"""Dumper une iteration de la boucle chaude d'une classe du corpus, instruction par
instruction, avec ce que NOUS facturons -- et de quoi le confronter a l'annexe B.

    python hw_calibration/class_dump.py            # la ROM v2, classe la plus chaude
    python hw_calibration/class_dump.py 8          # la ligne 8 de la v2 (`MEM`)

POURQUOI. Le tir v19 a etabli que le chemin d'une interruption vaut les 110 cy de
l'annexe B et que nos `data_access_cycles` / `branch_taken_extra` y facturent DEUX FOIS ce
que les etats tabules contiennent. Mais la case `MEM` du corpus EXIGE ces 4 cy par acces
(+12,1 % sans, -0,4 % avec). Deux mesures silicium qui se contredisent tant qu'on leur
applique la meme regle.

⚠️ ET LA v2 NE MESURE PAS CE QU'ON CROYAIT. Ses locales sont `volatile` : `v = w` fait
donc DEJA une lecture et une ecriture en RAM, exactement comme `*(u8*)0x4200 = v`.
⇒ `BASE` et `MEM` ont le MEME nombre d'acces. Un cout par acces les decale tous les deux ;
il n'isole rien, et il a ete cale sur le NIVEAU de `MEM`, pas sur un acces.

CE BANC NE CONCLUT PAS, IL MONTRE : il faut lire les instructions reellement emises pour
savoir ce que la table doit leur donner. Les colonnes `avec`/`sans` disent ou passent les
4 cy par acces, ligne par ligne.
"""

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HW = ROOT / "hw_calibration"
ROM = HW / "a_cpu_calib_v2.ngc"
CART_LO, CART_HI = 0x200000, 0x3FFFFF
WARM = 240
PROBE = 40_000


def build(dac=None):
    from core import native
    m = native.NativeMachine(ROM.read_bytes(), bios=(ROOT / "bios.bin").read_bytes())
    m.set_timing_silicon()
    if dac is not None:
        m.set_data_access_cycles(dac)
    m.reset(bios_handoff=True)
    for _ in range(WARM):
        m.write(0x00B0, bytes([0]))
        m.run_frames(1)
    return m


def hot_loop(m):
    """Les PC de la boucle la plus chaude, et l'ordre dans lequel ils passent."""
    seq = []
    hist = {}
    for _ in range(PROBE):
        s, recs = m.run(1)
        if not recs:
            break
        pc, cy = recs[0].pc, recs[0].cycles
        if CART_LO <= pc <= CART_HI:
            seq.append((pc, cy, bytes(recs[0].raw)[:recs[0].raw_len]))
            e = hist.get(pc)
            if e is None:
                hist[pc] = [1, cy]
            else:
                e[0] += 1
                e[1] += cy
    if not hist:
        raise SystemExit("aucune instruction cartouche vue")
    top = max(hist.values(), key=lambda e: e[0])[0]
    body = {pc for pc, e in hist.items() if e[0] >= top * 0.8}
    return seq, body, hist


def one_iteration(seq, body, skip=8):
    """Une iteration EN REGIME STATIONNAIRE de la boucle chaude.

    ⛔ ET C'EST TOUT LE POINT DE `skip`. Une premiere version rendait la PREMIERE tranche
    trouvee -- souvent l'iteration d'ENTREE, ou la file d'instructions n'a pas encore son
    remplissage de croisiere. Elle m'a fait annoncer DEUX FOIS que « la meme boucle coute
    46 cycles a une adresse impaire et 44 a une paire », donc que le modele restait
    sensible a l'alignement. C'etait faux : en regime stationnaire les deux valent 44,
    au cycle pres, avec la meme dette d'entree (-2). L'entree, elle, coute bien 2 de plus
    -- une fois par bloc, ce qui ne se voit dans aucune mesure.

    ⚖️ Une boucle ne se lit pas sur son premier tour. On saute `skip` iterations.
    """
    best = []
    cur = []
    seen = set()
    passes = 0
    for row in seq:
        if row[0] not in body:
            cur, seen = [], set()
            continue
        if row[0] in seen:
            passes += 1
            if passes > skip and len(cur) > len(best):
                best = cur
            cur, seen = [], set()
        cur.append(row)
        seen.add(row[0])
    if not best:
        raise SystemExit(f"moins de {skip} iterations vues : pas de regime stationnaire")
    return best


def main():
    m_on = build(None)
    seq_on, body, hist_on = hot_loop(m_on)
    it = one_iteration(seq_on, body)
    if not it:
        raise SystemExit("boucle non isolee")

    m_off = build(0)
    seq_off, body_off, hist_off = hot_loop(m_off)

    print(f"ROM {ROM.name} -- boucle chaude, {len(it)} instructions par tour")
    print(f"{'pc':>8}  {'octets':<14}{'avec dac=4':>11}{'sans dac':>10}{'ecart':>7}")
    tot_on = tot_off = 0
    for pc, cy, raw in it:
        e_off = hist_off.get(pc)
        cy_off = (e_off[1] / e_off[0]) if e_off else float("nan")
        tot_on += cy
        tot_off += cy_off
        print(f"{pc:08X}  {raw.hex():<14}{cy:>11}{cy_off:>10.2f}{cy - cy_off:>7.2f}")
    print(f"{'TOTAL':>8}  {'':<14}{tot_on:>11}{tot_off:>10.2f}{tot_on - tot_off:>7.2f}")
    print()
    print("  => les lignes a ecart non nul sont celles ou nous ajoutons un cout d'acces")
    print("     PAR-DESSUS les etats de l'annexe B. Confronter chacune a sa ligne de")
    print("     table (1)/(4)/(9)/(10) avant de decider quelle classe le porte deja.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
