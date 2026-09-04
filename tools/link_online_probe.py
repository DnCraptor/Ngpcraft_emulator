"""LE CABLE ONLINE, VU PAR LE JEU -- pas par un compteur d'octets.

/!\ CE QUE CETTE SONDE MESURE ET QU'AUCUN AUTRE BANC NE MESURAIT. Les tests du cable
font tourner deux consoles reliees EN LOCAL (`InProcessLink`, `run_linked`, le relais
de `PlayPage`). Le chemin ONLINE -- `core.link.TcpLink`, et le `LobbyLink` qui a la
meme forme -- n'a aucun banc a deux consoles: chaque test le remplace par une fausse
machine ou par une socket muette. Or c'est le seul chemin ou personne ne recopie le
RTS du pair dans le CTS local.

Depuis que 0xB1 bit2 est la LIGNE DE DETECTION (specs/LINK_CABLE.md section 1, releve
materiel du 2026-08-19), un jeu lit "pair present" dans `serial_cts_seen &&
!serial_cts_high`. En local le relais croise les deux lignes; en online personne ne
les croise.

La sonde imprime, pour les deux consoles et pour les deux modes, le 0xB1 que le CPU
lit vraiment (read8), pas le champ `port_b1` de `serial_state` -- celui-la est reste
sur l'ancien modele de detection et repond "cable vu" sur un cable que le jeu ne voit
pas.

Usage:
    python tools/link_online_probe.py "<rom>" [--frames 900] [--mode net|local|both]
"""

from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import ngpc_settings as cfg                                    # noqa: E402
from core.link import TcpLink                                  # noqa: E402
from core.native_session import NativeSession                  # noqa: E402

BIOS = REPO / "bios.bin"


def _boot(rom: Path, second: bool):
    s = NativeSession(rom, bios_path=BIOS if BIOS.exists() else None,
                      autosave=False, save_to_rom=False, second_console=second)
    s.machine.set_timing_silicon(cfg.CART_FETCH_WAIT, cfg.CART_BIOS_WAIT)
    return s


def _line(tag: str, sess) -> str:
    st = sess.machine.serial_state()
    b1 = sess.machine.read(0x0000B1, 1)[0]
    peer = "PAIR VU" if not (b1 & 0x04) else "aucun pair"
    return (f"  {tag}: 0xB1={b1:#04x} bit2={(b1 >> 2) & 1} -> {peer:11s}"
            f"  (serial_state.port_b1={st.port_b1:#04x})"
            f"  cts_high={st.cts_high} rts_low={st.rts_low}"
            f"  wire_out={st.wire_count} rx_read={st.rx_read_count}")


def run_net(rom: Path, frames: int) -> None:
    """Le chemin ONLINE, tel quel: deux TcpLink sur une paire de sockets."""
    sa, sb = socket.socketpair()
    a, b = _boot(rom, False), _boot(rom, True)
    la, lb = TcpLink(a.machine, sa), TcpLink(b.machine, sb)
    for _ in range(frames):
        for sess, link in ((a, la), (b, lb)):
            sess.machine.run_frames(1)
            link.pump()
    print(f"[online / TcpLink] {frames} trames")
    print(_line("console A", a))
    print(_line("console B", b))
    print(f"  octets: A out={la.bytes_out} in={la.bytes_in} | "
          f"B out={lb.bytes_out} in={lb.bytes_in}")
    a.close(); b.close()


def run_local(rom: Path, frames: int) -> None:
    """Le relais LOCAL du shell (PlayPage._pump_link), reduit a l'essentiel."""
    a, b = _boot(rom, False), _boot(rom, True)
    for m in (a.machine, b.machine):
        m.serial_set_enabled(True)
    for _ in range(frames):
        for src, dst in ((a.machine, b.machine), (b.machine, a.machine)):
            src.run_frames(1)
            src.serial_set_cts(not dst.serial_rts())
            data = src.serial_read_tx()
            if data:
                dst.serial_write_rx(data)
    print(f"[local / relais du shell] {frames} trames")
    print(_line("console A", a))
    print(_line("console B", b))
    a.close(); b.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("rom", type=Path)
    ap.add_argument("--frames", type=int, default=900)
    ap.add_argument("--mode", choices=("net", "local", "both"), default="both")
    args = ap.parse_args()
    print(f"=== {args.rom.name} ===")
    if args.mode in ("net", "both"):
        run_net(args.rom, args.frames)
    if args.mode in ("local", "both"):
        run_local(args.rom, args.frames)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
