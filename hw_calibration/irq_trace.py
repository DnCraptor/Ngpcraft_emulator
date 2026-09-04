#!/usr/bin/env python3
"""Trace INSTRUCTION PAR INSTRUCTION une interruption, sous les deux modeles.

But : nommer les ~28 cycles qui restent en trop dans le cout FIXE d'une IRQ quand la
file en octets est armee (139,1 cy contre 111,1 mesures sur console, ROM v18).
"""
import sys, struct
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import native

import os
ROM = ROOT / "hw_calibration" / os.environ.get("V19ROM", "a_irq_calib_v8.ngp")


def trace(queue, n_before=2, n_after=int(os.environ.get('NAFTER', '16'))):
    m = native.NativeMachine(ROM.read_bytes(), bios=(ROOT / "bios.bin").read_bytes())
    m.set_timing_silicon()
    if queue:
        m.set_queue_bytes(queue)
        m.set_muldiv_word(15, 47)
        m.set_branch_taken_extra(4)
    m.reset(bios_handoff=True)
    for _ in range(300):
        m.write(0x00B0, bytes([0]))
        m.run_frames(1)
    # ⛔ AVANCER JUSQU'A **LA BONNE** IRQ. Ce banc attrapait la premiere livraison venue
    # -- en pratique le VBlank, dont le gestionnaire BIOS fait des centaines de cycles et
    # ne rejoint JAMAIS la cartouche. On tracait donc un chemin qui n'est pas celui que
    # les ROM v8/v16/v18 mesurent. Un banc qui ne dit pas QUELLE interruption il regarde
    # ne condamne rien.
    # Critere : la livraison doit mener au gestionnaire UTILISATEUR, donc quitter le BIOS
    # dans les quelques instructions qui suivent.
    hist = []
    pend = None
    for _ in range(4_000_000):
        s, recs = m.run(1)
        if not recs:
            break
        row = (recs[0].pc, recs[0].cycles, bytes(recs[0].raw)[:recs[0].raw_len],
               m.dbg_queue() if queue else (0, 0, 0, 0))
        hist.append(row)
        if pend is not None:
            pend -= 1
            if recs[0].pc < 0xFF0000:          # on a rejoint la cartouche : c'est TI0
                break
            if pend <= 0:
                pend = None                    # fausse piste (VBlank), on continue
        if s.irq_deliveries and pend is None:
            pend = 12
            mark = len(hist) - 1
    else:
        raise SystemExit("aucune IRQ utilisateur livree")
    hist = hist[:mark + 1]
    out = hist[-n_before:]
    for _ in range(n_after):
        s, recs = m.run(1)
        if not recs:
            break
        out.append((recs[0].pc, recs[0].cycles, bytes(recs[0].raw)[:recs[0].raw_len],
                    m.dbg_queue() if queue else (0, 0, 0, 0)))
    return out


# DEUX EXECUTIONS = DEUX EVENEMENTS. Les deux modeles ne tombent pas sur la meme
# interruption ni au meme endroit ; les mettre cote a cote ligne a ligne faisait lire
# l'etat de file d'UNE execution en face du PC de l'AUTRE -- et les lignes fausses
# etaient justement celles du chemin d'interruption. Chaque modele est donc trace et
# totalise SEPAREMENT, avec ses propres PC.
def dump(label, rows):
    print("")
    print("=== " + label + " ===")
    print("      pc octets            cy | file@entree  oct  calage   aw")
    tot = 0
    for pc, cy, raw, q in rows:
        tot += cy
        print("%08X %-14s %5d | %11.2f %4d %7d %4d"
              % (pc, raw.hex(), cy, q[0] / 16.0, q[1], q[2], q[3]))
    print("   TOTAL                %5d" % tot)
    return tot


ta = dump("credit en cycles", trace(0))
tb = dump("file en octets", trace(4))
print("")
print("total credit %d   total file %d   ecart %+d   (fenetres NON identiques)"
      % (ta, tb, tb - ta))
