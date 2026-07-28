"""A peer that goes away must end the link, not the process.

⛔ THE CRASH THIS CONDEMNS. "Sometimes, some games lost the connection between users
playing in online mode and then, the emulator crashes." -- user report 2026-07-28
(issue #14.2). `TcpLink.pump()` caught BlockingIOError and InterruptedError, which are
the "the kernel is busy, try again" cases. A peer that GOES raises a different family --
ConnectionResetError, ConnectionAbortedError, BrokenPipeError -- and those escaped.

⚡ WHY THAT IS A CRASH AND NOT A TRACEBACK, measured rather than assumed: pump() runs
inside PlayPage._tick, which is a QTimer slot, and PyQt answers an unhandled Python
exception in a slot with qFatal(). A subprocess whose timer slot raises
ConnectionResetError dies with 0xC0000409 (STATUS_STACK_BUFFER_OVERRUN) and prints
nothing -- the same mechanism the root conftest documents for the test runner. So the
user sees the emulator vanish, with no error to report.

🎯 BOTH WAYS A PEER CAN VANISH ARE TESTED, because they are different events on the
wire and only one of them used to be noticed at all:
  * a clean FIN  -- the player quit, or went back to the library;
  * an RST       -- the process was killed, the machine slept, a NAT forgot the mapping.
MEASURED before the fix: ConnectionResetError out of the FIRST pump after the peer
left, in BOTH scenarios.
"""

from __future__ import annotations

import socket
import struct

import pytest

from core import link


class FakeMachine:
    """Just enough of the serial ABI for TcpLink -- no core needed."""

    def __init__(self) -> None:
        self._tx = bytearray()
        self.rx = bytearray()
        self.enabled = False

    def serial_set_enabled(self, on: bool) -> None:
        self.enabled = bool(on)

    def serial_read_tx(self, n: int = 64) -> bytes:
        out = bytes(self._tx[:n])
        del self._tx[:n]
        return out

    def serial_write_rx(self, data: bytes) -> None:
        self.rx += data

    def serial_rts(self) -> bool:
        return True

    def queue_tx(self, data: bytes) -> None:
        self._tx += data


@pytest.fixture
def wire():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    ours = socket.create_connection(srv.getsockname())
    theirs, _ = srv.accept()
    srv.close()
    yield ours, theirs
    for s in (ours, theirs):
        try:
            s.close()
        except OSError:
            pass


def _quit(sock: socket.socket) -> None:
    sock.close()                                   # FIN: the player left tidily


def _abort(sock: socket.socket) -> None:
    # SO_LINGER with a zero timeout makes close() send RST instead of FIN -- what a
    # killed process, a slept laptop or a dropped NAT mapping looks like on the wire.
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    sock.close()


@pytest.mark.parametrize("kill,name", [(_quit, "clean FIN"), (_abort, "RST")])
def test_pump_never_raises_when_the_peer_goes(wire, kill, name):
    ours, theirs = wire
    m = FakeMachine()
    lk = link.TcpLink(m, ours)
    m.queue_tx(b"\x01" * 8)
    lk.pump()                                      # a healthy frame first
    assert lk.lost is None, "a working cable must not report itself lost"

    kill(theirs)
    for _ in range(5):                             # several frames after it went
        m.queue_tx(b"\x02" * 8)
        lk.pump()                                  # must not raise -- THIS is the crash
    assert lk.lost, f"the peer went away ({name}) and the link never noticed"


def test_a_lost_link_stops_touching_its_socket(wire):
    # Not just "does not raise": once the peer is gone the link must stop trying, or
    # every frame for the rest of the session pays for a dead socket.
    ours, theirs = wire
    m = FakeMachine()
    lk = link.TcpLink(m, ours)
    _abort(theirs)
    m.queue_tx(b"\x01")
    lk.pump()
    assert lk.lost
    before = (lk.bytes_out, lk.bytes_in)
    m.queue_tx(b"\x02" * 32)
    lk.pump()
    assert (lk.bytes_out, lk.bytes_in) == before, "a dead cable must carry nothing"
    # ...and it leaves the console alone rather than emptying its transmit FIFO into
    # a socket nobody is holding. The shell tears the link down on this same frame
    # (PlayPage.net_link_lost), so nothing accumulates.
    assert bytes(m._tx) == b"\x02" * 32


def test_bytes_already_received_are_still_delivered(wire):
    # The peer's last words count. It sent, then left; both land in the same pump, and
    # dropping them would lose the end of a game's exchange.
    ours, theirs = wire
    m = FakeMachine()
    lk = link.TcpLink(m, ours)
    theirs.sendall(b"BYE")
    _quit(theirs)
    lk.pump()
    assert bytes(m.rx) == b"BYE", f"lost the peer's last bytes: {bytes(m.rx)!r}"
    assert lk.lost
