"""LE MEME JEU, LE MEME SCRIPT DE MANETTE, LES TROIS RELAIS -- qui casse, et lequel.

Le shell a trois facons de faire avancer deux consoles cablees, et elles ne se
ressemblent pas du tout:

  core   `ngpc_run_linked`   -- les deux consoles ET le cable dans le coeur, cadences
                               sur le temps-octet du cable. C'est le cable local 2
                               joueurs sur un seul PC.
  local  le relais de `PlayPage._pump_link`, tranche par tranche de CABLE_SLICE
                               instructions, les deux consoles alternees.
  net    `TcpLink` sur une paire de sockets -- le chemin ONLINE, une console par
                               PC dans la vraie vie, donc AUCUNE alternance imposee:
                               chacune court sa trame et pompe la socket.

Un jeu qui marche en local et casse en ligne accuse le TRANSPORT. Un jeu qui casse
partout accuse le modele du canal serie ou le jeu lui-meme. C'est la seule question
que ce banc pose, et il y repond avec l'ecran, pas avec un compteur.

Usage:
    python tools/link_relay_compare.py "<rom>" --script "A@100,A@190,DOWN@280,A@340" \
        --frames 900
"""

from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import ngpc_settings as cfg                                    # noqa: E402
from core import native                                        # noqa: E402
from core.link import CABLE_SLICE, TcpLink                     # noqa: E402
from core.link_debug import LinkMonitor                        # noqa: E402
from core.native_session import NativeSession                  # noqa: E402
from tools.link_online_session import parse_script, write_png  # noqa: E402

BIOS = REPO / "bios.bin"


def _pair(rom: Path, twin: bool = False):
    out = []
    for second in (False, not twin):
        s = NativeSession(rom, bios_path=BIOS if BIOS.exists() else None,
                          autosave=False, save_to_rom=False, second_console=second)
        s.machine.set_timing_silicon(cfg.CART_FETCH_WAIT, cfg.CART_BIOS_WAIT)
        out.append(s)
    return out


def _slice_frame(machine, pump) -> None:
    """Une trame decoupee comme `PlayPage._run_frame_relaying` la decoupe."""
    start = machine.run(0, record=False)[0].frame_count
    for _ in range(256):
        summ, _ = machine.run(CABLE_SLICE, record=False)
        pump()
        if summ.executed == 0 or summ.frame_count != start:
            return
    machine.run_frames(1)
    pump()


def run(rom: Path, relay: str, frames: int, presses: dict[int, int], out: Path,
        offset_b: int = 0, delay: int = 0, twin: bool = False):
    a, b = _pair(rom, twin)
    la = lb = None
    mon_a = mon_b = None
    if relay == "net":
        sa, sb = socket.socketpair()
        # La LATENCE est la seule chose que le chemin online a de plus que le local, et
        # c'est en trames qu'elle se compte pour une poignee de main: le decoupage a
        # CABLE_SLICE existe parce qu'UNE trame de retard tue celle de The Last Blade.
        if delay > 0:
            mon_a, mon_b = LinkMonitor(), LinkMonitor()
            mon_a.impair.delay_frames = mon_b.impair.delay_frames = delay
        la = TcpLink(a.machine, sa, monitor=mon_a)
        lb = TcpLink(b.machine, sb, monitor=mon_b)
    else:
        for m in (a.machine, b.machine):
            m.serial_set_enabled(True)

    def relay_local() -> None:
        for src, dst in ((a.machine, b.machine), (b.machine, a.machine)):
            src.serial_set_cts(not dst.serial_rts())
            data = src.serial_read_tx()
            if data:
                dst.serial_write_rx(data)

    for f in range(frames):
        # ⛔ NE PAS APPUYER SUR LES MEMES TOUCHES AU MEME INSTANT SUR LES DEUX CONSOLES.
        # Deux joueurs ne sont jamais synchrones a la trame pres, et un banc qui les
        # synchronise fabrique une situation qui n'existe pas -- c'est comme ca qu'il
        # a d'abord accuse un jeu qui marche tres bien en 2 fenetres sur un PC.
        a.machine.write(0x00B0, bytes([presses.get(f, 0)]))
        b.machine.write(0x00B0, bytes([presses.get(f - offset_b, 0)]))
        if mon_a is not None:
            mon_a.frame = mon_b.frame = f
        if relay == "core":
            native.run_linked(a.machine, b.machine, 1)
        elif relay == "local":
            # Les deux consoles avancent ENSEMBLE, tranche par tranche: c'est ce que
            # `_run_frame_interleaved` fait, et c'est la moitie de l'interet du decoupage.
            starts = [m.run(0, record=False)[0].frame_count for m in (a.machine, b.machine)]
            done = [False, False]
            for _ in range(256):
                for i, m in enumerate((a.machine, b.machine)):
                    if done[i]:
                        continue
                    summ, _ = m.run(CABLE_SLICE, record=False)
                    if summ.executed == 0 or summ.frame_count != starts[i]:
                        done[i] = True
                relay_local()
                if all(done):
                    break
            for i, m in enumerate((a.machine, b.machine)):
                if not done[i]:
                    m.run_frames(1)
                    relay_local()
        else:
            _slice_frame(a.machine, la.pump)
            _slice_frame(b.machine, lb.pump)

    tag_dir = out / (relay if not delay else f"{relay}_d{delay}")
    tag_dir.mkdir(parents=True, exist_ok=True)
    for tag, sess in (("A", a), ("B", b)):
        write_png(tag_dir / f"{tag}_f{frames:05d}.png", sess.machine.framebuffer())
        st = sess.machine.serial_state()
        b1 = sess.machine.read(0x0000B1, 1)[0]
        print(f"  [{relay:5s}] console {tag}: 0xB1={b1:#04x} "
              f"emis={st.wire_count} lus={st.rx_read_count} "
              f"irq_rx={st.irq_rx_count} cts_hold={st.cts_hold_ticks} "
              f"rts_hold={st.rts_hold_ticks}")
    a.close(); b.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("rom", type=Path)
    ap.add_argument("--frames", type=int, default=900)
    ap.add_argument("--script", default="")
    ap.add_argument("--relays", default="core,local,net")
    ap.add_argument("--offset-b", type=int, default=17,
                    help="decalage, en trames, des appuis de la console B (0 = les deux "
                         "joueurs appuient a la trame pres, ce qui n'arrive jamais)")
    ap.add_argument("--twin", action="store_true",
                    help="les DEUX consoles bootent comme 'console 1' -- ce que sont "
                         "deux PC en ligne: meme pile bouton, meme phase de quartz")
    ap.add_argument("--delay", type=int, default=0,
                    help="latence simulee sur le chemin net, EN TRAMES (1 ~ 17 ms)")
    ap.add_argument("--out", type=Path, default=REPO / "watches" / "relay_compare")
    args = ap.parse_args()
    presses = parse_script(args.script) if args.script else {}
    print(f"=== {args.rom.name} — {args.frames} trames ===")
    for relay in args.relays.split(","):
        run(args.rom, relay.strip(), args.frames, presses, args.out,
            offset_b=args.offset_b, delay=args.delay, twin=args.twin)
    print(f"  images -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
