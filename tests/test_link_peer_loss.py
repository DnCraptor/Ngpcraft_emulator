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

import select
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


def _arrived(sock: socket.socket, timeout: float = 5.0) -> None:
    """Wait until the kernel has something to report on `sock` -- data, FIN or RST.

    ⛔ WITHOUT THIS THE TEST MEASURES THE CLOCK, NOT THE CODE, and it did: written and
    run only on Windows, it passed there and failed on the macOS CI runner. On a Windows
    loopback the peer's close lands before the next Python statement; on macOS and Linux
    it does not, so five pumps in a row all completed BEFORE the FIN was processed and
    the test read "the link never noticed". The proof it was the clock and not the link:
    the same run also lost three bytes the peer had definitely already sent.

    A real game pumps once per FRAME (~16 ms), so it sees the loss within a frame or
    two on any platform -- what the test demanded, microseconds, was not something the
    product ever promised. The wait is bounded and generous, so a link that genuinely
    never notices still fails rather than hangs.
    """
    select.select([sock], [], [], timeout)


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
        _arrived(ours)
        m.queue_tx(b"\x02" * 8)
        lk.pump()                                  # must not raise -- THIS is the crash
        if lk.lost:
            break
    assert lk.lost, f"the peer went away ({name}) and the link never noticed"


def test_a_lost_link_stops_touching_its_socket(wire):
    # Not just "does not raise": once the peer is gone the link must stop trying, or
    # every frame for the rest of the session pays for a dead socket.
    ours, theirs = wire
    m = FakeMachine()
    lk = link.TcpLink(m, ours)
    _abort(theirs)
    _arrived(ours)
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
    _arrived(ours)
    lk.pump()
    assert bytes(m.rx) == b"BYE", f"lost the peer's last bytes: {bytes(m.rx)!r}"
    assert lk.lost


# ---- the same events, without a network -----------------------------------
# ⚡ WHY A FAKE SOCKET AS WELL. The tests above use real ones, so WHEN each event lands
# is the kernel's business, and the two families of kernel disagree: on Windows the
# peer's close is visible immediately, on macOS/Linux it is not. Written and run only
# on Windows, these tests passed locally and failed on the macOS runner -- not because
# the link was wrong there, but because the test pumped faster than the FIN travelled.
# This one scripts the POSIX ORDER by hand, so the behaviour is pinned from any machine.


class _ScriptedSocket:
    """A socket that answers a fixed script, one entry per call.

    `b"..."` = data, `b""` = the peer's FIN, an exception class = it is raised.
    `EAGAIN` is spelled BlockingIOError, which is what a POSIX non-blocking socket
    says while the FIN is still in flight -- the state the CI hit and Windows skips.
    """

    def __init__(self, recv_script, send_script=()):
        self.recv_script = list(recv_script)
        self.send_script = list(send_script)
        self.closed = False
        self.sent = bytearray()

    def setblocking(self, _flag): pass
    def close(self): self.closed = True

    def _step(self, script):
        step = script.pop(0) if script else BlockingIOError
        if isinstance(step, type) and issubclass(step, Exception):
            raise step()
        return step

    def recv(self, _n):
        return self._step(self.recv_script)

    def sendall(self, data):
        self._step(self.send_script)
        self.sent += data


def test_a_posix_style_departure_is_noticed_and_never_raises():
    # The exact sequence the macOS runner produced: several pumps where nothing has
    # arrived yet and sends still succeed, and only then the FIN.
    sock = _ScriptedSocket(recv_script=[BlockingIOError, BlockingIOError,
                                        BlockingIOError, b"BYE", b""])
    m = FakeMachine()
    lk = link.TcpLink(m, sock)
    for _ in range(6):
        m.queue_tx(b"\x02")
        lk.pump()                       # must never raise, on any platform
    assert lk.lost, "a POSIX FIN went unnoticed"
    assert bytes(m.rx) == b"BYE", "the peer's last bytes were dropped"
    assert sock.closed, "the dead socket was not released"


def test_a_send_that_fails_late_is_noticed_and_never_raises():
    # POSIX again: the first send after the peer left is accepted, and it is a LATER
    # one that reports EPIPE. Windows raises on the first, which is why only scripting
    # this can pin it.
    sock = _ScriptedSocket(recv_script=[BlockingIOError] * 10,
                           send_script=[None, None, BrokenPipeError])
    m = FakeMachine()
    lk = link.TcpLink(m, sock)
    for _ in range(4):
        m.queue_tx(b"\x02")
        lk.pump()
    assert lk.lost, "a broken pipe went unnoticed"
