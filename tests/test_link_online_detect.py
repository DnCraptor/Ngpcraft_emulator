"""LE CABLE ONLINE DOIT SE VOIR -- 0xB1 bit2, sur le chemin TCP.

⛔ LE TROU QUE CE FICHIER FERME. Tout le reste du cable est teste a deux consoles
DANS LE MEME PROCESSUS: `InProcessLink`, `ngpc_run_linked`, le relais de `PlayPage`.
Ces trois-la lisent la broche RTS de l'autre console directement et la recopient dans
le CTS local. Le chemin ONLINE -- `core.link.TcpLink`, et `core.lobby.LobbyLink` qui a
la meme forme -- n'a pas d'autre console a portee de main, et personne n'a jamais parle
pour le pair: `serial_cts_seen` restait faux et 0xB1 bit2 restait a 1.

Cote jeu, bit2 a 1 veut dire "aucune console au bout du fil". C'est la ligne sur
laquelle Card Fighters' Clash conditionne sa poignee de main (il ne quitte jamais
"EITHER PLAYER MUST PUSH A"), sur laquelle Gals' Fighters / Puyo Pop / Magical Drop
declarent ne pas voir de second joueur, et sur laquelle The Last Blade rend LINK ERROR.

Mesure d'origine (tools/link_online_probe.py, deux consoles de Gals' Fighters, 400
trames): online 0xB1=0x06, local 0xB1=0x02. Les tests ci-dessous refusent que l'ecart
revienne, DANS LES DEUX SENS -- car le contre-exemple compte autant: une console seule,
cable arme et personne en face, doit continuer a lire "aucun pair" (silicium 2026-08-19,
specs/LINK_CABLE.md §1).
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BIOS = REPO / "bios.bin"
ROM = REPO / "tests" / "roms" / "link_probe.ngc"

PORT_B1 = 0x0000B1
NO_PEER = 0x04          # bit2 = 1 -> rien au bout du cable

requires_rom = pytest.mark.skipif(
    not (BIOS.exists() and ROM.exists()),
    reason="needs the retail bios.bin (gitignored) and the probe ROM",
)


def _console(second: bool = False):
    from core.native_session import NativeSession
    return NativeSession(ROM, bios_path=BIOS, autosave=False,
                         save_to_rom=False, second_console=second)


def _b1(sess) -> int:
    """0xB1 tel que le CPU le lit -- pas le champ `port_b1` d'un instrument."""
    return sess.machine.read(PORT_B1, 1)[0]


@requires_rom
def test_online_link_is_visible_to_the_game():
    """Deux consoles reliees par le transport ONLINE se voient l'une l'autre."""
    from core.link import TcpLink

    sa, sb = socket.socketpair()
    a, b = _console(), _console(second=True)
    try:
        la, lb = TcpLink(a.machine, sa), TcpLink(b.machine, sb)
        for _ in range(60):
            for sess, link in ((a, la), (b, lb)):
                sess.machine.run_frames(1)
                link.pump()
        assert not (_b1(a) & NO_PEER), f"console A ne voit pas le pair: {_b1(a):#04x}"
        assert not (_b1(b) & NO_PEER), f"console B ne voit pas le pair: {_b1(b):#04x}"
    finally:
        a.close(); b.close()


@requires_rom
def test_online_and_local_agree():
    """Le meme jeu, sur les deux chemins, doit lire la MEME chose. C'est l'ecart
    entre les deux qui etait le bug -- pas une valeur absolue."""
    from core.link import TcpLink

    sa, sb = socket.socketpair()
    net_a, net_b = _console(), _console(second=True)
    loc_a, loc_b = _console(), _console(second=True)
    try:
        la, lb = TcpLink(net_a.machine, sa), TcpLink(net_b.machine, sb)
        for m in (loc_a.machine, loc_b.machine):
            m.serial_set_enabled(True)
        for _ in range(60):
            for sess, link in ((net_a, la), (net_b, lb)):
                sess.machine.run_frames(1)
                link.pump()
            for src, dst in ((loc_a.machine, loc_b.machine),
                             (loc_b.machine, loc_a.machine)):
                src.run_frames(1)
                src.serial_set_cts(not dst.serial_rts())
                data = src.serial_read_tx()
                if data:
                    dst.serial_write_rx(data)
        assert _b1(net_a) & NO_PEER == _b1(loc_a) & NO_PEER
        assert _b1(net_b) & NO_PEER == _b1(loc_b) & NO_PEER
    finally:
        for s in (net_a, net_b, loc_a, loc_b):
            s.close()


