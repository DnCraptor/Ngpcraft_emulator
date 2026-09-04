#!/usr/bin/env python3
"""Rejoue les ROM de calibration contre leurs tirs silicium enregistres.

    python hw_calibration/corpus_gate.py                # desarme vs arme keep=6
    python hw_calibration/corpus_gate.py --keep 4 6 8    # balaye plusieurs reglages

POURQUOI CE BANC EXISTE. La ROM v13 a mesure qu'une branche prise coute ~+6 cycles de
plus que sa ligne de table, et `branch_flush_keep` reproduit ce chiffre. Mais un nombre
qui reproduit UNE boucle n'est pas un nombre juste : c'est exactement ce que la v2 avait
attrape sur `cart_data_wait = 5`, cale sur Cool Boarders et refute des qu'on l'a mesure.
⇒ On n'arme rien avant d'avoir rejoue tout le corpus.

⚠️ LA ROM QUI COMPTE LE PLUS EST v11. Ses quatre boucles sont de tailles differentes,
donc de DENSITE DE BRANCHES differente -- c'est le meme axe que la v13, mesure
independamment. Si un reglage ameliore la v13 et degrade v11, il est faux.

LECTURE DES NOMBRES A L'ECRAN. Toutes ces ROM ecrivent leurs valeurs en colonne 12 avec
`PrintDecimal`. La table tuile->chiffre est extraite de la ligne-cle "0123456789" de la
v13, puis reutilisee : meme fonte BIOS, meme routine. ⛔ Le controle qui valide cette
reutilisation est la v12, dont les quatre cases doivent retomber sur ~682 ; si elle sort
autre chose, la table est fausse et TOUT le tableau est a jeter.
"""

import argparse
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HW = ROOT / "hw_calibration"
MAP1 = 0x9000
VAL_COL = 12

# Tirs silicium enregistres (hw_calibration/README.md). `None` = case volontairement
# exclue de la note, avec sa raison.
CORPUS = [
    dict(name="v2  classes", rom="a_cpu_calib_v2.ngc", blocks=10,
         rows={"BASE": 3, "SHIFT": 4, "ADD": 5, "MUL": 6, "DIV": 7,
               "MEM": 8, "CSEQ": 9, "CRND": 10, "RRND": 11},
         silicon={"BASE": 682, "SHIFT": 538, "ADD": 578, "MUL": 444, "DIV": 265,
                  "MEM": 471, "CSEQ": 270, "CRND": 252, "RRND": 252}),
    dict(name="v11 tailles", rom="a_droite_calib_v11.ngp", blocks=5,
         rows={"L1(5 mots)": 3, "L2(14)": 4, "L3(35)": 5, "L4(53)": 6},
         silicon={"L1(5 mots)": 678, "L2(14)": 261, "L3(35)": 107, "L4(53)": 71}),
    dict(name="v12 adresses", rom="a_align_calib_v12.ngp", blocks=5,
         rows={"A1": 3, "A2": 4, "A3": 5, "A4": 6},
         silicon={"A1": 682, "A2": 682, "A3": 683, "A4": 682}),
    dict(name="v10 pont", rom="a_pont_calib_v10.ngp", blocks=8,
         rows={"REF": 3, "SHIFT": 5, "ADD": 6, "MUL": 7, "DIV": 8, "MEM": 9},
         silicon={"REF": 261, "SHIFT": 538, "ADD": 578, "MUL": 444, "DIV": 266,
                  "MEM": 471}),
    # ⛔ v10 `BASE` (ligne 4) est DELIBEREMENT absente : le 281 du tir du 24/08 est
    # l'anomalie que trois ROM suivantes n'ont jamais reproduite (~680 partout). La
    # noter reviendrait a calibrer sur un point suspect.
    dict(name="v8  irq", rom="a_irq_calib_v8.ngp", blocks=4,
         rows={"WORK0": 3, "WORK1": 4, "WORK4": 5},
         silicon={"WORK0": 261, "WORK1": 218, "WORK4": 249}),
]


def digit_map(machine_factory):
    """Table tuile->chiffre, extraite de la ligne-cle de la v13."""
    m = machine_factory(HW / "a_flush_calib_v13.ngp", 400)
    key = {}
    for i in range(10):
        a = MAP1 + (13 * 32 + (1 + i)) * 2
        key[struct.unpack("<H", m.read(a, 2))[0] & 0x01FF] = str(i)
    if len(key) != 10:
        raise SystemExit("table de chiffres incomplete : la v13 n'a pas fini de tourner")
    return key


