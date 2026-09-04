"""UN HOTE DE CABLE, SANS INTERFACE -- pour prouver un lien avec un AUTRE frontend.

Le bureau, le coeur libretro et l'application Android parlent le meme fil: les octets du
cable, sans en-tete ni version. C'est ce qui rend le jeu multiplateforme possible -- et
c'est invérifiable tant qu'il faut deux fenetres et deux joueurs pour l'essayer. Ce banc
tient un bout: il ecoute, joue la ROM sonde, et dit ce que SA cartouche a recu.

    python tools/link_host_bench.py --port 7788 --seconds 60

`g_last_rx` @0x400A doit finir par porter l'octet de manette de l'AUTRE console, pas le
sien: un relais reboucle sur lui-meme satisferait un compteur, pas cette assertion.
"""

from __future__ import annotations

import argparse
import socket
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import ngpc_settings as cfg                                    # noqa: E402
from core.link import CABLE_SLICE, TcpLink                     # noqa: E402
from core.native_session import NativeSession                  # noqa: E402

G_LAST_RX, G_RX_TOTAL = 0x400A, 0x400C


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rom", type=Path, default=REPO / "tests" / "roms" / "link_probe.ngc")
    ap.add_argument("--port", type=int, default=7788)
    ap.add_argument("--join", default="",
                    help="rejoindre CETTE adresse au lieu d'ecouter -- l'autre sens")
    ap.add_argument("--pad", type=lambda v: int(v, 0), default=0x11)
    ap.add_argument("--seconds", type=float, default=60.0)
    args = ap.parse_args()

    if args.join:
        print(f"connexion a {args.join}:{args.port} — ROM {args.rom.name}", flush=True)
        try:
            conn = socket.create_connection((args.join, args.port), timeout=args.seconds)
        except OSError as e:
            print(f"impossible de joindre l'hote: {e}", flush=True)
            return 1
        who = (args.join, args.port)
    else:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", args.port))
        srv.listen(1)
        print(f"hote en ecoute sur 0.0.0.0:{args.port} — ROM {args.rom.name}", flush=True)
        srv.settimeout(args.seconds)
        try:
            conn, who = srv.accept()
        except socket.timeout:
            print("personne ne s'est connecte", flush=True)
            return 1
        finally:
            srv.close()
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    print(f"pair connecte depuis {who[0]}:{who[1]}", flush=True)

    s = NativeSession(args.rom, bios_path=REPO / "bios.bin", autosave=False,
                      save_to_rom=False)
    s.machine.set_timing_silicon(cfg.CART_FETCH_WAIT, cfg.CART_BIOS_WAIT)
    link = TcpLink(s.machine, conn)

    # ⛔ `g_last_rx` NE RETIENT QUE LE DERNIER OCTET, et ca suffit a rendre un banc
    # ambigu: on tient une touche au milieu de la fenetre, on la relache, et le dernier
    # octet recu redevient 0 -- le banc conclut « rien n'est passe » alors que tout est
    # passe. On echantillonne donc pendant la course et on rend l'ENSEMBLE des valeurs
    # vues, ce qui repond a « la manette du pair traverse-t-elle » sans dependre de
    # l'instant ou la mesure tombe.
    seen: set[int] = set()
    end = time.time() + args.seconds
    while time.time() < end and link.lost is None:
        seen.add(s.machine.read(G_LAST_RX, 1)[0])
        s.machine.write(0x00B0, bytes([args.pad]))
        start = s.machine.run(0, record=False)[0].frame_count
        for _ in range(256):
            summ, _ = s.machine.run(CABLE_SLICE, record=False)
            link.pump()
            if summ.executed == 0 or summ.frame_count != start:
                break
    last = s.machine.read(G_LAST_RX, 1)[0]
    total = int.from_bytes(s.machine.read(G_RX_TOTAL, 2), "little")
    b1 = s.machine.read(0x0000B1, 1)[0]
    seen.add(last)
    print("cartouche: octets de manette recus du pair: "
          + ", ".join(f"{v:#04x}" for v in sorted(seen)), flush=True)
    print(f"cartouche: last_rx={last:#04x} rx_total={total} | 0xB1={b1:#04x} "
          f"({'pair vu' if not (b1 & 4) else 'aucun pair'}) | "
          f"socket out={link.bytes_out} in={link.bytes_in} lost={link.lost}", flush=True)
    s.close()
    return 0 if total and last != args.pad else 2


if __name__ == "__main__":
    raise SystemExit(main())