@requires_rom
def test_a_cable_with_nobody_at_the_far_end_still_reads_no_peer():
    """LE CONTRE-EXEMPLE, et il vaut autant que le cas positif.

    L'ancien modele derivait bit2 de "l'hote a branche un cable", donc une console
    seule cable arme lisait 0x02 la ou le silicium lit 0x07 -- et Gals' Fighters rend
    une erreur de lien sur exactement ca. Le correctif online ne doit pas y ramener.
    """
    a = _console()
    try:
        a.machine.serial_set_enabled(True)      # cable arme, personne en face
        for _ in range(60):
            a.machine.run_frames(1)
        assert _b1(a) & NO_PEER, f"un pair est signale sans pair: {_b1(a):#04x}"
    finally:
        a.close()


@requires_rom
def test_losing_the_peer_takes_the_detect_line_down():
    """Debrancher se VOIT. C'est comme ca que Match of the Millennium leve sa propre
    erreur de lien; tant que le jeu ne pouvait pas voir la coupure, elle n'etait
    traitee que cote hote."""
    from core.link import TcpLink

    sa, sb = socket.socketpair()
    a, b = _console(), _console(second=True)
    try:
        la, lb = TcpLink(a.machine, sa), TcpLink(b.machine, sb)
        for _ in range(30):
            for sess, link in ((a, la), (b, lb)):
                sess.machine.run_frames(1)
                link.pump()
        assert not (_b1(a) & NO_PEER)
        sb.close()                              # le pair s'en va
        for _ in range(10):
            a.machine.run_frames(1)
            la.pump()
        assert la.lost is not None, "la perte du pair n'a pas ete remarquee"
        assert _b1(a) & NO_PEER, "la coupure reste invisible au jeu"
    finally:
        a.close(); b.close()


@requires_rom
def test_the_debugger_reads_the_same_b1_as_the_cpu():
    """⛔ L'INSTRUMENT MENTAIT. `ngpc_serial_state.port_b1` etait reste sur l'ancien
    modele de detection: il annoncait 0x02 ("cable vu") sur une session online ou le
    CPU lisait 0x06 ("aucun pair"). L'onglet Link du debogueur cachait donc exactement
    la panne qu'on lui demandait de montrer."""
    a = _console()
    try:
        a.machine.serial_set_enabled(True)
        for _ in range(30):
            a.machine.run_frames(1)
        assert a.machine.serial_state().port_b1 == _b1(a)
        a.machine.serial_set_cts(False)          # un pair vient de se declarer
        assert a.machine.serial_state().port_b1 == _b1(a)
    finally:
        a.close()


@requires_rom
def test_the_detect_line_survives_a_save_state():
    """⛔ UN REWIND DÉBRANCHAIT LE CÂBLE, AUX YEUX DU JEU.

    `serial_cts_seen` est la seule chose qui fasse répondre 0xB1 bit2 « une console est
    au bout du fil » -- `cts_high` ne suffit pas, sa valeur par défaut voulant déjà dire
    « pair prêt ». Il ne vivait dans aucun bloc sérialisé. Or la bague de rewind du shell
    passe par la même capture, et le netplay libretro roule sur `retro_serialize` : un
    match en cours, restauré d'un cran, lisait « aucun câble » et le jeu prenait la
    sortie qu'il a pour ça.
    """
    a = _console()
    try:
        a.machine.serial_set_enabled(True)
        a.machine.serial_set_cts(False)              # un pair s'est déclaré
        assert not (_b1(a) & NO_PEER)
        blob = a.machine.link_state()

        a.machine.serial_set_enabled(False)          # on abîme l'état...
        a.machine.run_frames(1)
        assert _b1(a) & NO_PEER

        assert a.machine.set_link_state(blob)         # ...puis on restaure
        assert not (_b1(a) & NO_PEER), (
            f"le câble a disparu au rechargement: {_b1(a):#04x}")
    finally:
        a.close()