def make_runner(flush, keep, extra=None, q16=0, dw=0, slack=None, word=None):
    from core import native

    bios = (ROOT / "bios.bin").read_bytes()

    def run(rom_path, frames):
        m = native.NativeMachine(rom_path.read_bytes(), bios=bios)
        m.set_timing_silicon()
        m.set_branch_flush(flush)
        if flush:
            m.set_branch_flush_keep(keep)
        # ⛔ None = LAISSER LE DEFAUT, 0 = DESARMER. `if extra:` confondait les deux et
        # rendait `--with-extra 0` silencieusement identique a `--with-extra 4` : deux
        # colonnes du tableau sortaient au chiffre pres pareilles sans que rien ne le
        # signale. Meme defaut que celui deja corrige sur `--dw`, reste sur cette option.
        if extra is not None:
            m.set_branch_taken_extra(extra)
        if q16 and q16 > 1:
            m.set_fetch_wait_byte_q16(q16)
        elif q16 == -1:
            # Le modele d'AVANT la campagne v14 : fetch par mot, branche non facturee.
            # (Les couts MUL/DIV octet, eux, sont dans le coeur et ne se desarment pas.)
            m.set_fetch_wait_byte_q16(0)
            m.set_branch_taken_extra(0)
        elif q16:
            m.set_fetch_wait_byte_q16(q16)
        # ⛔ -1 = DESARMER. `if dw:` sautait la valeur 0 et laissait le defaut en
        # place : deux colonnes du tableau sortaient identiques sans que rien ne le
        # signale. Une option qui ne peut pas exprimer « zero » ment en silence.
        if dw:
            m.set_data_access_cycles(0 if dw < 0 else dw)
        if slack is not None:
            # reutilise pour balayer la TAILLE DE FILE depuis que le modele est en octets
            m.set_queue_bytes(slack)
        if word is not None:
            m.set_muldiv_word(word[0], word[1])
        m.reset(bios_handoff=True)
        for _ in range(frames):
            m.write(0x00B0, bytes([0]))
            m.run_frames(1)
        return m

    return run


def read_rom(run, key, entry):
    frames = entry["blocks"] * 60 + 240
    m = run(HW / entry["rom"], frames)
    out = {}
    for label, row in entry["rows"].items():
        txt = ""
        for i in range(5):
            a = MAP1 + (row * 32 + VAL_COL + i) * 2
            txt += key.get(struct.unpack("<H", m.read(a, 2))[0] & 0x01FF, " ")
        txt = txt.strip()
        out[label] = int(txt) if txt.isdigit() else None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", type=int, nargs="*", default=[6],
                    help="reglages de branch_flush_keep a evaluer (defaut 6)")
    ap.add_argument("--extra", type=int, nargs="*", default=[],
                    help="reglages de branch_taken_extra a evaluer")
    ap.add_argument("--q16", type=int, nargs="*", default=[],
                    help="fetch par OCTET, en seiziemes ; combine avec --with-extra")
    ap.add_argument("--with-extra", type=int, nargs="*", default=[0],
                    help="surcharges de branche a croiser avec --q16")
    ap.add_argument("--dw", type=int, nargs="*", default=[0],
                    help="data_access_cycles a croiser avec --q16")
    ap.add_argument("--slack", type=int, nargs="*", default=[None],
                    help="taille de file en OCTETS a croiser avec --with-extra")
    ap.add_argument("--word", action="store_true",
                    help="armer aussi les couts MOT mesures par la v17 (15 / 47)")
    args = ap.parse_args()

    settings = ([("modele", False, 0, None, 0),
                 ("avant v14", False, 0, None, (-1, 0))]
                + [(f"keep={k}", True, k, None, 0) for k in args.keep]
                + [(f"extra={e}", False, 0, e, 0) for e in args.extra]
                + [(f"q{sl}e{e}d{d}", False, 0, e, ('slack', sl, d))
                   for sl in args.slack for e in args.with_extra for d in args.dw
                   if sl is not None]
                + [(f"q{q}e{e}d{d}", False, 0, e, (q, d))
                   for q in args.q16 for e in args.with_extra for d in args.dw])

    def _mk(f, k, e, q):
        if isinstance(q, tuple) and q[0] == 'slack':
            return make_runner(f, k, e, 0, q[2], q[1],
                               (15, 47) if args.word else None)
        if isinstance(q, tuple):
            return make_runner(f, k, e, q[0], q[1])
        return make_runner(f, k, e, q)
    runners = {name: _mk(f, k, e, q) for name, f, k, e, q in settings}
    key = digit_map(runners["modele"])

    totals = {name: [] for name, _, _, _, _ in settings}
    for entry in CORPUS:
        print(f"\n=== {entry['name']}  ({entry['rom']}) ===")
        head = "  " + f"{'':<12}" + f"{'silicium':>9}"
        for name, _, _, _, _ in settings:
            head += f"{name:>16}"
        print(head)
        measured = {name: read_rom(runners[name], key, entry) for name, _, _, _, _ in settings}
        for label, si in entry["silicon"].items():
            line = f"  {label:<12}{si:>9}"
            for name, _, _, _, _ in settings:
                got = measured[name][label]
                if got is None or got == 0:
                    line += f"{'?':>16}"
                    continue
                # Le compteur est un DEBIT : plus haut = plus rapide. L'ecart de
                # VITESSE est donc got/si - 1, positif = nous sommes trop rapides.
                dev = (got / si - 1.0) * 100.0
                totals[name].append(abs(dev))
                line += f"{got:>9}{dev:>+6.1f}%"
            print(line)

    print("\n=== BILAN (ecart absolu moyen au silicium, toutes cases) ===")
    for name, _, _, _, _ in settings:
        vals = totals[name]
        if not vals:
            continue
        worst = max(vals)
        print(f"  {name:<10}  moyen {sum(vals) / len(vals):5.2f} %   pire {worst:5.2f} %"
              f"   ({len(vals)} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
