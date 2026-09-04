"""UNE SESSION ONLINE A DEUX CONSOLES, SANS INTERFACE -- et on REGARDE l'ecran.

Un compteur d'octets ne dit pas si le mode VS demarre: les jeux qui echouaient
echangeaient deja des octets. Ce banc fait donc les trois choses qu'une manette fait:
il boote deux consoles reliees par le chemin ONLINE (`core.link.TcpLink` sur une paire
de sockets -- le meme objet que le shell attache pour une partie en direct ou par le
salon), il APPUIE sur A / OPTION sur les deux comme un joueur, et il ENREGISTRE l'image
des deux ecrans a la fin (PNG) en plus des compteurs du cable.

Usage:
    python tools/link_online_session.py "<rom>" [--frames 1800] [--out <dossier>]
    python tools/link_online_session.py "<rom>" --script "A@120,OPTION@300,A@480"
"""

from __future__ import annotations

import argparse
import socket
import sys
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import ngpc_settings as cfg                                    # noqa: E402
from core.link import CABLE_SLICE, TcpLink                     # noqa: E402
from core.native_session import NativeSession                  # noqa: E402

BIOS = REPO / "bios.bin"
W, H = 160, 152
A, B, OPTION = 0x10, 0x20, 0x40
NAMES = {"A": A, "B": B, "OPTION": OPTION,
         "UP": 0x01, "DOWN": 0x02, "LEFT": 0x04, "RIGHT": 0x08}


def write_png(path: Path, pixels: list[int]) -> None:
    """PNG sans dependance: le banc doit tourner meme sans Pillow/numpy."""
    rows = bytearray()
    for y in range(H):
        rows.append(0)                                  # filtre "none"
        for x in range(W):
            v = pixels[y * W + x]
            rows += bytes(((v & 0x0F) * 17, ((v >> 4) & 0x0F) * 17,
                           ((v >> 8) & 0x0F) * 17))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (len(data).to_bytes(4, "big") + tag + data
                + zlib.crc32(tag + data).to_bytes(4, "big"))

    hdr = W.to_bytes(4, "big") + H.to_bytes(4, "big") + bytes((8, 2, 0, 0, 0))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", hdr)
                     + chunk(b"IDAT", zlib.compress(bytes(rows), 6))
                     + chunk(b"IEND", b""))


def parse_script(text: str) -> dict[int, int]:
    """"A@120,OPTION@300" -> {frame: masque}. Un appui dure 6 trames (voir plus bas)."""
    out: dict[int, int] = {}
    for item in filter(None, (t.strip() for t in text.split(","))):
        name, _, frame = item.partition("@")
        mask = NAMES[name.strip().upper()]
        start = int(frame)
        for f in range(start, start + 6):               # ~100 ms de maintien
            out[f] = out.get(f, 0) | mask
    return out


def _boot(rom: Path, second: bool):
    s = NativeSession(rom, bios_path=BIOS if BIOS.exists() else None,
                      autosave=False, save_to_rom=False, second_console=second)
    s.machine.set_timing_silicon(cfg.CART_FETCH_WAIT, cfg.CART_BIOS_WAIT)
    return s



