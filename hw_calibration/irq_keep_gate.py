#!/usr/bin/env python3
"""La file se recharge-t-elle pendant l'acceptation d'une interruption ?

Deux mesures INDEPENDANTES du meme mecanisme :
  - v18 page 0 : cout FIXE d'une IRQ                       silicium 111,1 cy
  - v8         : WORK0 / WORK1 / WORK4 (152 et 38 IRQ/trame) silicium 261 / 218 / 249
Si une meme valeur les satisfait toutes, le mecanisme tient. Sinon il ne tient pas, et
c'est un resultat negatif -- pas un reglage a chercher.
"""
import sys, struct
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "hw_calibration"))
import v18_gate as G
from core import native

BIOS = (ROOT / "bios.bin").read_bytes()
V8 = ROOT / "hw_calibration" / "a_irq_calib_v8.ngp"


def mk(rom, keep, bw=None, extra=4):
    m = native.NativeMachine(rom.read_bytes(), bios=BIOS)
    m.set_timing_silicon()
    m.set_queue_bytes(4)
    m.set_muldiv_word(15, 47)
    m.set_branch_taken_extra(extra)
    m.set_irq_queue_keep_q16(keep)
    if bw is not None:
        m.set_bios_wait(bw)
        m.set_bios_data_wait(bw)
    m.reset(bios_handoff=True)
    return m


def step(m, n, b=0):
    for _ in range(n):
        m.write(0x00B0, bytes([b]))
        m.run_frames(1)


def tile(m, c, r):
    return struct.unpack("<H", m.read(0x9000 + (r * 32 + c) * 2, 2))[0] & 0x01FF


def v18(keep, bw=None, extra=4):
    m = mk(G.ROM_PATH, keep, bw, extra)
    step(m, 400)
    key = {tile(m, 1 + i, 17): str(i) for i in range(10)}
    out = {}
    for pg, names in G.PAGES.items():
        for _ in range(60):
            cur = key.get(tile(m, 11, 1))
            if cur == str(pg):
                break
            step(m, 20, G.J_RIGHT if (cur is None or int(cur) < pg) else G.J_LEFT)
            step(m, 20, 0)
        step(m, len(names) * 60 + 280)
        for i, n in enumerate(names):
            s = "".join(key.get(tile(m, 12 + j, 3 + i), " ") for j in range(5)).strip()
            out[n] = int(s) if s.isdigit() else None
    p0 = [out[n] for n in G.PAGES[0]]
    sl, fx, _ = G.fit([float(x) for x in G.NOPS], G.per_irq(p0[0], p0[1:]))
    return p0[0], sl, fx


def v8(keep, digit_key, bw=None, extra=4):
    m = mk(V8, keep, bw, extra)
    step(m, 4 * 60 + 240)
    out = []
    for row in (3, 4, 5):
        s = "".join(digit_key.get(tile(m, 12 + j, row), " ") for j in range(5)).strip()
        out.append(int(s) if s.isdigit() else None)
    return out


# cle de chiffres : meme fonte BIOS, prise sur la v13 comme le corpus
mk13 = mk(ROOT / "hw_calibration" / "a_flush_calib_v13.ngp", 0)
step(mk13, 400)
KEY = {}
for i in range(10):
    KEY[struct.unpack("<H", mk13.read(0x9000 + (13 * 32 + (1 + i)) * 2, 2))[0] & 0x01FF] = str(i)
assert len(KEY) == 10

import sys as _s
MODE = _s.argv[1] if len(_s.argv) > 1 else "keep"
d = lambda g, s: f"{g:4}{(g/s-1)*100:+6.1f}%"
print("silicium :  v18 FIXE 111,1 cy      v8  WORK0 261  WORK1 218  WORK4 249")
if MODE == "keep":
    for keep in (0, 16, 32, 48, 64):
        w0, sl, fx = v18(keep)
        a, b, c = v8(keep, KEY)
        print(f"  keep={keep:2} ({keep/16:.1f} o.)  W0={w0}  {sl:5.2f} cy/nop  FIXE {fx:6.1f}"
              f"   |  {d(a,261)}  {d(b,218)}  {d(c,249)}")
elif MODE == "extra":
    # ⚖️ `branch_taken_extra` a ete cale SOUS LE CREDIT EN CYCLES. Le modele en octets,
    # lui, fait deja payer un vidage de file a chaque transfert pris : la surcharge peut
    # etre comptee DEUX FOIS. Un chemin d'interruption en contient deux ou trois.
    for e in (0, 1, 2, 3, 4):
        w0, sl, fx = v18(0, None, e)
        a, b, c = v8(0, KEY, None, e)
        print(f"  extra={e}  W0={w0}  {sl:5.2f} cy/nop  FIXE {fx:6.1f}"
              f"   |  {d(a,261)}  {d(b,218)}  {d(c,249)}")
else:
    # `bios_wait` n'a JAMAIS ete mesure sous le modele en octets : il a ete cale dans le
    # credit en cycles, ou le fetch BIOS etait absorbe autrement. Et le BIOS est une ROM
    # INTERNE -- rien n'oblige son mot a couter le prix du bus 8 bits de la cartouche.
    for bw in (8, 7, 6, 5, 4, 3, 2):
        w0, sl, fx = v18(0, bw)
        a, b, c = v8(0, KEY, bw)
        print(f"  bios_wait={bw}  ({bw/2:.1f} cy/octet)  W0={w0}  {sl:5.2f} cy/nop"
              f"  FIXE {fx:6.1f}   |  {d(a,261)}  {d(b,218)}  {d(c,249)}")
