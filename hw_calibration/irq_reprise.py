#!/usr/bin/env python3
"""Le flot interrompu est-il plus lent EN DEHORS du chemin d'interruption ?

    python hw_calibration/irq_reprise.py [credit|file]

POURQUOI CE BANC EXISTE. La trace TI0 honnete montre que les quatre instructions du
chemin d'interruption (stub BIOS + `reti`) coutent le MEME prix sous les deux modeles,
sauf UNE : la premiere du stub, qui part file vide (+16 cy). Or le modele en octets est
~28 cy trop cher par interruption contre le silicium. ⇒ le reste est **hors du chemin**,
dans la REPRISE du flot interrompu -- ou bien il n'existe pas et c'est le montage qui
ment.

CE QU'IL MESURE, ET POURQUOI C'EST SANS HYPOTHESE. On accumule un histogramme
`PC -> (passages, cycles)` sur du code CARTOUCHE, une fois avec les interruptions
interdites (page 0 de la ROM v19) et une fois avec elles autorisees (page 1). **Les deux
pages executent la meme boucle aux memes adresses.** Une instruction qui coute plus cher
au meme PC sur la page 1 est ralentie par les interruptions SANS etre dans leur chemin.

⛔ On ne compare que les PC vus dans LES DEUX pages, et seulement ceux vus assez souvent
pour que la moyenne veuille dire quelque chose. Un PC vu 3 fois n'est pas une mesure.
"""

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hw_calibration"))

import v19_gate as G                                    # noqa: E402
from core import native                                 # noqa: E402

CART_LO, CART_HI = 0x200000, 0x3FFFFF
STEPS = 120_000
MIN_HITS = 200


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


def census(m):
    """Histogramme PC -> [passages, cycles] sur le code cartouche."""
    hist = {}
    deliveries = 0
    bios_cy = 0
    for _ in range(STEPS):
        s, recs = m.run(1)
        if not recs:
            break
        deliveries += s.irq_deliveries
        pc, cy = recs[0].pc, recs[0].cycles
        if CART_LO <= pc <= CART_HI:
            e = hist.get(pc)
            if e is None:
                hist[pc] = [1, cy]
            else:
                e[0] += 1
                e[1] += cy
        else:
            bios_cy += cy
    return hist, deliveries, bios_cy


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "file"
    queue = 0 if which == "credit" else 4
    print(f"modele : {'credit en cycles' if not queue else 'file en octets'}"
          f"   ({STEPS} instructions par page)")

    m = build(queue)
    step(m, 400)
    key = {tile(m, 1 + i, 17): str(i) for i in range(10)}
    if len(key) != 10:
        raise SystemExit("cle de chiffres illisible")

    # ⛔ LES DEUX PAGES NE TOURNENT PAS SUR LA MEME BOUCLE AU MEME MOMENT. Chaque page
    # enchaine les quatre largeurs (1/2/3/5 octets) une seconde chacune : echantillonner
    # « la page 0 » puis « la page 1 » comparait le flot de `ld XWA` d'un cote et celui
    # de `ld A` de l'autre -- aucun PC commun, et si les adresses s'etaient trouvees
    # partagees, la comparaison aurait ete SILENCIEUSEMENT fausse.
    # ⇒ On identifie la boucle sur la page 0, puis on n'accumule sur la page 1 que
    # lorsque CES PC-LA passent.
    goto_page(m, key, 0)
    step(m, 300)
    h0, d0, b0 = census(m)
    target = {pc for pc, (n, _) in h0.items() if n >= MIN_HITS}
    if not target:
        print("  [STOP] aucune boucle dominante sur la page 0.")
        return 2

    goto_page(m, key, 1)
    step(m, 300)
    h1, d1, b1 = {}, 0, 0
    for _ in range(24):                      # au plus ~24 fenetres : une seconde de jeu
        hx, dx, bx = census(m)
        d1 += dx
        b1 += bx
        for pc, (n, cy) in hx.items():
            e = h1.setdefault(pc, [0, 0])
            e[0] += n
            e[1] += cy
        if min(h1.get(pc, [0])[0] for pc in target) >= MIN_HITS:
            break

    print(f"  page 0 (IRQ interdites) : {len(h0)} PC, {d0} livraisons,"
          f" {b0} cy hors cartouche")
    print(f"  page 1 (IRQ autorisees) : {len(h1)} PC, {d1} livraisons,"
          f" {b1} cy hors cartouche")
    # ⚠️ La page 0 n'est pas a zero livraison et ne peut pas l'etre : le VBlank du BIOS
    # tombe de toute facon, sur les DEUX pages -- il est donc controle, pas parasite. Ce
    # qui doit rester vrai, c'est que TI0 domine largement sur la page 1.
    if d1 < 10 * max(d0, 1):
        print("  [STOP] la page 1 ne subit pas assez d'interruptions de plus que la 0 :")
        print("         le contraste ne vient pas de TI0.")
        return 2

    common = [pc for pc in sorted(target)
              if pc in h1 and h1[pc][0] >= MIN_HITS]
    if len(common) < len(target):
        print(f"  [STOP] {len(target) - len(common)} PC de la boucle n'ont pas ete")
        print("         assez revus sur la page 1 : la comparaison serait partielle.")
        return 2

    n0 = sum(h0[pc][0] for pc in common)
    n1 = sum(h1[pc][0] for pc in common)
    c0 = sum(h0[pc][1] for pc in common)
    c1 = sum(h1[pc][1] for pc in common)
    print("")
    print(f"  {len(common)} PC de la MEME boucle   ({n0} / {n1} passages)")
    print(f"  cout moyen HORS chemin d'IRQ : {c0/n0:6.3f} cy   ->  {c1/n1:6.3f} cy"
          f"   ({(c1/n1)/(c0/n0)-1:+.2%})")

    worst = sorted(common, key=lambda pc: -(h1[pc][1]/h1[pc][0] - h0[pc][1]/h0[pc][0]))
    print("\n  les cinq PC les plus ralentis :")
    for pc in worst[:5]:
        a, b = h0[pc][1]/h0[pc][0], h1[pc][1]/h1[pc][0]
        print(f"    {pc:08X}  {a:6.2f} -> {b:6.2f}  ({b-a:+5.2f} cy)"
              f"   [{h0[pc][0]} / {h1[pc][0]} passages]")

    extra = c1/n1 - c0/n0
    if d1:
        per_irq = extra * n1 / d1
        print(f"\n  => surcout de REPRISE : {extra:+.3f} cy par instruction hors chemin,")
        print(f"    soit {per_irq:+.1f} cy par interruption sur cette fenetre.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