def _run_one_frame(machine, link, whole_frame: bool) -> None:
    """Une trame, relayee comme le shell la relaie.

    /!\ UN BANC QUI POUSSE LA TRAME ENTIERE PUIS RELAIE UNE FOIS INVENTE UNE TRAME DE
    LATENCE QUE LE SHELL N'A PAS. `PlayPage._run_frame_relaying` decoupe la trame en
    tranches de `CABLE_SLICE` = 400 instructions et pompe la socket entre chaque -- et
    ce nombre est un chiffre de CORRECTNESS, pas un reglage: la poignee de main de The
    Last Blade meurt a une trame de latence. Mesurer la panne d'un jeu sur un banc plus
    lent que le produit, c'est mesurer le banc.
    """
    if whole_frame:
        machine.run_frames(1)
        link.pump()
        return
    start = machine.run(0, record=False)[0].frame_count
    for _ in range(256):
        summ, _ = machine.run(CABLE_SLICE, record=False)
        link.pump()
        if summ.executed == 0 or summ.frame_count != start:
            return
    machine.run_frames(1)
    link.pump()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("rom", type=Path)
    ap.add_argument("--frames", type=int, default=1800)
    ap.add_argument("--script", default="")
    ap.add_argument("--auto", action="store_true",
                    help="appuie sur A toutes les 90 trames (traverse les ecrans-titre)")
    ap.add_argument("--out", type=Path, default=REPO / "watches" / "link_online")
    ap.add_argument("--shots", type=int, default=4, help="nombre d'instantanes")
    ap.add_argument("--every", type=int, default=0,
                    help="une image toutes les N trames (prime sur --shots)")
    ap.add_argument("--distinct", action="store_true",
                    help="n'ecrit une image que si l'ecran a CHANGE depuis la "
                         "derniere ecrite -- un fichier par ecran, pas par trame")
    ap.add_argument("--whole-frame", action="store_true",
                    help="une seule relance du cable par trame -- ce que fait un banc "
                         "naif, PAS ce que fait le shell. Pour montrer l'ecart.")
    ap.add_argument("--as-shipped", action="store_true",
                    help="neutralise la declaration de pair -- rejoue EXACTEMENT le "
                         "comportement de la version livree, pour l'attribution A/B")
    args = ap.parse_args()

    if args.as_shipped:
        # Le seul changement du correctif, retire. Rien d'autre ne differe, donc tout
        # ecart mesure entre les deux passes lui appartient.
        import core.link as _link
        _link.declare_peer = lambda machine, present: None

    presses = parse_script(args.script) if args.script else {}
    if args.auto:
        for f in range(90, args.frames, 90):
            for k in range(f, f + 6):
                presses[k] = presses.get(k, 0) | A

    args.out.mkdir(parents=True, exist_ok=True)
    stem = args.rom.stem.replace(" ", "_")[:40]

    sa, sb = socket.socketpair()
    a, b = _boot(args.rom, False), _boot(args.rom, True)
    la, lb = TcpLink(a.machine, sa), TcpLink(b.machine, sb)

    last_hash = {"A": None, "B": None}
    every = args.every if args.every > 0 else max(1, args.frames // max(1, args.shots))
    for f in range(args.frames):
        pad = presses.get(f, 0)
        for sess, link in ((a, la), (b, lb)):
            sess.machine.write(0x00B0, bytes([pad]))
            _run_one_frame(sess.machine, link, args.whole_frame)
        if (f + 1) % every == 0 or f == args.frames - 1:
            for tag, sess in (("A", a), ("B", b)):
                fb = sess.machine.framebuffer()
                if args.distinct:
                    # Un ecran de menu est identique pendant des centaines de trames.
                    # N'ecrire que les CHANGEMENTS donne un fichier par ecran traverse,
                    # ce qui est la seule granularite lisible pour retrouver un chemin
                    # de menu sans jouer a la main.
                    h = zlib.crc32(bytes(str(fb), "ascii"))
                    if h == last_hash[tag]:
                        continue
                    last_hash[tag] = h
                write_png(args.out / f"{stem}_{tag}_f{f + 1:05d}.png", fb)

    for tag, sess, lk in (("A", a, la), ("B", b, lb)):
        st = sess.machine.serial_state()
        b1 = sess.machine.read(0x0000B1, 1)[0]
        print(f"  console {tag}: 0xB1={b1:#04x} "
              f"({'PAIR VU' if not (b1 & 4) else 'aucun pair'})"
              f"  emis={st.wire_count} lus={st.rx_read_count}"
              f"  irq_tx={st.irq_tx_count} irq_rx={st.irq_rx_count}"
              f"  socket out={lk.bytes_out} in={lk.bytes_in}")
    print(f"  images -> {args.out}")
    a.close(); b.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
