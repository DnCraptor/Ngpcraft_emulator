#!/usr/bin/env python3
"""Combien d'interruptions sont REELLEMENT livrees par trame ?

    python hw_calibration/irq_count_gate.py

POURQUOI. `v16_gate` / `v18_gate` divisent par une constante -- `IRQ_PER_WINDOW =
60 x 152`, « une impulsion TI0 par ligne » -- pour passer d'un nombre de blocs a un cout
par interruption. **C'est une HYPOTHESE, pas une mesure.** Si le compte reel n'est pas
152, ou s'il n'est pas le MEME sous les deux modeles de recouvrement, alors le cout par
interruption qu'ils annoncent est faux d'autant, et la comparaison des deux modeles l'est
aussi.

⚠️ CE QUI REND LA QUESTION SERIEUSE. `irq_pending` est un BIT : deux impulsions qui
arrivent avant que la premiere ne soit servie n'en font qu'une. Un modele plus LENT en
perd donc davantage -- ce qui fait baisser le cout par interruption qu'on lui attribue
sans que le modele y soit pour rien. Exactement le genre de biais qui se lit comme un
defaut du modele.

⇒ On ne deduit pas : on compte. `Summary.irq_deliveries` est renseigne par le coeur.
La page 0 de la ROM v19 (interruptions INTERDITES) donne le bruit de fond (VBlank), la
page 1 le total ; la difference est TI0.
"""

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hw_calibration"))

import v19_gate as G                                    # noqa: E402
from core import native                                 # noqa: E402

FRAMES = 120


def build(queue):
    m = native.NativeMachine(G.ROM_PATH.read_bytes(),
                             bios=(ROOT / "bios.bin").read_bytes())
    m.set_timing_silicon()
    if queue:
        m.set_queue_bytes(queue)
        m.set_muldiv_word(15, 47)
    m.reset(bios_handoff=True)
    return m


def step(m, n, b=0):
    for _ in range(n):
        m.write(0x00B0, bytes([b]))
        m.run_frames(1)


def tile(m, c, r):
    return struct.unpack("<H", m.read(0x9000 + (r * 32 + c) * 2, 2))[0] & 0x01FF


def goto_page(m, key, pg):
    for _ in range(60):
        cur = key.get(tile(m, 11, 1))
        if cur == str(pg):
            return
        step(m, 20, G.J_RIGHT if (cur is None or int(cur) < pg) else G.J_LEFT)
        step(m, 20, 0)
    raise SystemExit(f"page {pg} inatteignable")


def count(m):
    """Livraisons par trame, sur FRAMES trames."""
    total = 0
    for _ in range(FRAMES):
        m.write(0x00B0, bytes([0]))
        s = m.run_frames(1)
        total += s.irq_deliveries
    return total / FRAMES


def main():
    print("hypothese du depouillement : 152,0 interruptions TI0 par trame")
    print(f"{'modele':20}{'page 0':>10}{'page 1':>10}{'TI0':>10}")
    got = {}
    for label, q in (("credit (courant)", 0), ("file 4 octets", 4)):
        m = build(q)
        step(m, 400)
        key = {tile(m, 1 + i, 17): str(i) for i in range(10)}
        if len(key) != 10:
            raise SystemExit("cle de chiffres illisible")
        goto_page(m, key, 0)
        step(m, 120)
        c0 = count(m)
        goto_page(m, key, 1)
        step(m, 120)
        c1 = count(m)
        got[label] = c1 - c0
        print(f"  {label:18}{c0:10.2f}{c1:10.2f}{c1 - c0:10.2f}")

    a, b = got["credit (courant)"], got["file 4 octets"]
    print()
    if abs(a - 152.0) > 3 or abs(b - 152.0) > 3:
        print("  [!] Le compte reel n'est PAS 152 : le cout par interruption annonce par")
        print("      v16_gate / v18_gate est faux du meme rapport.")
    if abs(a - b) > 1.0:
        print(f"  [!!] ET IL DIFFERE ENTRE LES DEUX MODELES ({a:.2f} contre {b:.2f}) :")
        print("       comparer leurs couts par interruption revient a diviser deux")
        print("       mesures par deux constantes differentes en les croyant egales.")
        print(f"       Correction a appliquer au modele en octets : x{a / b:.4f}")
    else:
        print("  Les deux modeles livrent le meme nombre d'interruptions : la")
        print("  normalisation du depouillement n'est pas la source de l'ecart.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
