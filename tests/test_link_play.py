"""Two-player link GUI (`ngpc_link_play.py`) — offscreen structural test.

Boots two consoles through the real coordinator, ticks it, and checks the cable
relays bytes both ways with the two players' inputs kept separate. Needs the
retail BIOS and the probe ROM, so it skips when either is absent.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402
from PyQt6.QtCore import Qt  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
BIOS = REPO / "bios.bin"
ROM = REPO / "tests" / "roms" / "link_probe.ngc"

# probe-ROM globals, from its .map (see tests/test_link_cable.py)
G_TX_COUNT = 0x400E


def rd16(machine, addr):
    d = machine.read(addr, 2)
    return d[0] | (d[1] << 8)

pytestmark = pytest.mark.skipif(
    not (BIOS.exists() and ROM.exists()),
    reason="needs the retail bios.bin (gitignored) and the probe ROM",
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_two_player_link_relays_with_split_input(app):
    import ngpc_link_play as lp

    play = lp.LinkPlay(ROM, BIOS)
    try:
        # Player 1 holds RIGHT, player 2 holds UP -- distinct bits.
        play._held.add(Qt.Key.Key_Right)   # P1 -> RIGHT (0x08)
        play._held.add(Qt.Key.Key_W)       # P2 -> UP    (0x01)
        for _ in range(150):
            play._tick()

        # the cable moved bytes both ways
        assert play.link.bytes_ab > 50
        assert play.link.bytes_ba > 50

        # each console received the OTHER player's controller byte
        assert play.a.machine.read(0x400A, 1)[0] == lp.ipt.UP     # P1 got P2's UP
        assert play.b.machine.read(0x400A, 1)[0] == lp.ipt.RIGHT  # P2 got P1's RIGHT

        # both screens rendered something
        assert any(c != 0 for c in play.a.machine.framebuffer())
        img = lp.frame_to_qimage(play.a.machine.framebuffer())
        assert img.width() == lp.SCREEN_W and img.height() == lp.SCREEN_H
    finally:
        play.close()


def test_shell_link_2p_launch_and_relay(app, monkeypatch):
    """The in-shell path: 🔗 opens P2's window (its own ROM), wires the cable, and
    the two PlayPages relay serial bytes with split per-player input."""
    import ngpc_shell
    from PyQt6.QtWidgets import QFileDialog

    sh = ngpc_shell.Shell()
    try:
        sh._settings.setValue("paths/bios", str(BIOS))
        # deterministic stepping: one frame per _tick, no audio-clock pacing
        sh.play._frames_due = lambda: 1
        sh.play.start(ROM)

        # P2 picks the same ROM (its own session/flash save)
        monkeypatch.setattr(QFileDialog, "getOpenFileName",
                            staticmethod(lambda *a, **k: (str(ROM), "")))
        sh._launch_link_2p()
        assert sh._link2p is not None
        p2 = sh._link2p.play
        p2._frames_due = lambda: 1

        # split input: P1 holds RIGHT, P2 holds UP (distinct bits)
        sh.play.held = 0x08                # RIGHT
        p2.held = 0x01                     # UP

        for _ in range(300):
            sh.play._tick()
            p2._tick()

        assert sh.play.machine.read(0x400A, 1)[0] == 0x01   # P1 got P2's UP
        assert p2.machine.read(0x400A, 1)[0] == 0x08        # P2 got P1's RIGHT
    finally:
        if sh._link2p is not None:
            sh._link2p.close()
        sh.play.stop()


def test_shell_link_2p_monitor_taps_the_relay(app, monkeypatch):
    """The debugger's tap on the SHELL's own relay (PlayPage._pump_link, which is
    a separate code path from core.link's): what P1 transmits must show up in P1's
    monitor as TX and in P2's as RX, and cutting P1's wire must actually stop the
    bytes reaching P2."""
    import ngpc_shell
    from PyQt6.QtWidgets import QFileDialog
    from core.link_debug import RX, TX, Impairment, LinkMonitor

    sh = ngpc_shell.Shell()
    try:
        sh._settings.setValue("paths/bios", str(BIOS))
        sh.play._frames_due = lambda: 1
        sh.play.start(ROM)
        monkeypatch.setattr(QFileDialog, "getOpenFileName",
                            staticmethod(lambda *a, **k: (str(ROM), "")))
        sh._launch_link_2p()
        p2 = sh._link2p.play
        p2._frames_due = lambda: 1

        mon1, mon2 = LinkMonitor(), LinkMonitor()
        sh.play.set_link_monitor(mon1)
        p2.set_link_monitor(mon2)
        sh.play.held, p2.held = 0x08, 0x01

        for _ in range(300):
            sh.play._tick()
            p2._tick()

        assert mon1.bytes_tx > 0 and mon1.bytes_rx > 0
        assert mon1.raw(TX) == mon2.raw(RX), "P1's transmissions ARE P2's arrivals"
        assert sh.play.link_mode() == "local2p"

        # cut P1's wire: P2 stops hearing, P1 keeps hearing P2
        mon1.impair = Impairment(cut=True)
        before_p2 = p2.machine.serial_state().rx_queued_count
        before_p1 = sh.play.machine.serial_state().rx_queued_count
        for _ in range(200):
            sh.play._tick()
            p2._tick()
        assert p2.machine.serial_state().rx_queued_count == before_p2
        assert sh.play.machine.serial_state().rx_queued_count > before_p1
    finally:
        if sh._link2p is not None:
            sh._link2p.close()
        sh.play.stop()


def test_attaching_the_cable_power_cycles_a_running_console(app, monkeypatch):
    """⚡ A cable handed to a console that is already running is a cable no game
    will ever see.

    Samurai Shodown! 2 sends its first link packet at frame FIVE of its own boot
    and latches the answer: no peer then means no peer for the rest of the
    session, and VS PLAY stops responding entirely. Arming the link at frame 0
    works and arming it at frame 30 does not -- but the shell's flow is to boot a
    game and press the link button afterwards, which is always the late case.

    So attaching the cable power-cycles the console: on hardware you plug the
    cable in and *then* switch both consoles on. Player 1 has been running for a
    while when 🔗 is pressed; it must restart with the link already armed, so the
    game's boot-time probe finds its peer.
    """
    import ngpc_shell
    from PyQt6.QtWidgets import QFileDialog

    sh = ngpc_shell.Shell()
    try:
        sh._settings.setValue("paths/bios", str(BIOS))
        sh.play._frames_due = lambda: 1
        sh.play.start(ROM)
        for _ in range(120):               # P1 is well past its own boot probe
            sh.play._tick()
        assert rd16(sh.play.machine, G_TX_COUNT) > 50   # it has been running a while

        monkeypatch.setattr(QFileDialog, "getOpenFileName",
                            staticmethod(lambda *a, **k: (str(ROM), "")))
        sh._launch_link_2p()
        p2 = sh._link2p.play

        # P1 went back to power-on WITH the cable armed -- not left mid-session.
        # Its own loop counter, in work RAM, is the witness: a reboot zeroes it.
        assert rd16(sh.play.machine, G_TX_COUNT) == 0, "the console did not power-cycle"
        assert sh.play.machine.serial_state().enabled == 1
        assert p2.machine.serial_state().enabled == 1

        # and the cable works from P1's very first frame after that
        p2._frames_due = lambda: 1
        sh.play.held, p2.held = 0x08, 0x01
        for _ in range(300):
            sh.play._tick()
            p2._tick()
        assert sh.play.machine.read(0x400A, 1)[0] == 0x01
        assert p2.machine.read(0x400A, 1)[0] == 0x08
    finally:
        if sh._link2p is not None:
            sh._link2p.close()
        sh.play.stop()
