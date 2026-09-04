#!/usr/bin/env python3
"""Le cout d'une interruption, par SOMME DIRECTE, sur des centaines d'occurrences.

    python hw_calibration/irq_sum_gate.py

POURQUOI. Le depouillement de la v18 (et la page 1 de la v19) donnent le cout d'une
interruption par une DIFFERENCE de debits, normalisee par un nombre d'interruptions. Ce
banc-ci le donne autrement : en additionnant, interruption par interruption, tout ce qui
est execute entre la livraison et le retour dans le flot -- l'acceptation comprise.

⚡ LES DEUX METHODES DOIVENT TOMBER SUR LE MEME NOMBRE. Quand elles n'y tombent pas, ce
n'est pas une marge : c'est qu'une des deux mesure autre chose que ce qu'elle annonce.
Trois instruments couvrent deja le chemin sans y trouver les 25,4 cy/IRQ d'ecart entre
les deux modeles que le depouillement annonce -- seulement 16.

CE QU'ON SOMME. La boucle de travail est identifiee sur la page 0 (interruptions
interdites) : c'est l'ensemble des PC qui y passent souvent. Une interruption commence a
la livraison et finit quand le PC revient dans cet ensemble. Les cycles de l'instruction
de reprise ne sont PAS comptes : elle se serait executee de toute facon.

⚠️ `run(1)` rend dans `total_cycles` l'instruction ET le cout d'acceptation qui la suit
eventuellement ; c'est exactement ce qu'il faut, et c'est pourquoi ce banc lit
`s.total_cycles` plutot que `rec.cycles`.
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
PROBE = 60_000
STEPS = 240_000
MIN_HITS = 200
MAX_PATH = 400          # garde-fou : un chemin plus long n'est pas une IRQ TI0


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


def measure(m):
    """Somme directe : cycles entre la livraison et le retour a l'adresse de reprise.

    ⛔ L'ANCRE EST `next_pc`, PAS UN ENSEMBLE DE PC. Une premiere version identifiait la
    boucle de travail sur la page 0 puis guettait ces adresses-la sur la page 1 : les
    deux pages n'y tournent pas sur la meme largeur au meme moment, donc l'ensemble ne
    correspondait a rien et le banc a compte ZERO interruption. L'adresse ou le flot
    reprend, elle, est donnee par le coeur pour l'instruction interrompue -- elle est
    exacte et ne suppose rien du programme.
    """
    out = []
    resume = None
    acc = 0
    n = 0
    for _ in range(STEPS):
        s, recs = m.run(1)
        if not recs:
            break
        # `Summary` est neuve a chaque appel : son `total_cycles` est ce que CET appel a
        # depense -- l'instruction PLUS le cout d'acceptation qui la suit.
        spent = s.total_cycles
        r = recs[0]
        if resume is not None:
            if r.pc == resume:
                out.append((acc, n))       # la reprise elle-meme n'est pas comptee
                resume = None
            else:
                acc += spent
                n += 1
                if n > MAX_PATH:
                    resume = None
        if resume is None and s.irq_deliveries:
            # l'instruction vient de s'executer, PUIS l'interruption a ete livree : on
            # ne garde que ce que la livraison a ajoute a cet appel.
            resume = r.next_pc
            acc = spent - r.cycles
            n = 0
    return out


def main():
    print("reference : page 1 de la v19 (meme ROM, gestionnaire vide)")
    print("            credit ~115 cy/IRQ    file ~138-140 cy/IRQ")
    print()
    print(f"{'modele':20}{'IRQ':>7}{'cout moyen':>12}{'ecart-type':>12}"
          f"{'instr/chemin':>14}")
    out = {}
    for label, q in (("credit (courant)", 0), ("file 4 octets", 4)):
        m = build(q)
        step(m, 400)
        key = {tile(m, 1 + i, 17): str(i) for i in range(10)}
        if len(key) != 10:
            raise SystemExit("cle de chiffres illisible")
        goto_page(m, key, 1)
        step(m, 300)
        rows = measure(m)
        # ⛔ SEPARER TI0 DU VBLANK. Le gestionnaire VBlank du BIOS fait des centaines
        # d'instructions ; TI0 en fait QUATRE (stub + `reti`). Les melanger donnerait une
        # moyenne qui ne decrit aucune des deux.
        short = [c for c, k in rows if k <= 10]
        longs = [c for c, k in rows if k > 10]
        if len(short) < 50:
            raise SystemExit(f"{len(short)} interruptions courtes : pas une mesure")
        mean = sum(short) / len(short)
        var = sum((c - mean) ** 2 for c in short) / len(short)
        klen = sum(k for c, k in rows if k <= 10) / len(short)
        out[label] = mean
        print(f"  {label:18}{len(short):>7}{mean:>12.1f}{var ** 0.5:>12.1f}"
              f"{klen:>14.2f}"
              + (f"   [{len(longs)} chemins longs = VBlank]" if longs else ""))

    a, b = out["credit (courant)"], out["file 4 octets"]
    print()
    print(f"  SOMME DIRECTE      credit {a:6.1f}   file {b:6.1f}   ecart {b - a:+.1f}")
    print("  DEPOUILLEMENT v18  credit  113.2   file  138.6   ecart  +25.4")
    print()
    print("  Les deux methodes ne mesurent PAS la meme grandeur, et l'ecart entre")
    print("  elles est explique par une mesure independante (`irq_reprise.py`) : la")
    print("  boucle interrompue tourne PLUS VITE quand les interruptions sont")
    print("  autorisees -- -0,574 cy/instruction sous le credit, -0,224 sous la file.")
    print("  Sur ~27 instructions de boucle par interruption cela fait ~15 et ~6 cy de")
    print("  RISTOURNE, du bon signe et du bon ordre pour couvrir les 16,8 et 7,4 qui")
    print("  separent les deux colonnes.")
    print()
    print("  [!!] UNE INTERRUPTION NE PEUT PAS RENDRE LE CODE INTERROMPU MOINS CHER.")
    print("       C'est un artefact de nos deux modeles : les cycles de l'ISR")
    print("       RECHARGENT la file (ou soldent la dette) du flot interrompu, alors")
    print("       que le bus est occupe a chercher les octets de l'ISR, pas les siens.")
    print()
    print("       ! CE QUE CA NE DIT PAS. Le silicium n'est mesurable QU'EN DEBIT : ses")
    print("       111,1 cy se comparent donc a 113,2 et 138,6, pas a 130 et 146. Le")
    print("       verdict du depouillement tient. Ce que la somme directe ajoute, c'est")
    print("       que l'accord du credit REPOSE sur une ristourne de ~17 cy : otez")
    print("       l'artefact des deux modeles et c'est le credit qui devient faux.")
    print("       => la ristourne n'est pas un detail de mesure, c'est une piece du")
    print("         modele -- et la seule qui explique les ~9 cy manquants, puisque la")
    print("         file en recoit DEUX FOIS MOINS que le credit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
