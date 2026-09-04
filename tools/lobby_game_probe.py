"""UN VRAI JEU A TRAVERS LE VRAI SERVEUR DE SALON -- le maillon jamais mesure.

⛔ CE QUE LES BANCS PRECEDENTS NE TOUCHAIENT PAS. `tools/link_relay_compare.py`
compare trois relais, mais son relai "net" est une paire de sockets: pas de serveur,
pas de trames longueur-prefixee, pas de fil de fond, pas de reveil par pipe. Et
`tests/test_lobby.py` fait bien tourner le VRAI serveur, mais avec la ROM sonde -- qui
emet en boucle et ne demande jamais rien a personne. Le trou est exactement au milieu:
**un jeu du commerce, avec sa poignee de main, a travers le serveur.**

Le banc demarre `server/lobby_server.py` sur un port ephemere, cree une room, la
rejoint, attache un `LobbyLink` a chaque console, joue le script de manette, et
enregistre les deux ecrans. `--mode mirror` fait la meme chose sur une room miroir.

Usage:
    python tools/lobby_game_probe.py "<rom>" --script "A@100,A@190,DOWN@280,A@340"
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "server"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication                       # noqa: E402

import lobby_server                                            # noqa: E402
import ngpc_settings as cfg                                    # noqa: E402
from core.link import CABLE_SLICE                              # noqa: E402
from core.lobby import LobbyClient, LobbyLink                  # noqa: E402
from core.native_session import NativeSession                  # noqa: E402
from tools.link_online_session import parse_script, write_png  # noqa: E402

BIOS = REPO / "bios.bin"


def start_server() -> int:
    box: dict = {}
    ready = threading.Event()

    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def boot():
            lobby = lobby_server.Lobby()
            srv = await asyncio.start_server(
                lambda r, w: lobby_server.client_task(lobby, r, w), "127.0.0.1", 0)
            box["port"] = srv.sockets[0].getsockname()[1]
            ready.set()
            async with srv:
                await srv.serve_forever()

        loop.run_until_complete(boot())

    threading.Thread(target=run, daemon=True).start()
    assert ready.wait(5), "le serveur de salon n'a pas demarre"
    return box["port"]


def wait(app, pred, timeout=10.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.005)
    return False


def boot(rom: Path, second: bool):
    s = NativeSession(rom, bios_path=BIOS if BIOS.exists() else None,
                      autosave=False, save_to_rom=False, second_console=second)
    s.machine.set_timing_silicon(cfg.CART_FETCH_WAIT, cfg.CART_BIOS_WAIT)
    return s


def slice_frame(machine, link, app) -> None:
    """Une trame decoupee comme `PlayPage._run_frame_relaying`, avec la boucle Qt
    servie entre les tranches -- c'est le fil de la socket qui livre les octets."""
    start = machine.run(0, record=False)[0].frame_count
    for _ in range(256):
        summ, _ = machine.run(CABLE_SLICE, record=False)
        link.pump()
        app.processEvents()
        if summ.executed == 0 or summ.frame_count != start:
            return
    machine.run_frames(1)
    link.pump()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("rom", type=Path)
    ap.add_argument("--frames", type=int, default=900)
    ap.add_argument("--script", default="")
    ap.add_argument("--offset-b", type=int, default=17)
    ap.add_argument("--twin", action="store_true",
                    help="les DEUX consoles bootent comme 'console 1' -- ce que sont "
                         "deux PC en ligne: meme pile bouton, meme phase de quartz")
    ap.add_argument("--out", type=Path, default=REPO / "watches" / "lobby_probe")
    args = ap.parse_args()

    app = QApplication.instance() or QApplication([])
    port = start_server()
    presses = parse_script(args.script) if args.script else {}

    box: dict = {"room": None, "host": None, "guest": None}
    host = LobbyClient("127.0.0.1", port, "Alice")
    host.created.connect(lambda r: box.__setitem__("room", r))
    host.joined.connect(lambda o: box.__setitem__("host", o))
    host.start()
    assert wait(app, lambda: host._sock is not None), "hote non connecte"
    host.create("banc", args.rom.stem[:24], public=True)
    assert wait(app, lambda: box["room"]), "aucun code de room"

    guest = LobbyClient("127.0.0.1", port, "Bob")
    guest.joined.connect(lambda o: box.__setitem__("guest", o))
    guest.start()
    assert wait(app, lambda: guest._sock is not None), "invite non connecte"
    guest.join(box["room"])
    assert wait(app, lambda: box["host"] and box["guest"]), "appairage jamais fait"
    print(f"  room {box['room']} — appairage OK "
          f"(hote={box['host'].get('peer')}, invite={box['guest'].get('role')})")

    a, b = boot(args.rom, False), boot(args.rom, not args.twin)
    la, lb = LobbyLink(a.machine, host), LobbyLink(b.machine, guest)

    for f in range(args.frames):
        a.machine.write(0x00B0, bytes([presses.get(f, 0)]))
        b.machine.write(0x00B0, bytes([presses.get(f - args.offset_b, 0)]))
        slice_frame(a.machine, la, app)
        slice_frame(b.machine, lb, app)

    args.out.mkdir(parents=True, exist_ok=True)
    for tag, sess, lk in (("A", a, la), ("B", b, lb)):
        write_png(args.out / f"{tag}_f{args.frames:05d}.png", sess.machine.framebuffer())
        st = sess.machine.serial_state()
        print(f"  console {tag}: 0xB1={sess.machine.read(0xB1, 1)[0]:#04x} "
              f"emis={st.wire_count} lus={st.rx_read_count} "
              f"relay out={lk.bytes_out} in={lk.bytes_in}")
    print(f"  images -> {args.out}")
    a.close(); b.close(); host.close(); guest.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
