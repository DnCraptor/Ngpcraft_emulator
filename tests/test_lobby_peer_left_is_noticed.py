"""When the other player leaves a lobby game, the shell has to notice.

The lobby link cannot crash the way the direct one did (see test_link_peer_loss): it
only ever touches thread-safe queues, never a socket. It went QUIET instead. The client
already knew -- `LobbyClient.peer_left` and `.disconnected` were emitted on every one of
these events -- and nothing in the shell was connected to either, so the player was left
holding a controller, waiting on a console that had already left the room.

This pins the wiring, not the transport: emit what the client emits, and the link must
come down and the player must be told.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, pyqtSignal  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import ngpc_settings as cfg  # noqa: E402
import ngpc_shell as shell  # noqa: E402

# A cartridge is not needed: the link attaches to whatever machine is running, and the
# console's own BIOS screen is a running machine. That keeps this test off the user's
# ROM folder -- where it SKIPPED, and a skipped test proves nothing.
pytestmark = pytest.mark.skipif(not shell.DEFAULT_BIOS.is_file(),
                                reason="needs the local BIOS image")


class FakeLobbyClient(QObject):
    """The signals and the two methods LobbyLink.disconnect() calls. No server."""

    peer_left = pyqtSignal()
    disconnected = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.left = False
        self.closed = False

    def send_serial(self, data: bytes) -> None:
        pass

    def read_serial(self) -> bytes:
        return b""

    def leave(self) -> None:
        self.left = True

    def close(self) -> None:
        self.closed = True


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _clean_settings():
    s = cfg.make_settings()
    s.clear()
    yield
    s.clear()


@pytest.fixture
def linked(app):
    """A running console with a lobby link attached -- and torn down for real.

    ⚠️ TEARDOWN IS PART OF THIS TEST, not boilerplate. These are the first tests in the
    suite to leave a BOOTED machine behind (every other Shell test that boots one skips
    for want of a ROM folder), and `close()` alone only hides the window: the Shell, its
    timers and its native core stayed alive, and the next test that called
    `QApplication.processEvents()` delivered events into them. That killed the whole run
    at ~90% -- in an unrelated, passing test, with no summary and no traceback, which is
    exactly how this class of Qt fault disguises itself (see the root conftest).
    So: stop the emulation, stop the thumbnail worker (a second native core is its own
    crash -- `analyze_rom` stops it for the same reason), then destroy the window and
    let Qt actually run the deletion before the next test starts.
    """
    w = shell.Shell()
    w.library._stop_worker()
    w.play._frames_due = lambda: 1
    w.play.start_bios()
    client = FakeLobbyClient()
    w._on_lobby_linked(client)
    assert w.play._net_link is not None, "the link never attached"
    yield w, client
    w.play.stop()
    w.library._stop_worker()
    w.close()
    w.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("event", ["peer_left", "disconnected"])
def test_the_shell_ends_the_link_when_the_peer_goes(linked, event):
    # BOTH signals: "the other player left the room" and "the connection to the server
    # dropped" are different events and only one of them is the polite one.
    w, client = linked
    before = w.play.overlay.text()
    if event == "peer_left":
        client.peer_left.emit()
    else:
        client.disconnected.emit("server closed")
    assert w.play._net_link is None, (
        f"the peer went ({event}) and the shell kept the link attached")
    assert client.closed, "the lobby client was never shut down"
    assert w.play.overlay.text() != before, "the player was never told"


def test_the_game_keeps_running_after_the_peer_goes(linked):
    # A dropped cable is not a dropped game -- unplugging the lead on real hardware
    # does not switch the console off, and neither should this.
    w, client = linked
    client.peer_left.emit()
    assert w.play.machine is not None, "the console was torn down with the cable"
    w.play._tick()                      # and the next frame must not raise
