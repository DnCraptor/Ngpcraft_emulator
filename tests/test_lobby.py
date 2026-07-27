"""Online lobby + relay, end to end in-process.

Starts the real lobby server on an ephemeral port in a background asyncio thread,
connects two real LobbyClients, and drives the full flow: create -> list -> join,
then relays serial bytes between two consoles through the server. Nothing is
deployed; this proves the whole online path on localhost.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "server"))
import lobby_server  # noqa: E402

from core.lobby import LobbyClient, LobbyLink  # noqa: E402

BIOS = REPO / "bios.bin"
ROM = REPO / "tests" / "roms" / "link_probe.ngc"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _start_server():
    """Run the lobby server on an ephemeral port in a daemon thread; return port."""
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
    assert ready.wait(5), "server did not start"
    return box["port"]


def _wait(app, pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_lobby_create_list_join(app):
    port = _start_server()
    events = {"host_created": None, "guest_list": None,
              "host_joined": None, "guest_joined": None}

    host = LobbyClient("127.0.0.1", port, "Alice")
    host.created.connect(lambda room: events.__setitem__("host_created", room))
    host.joined.connect(lambda o: events.__setitem__("host_joined", o))
    host.start()
    assert _wait(app, lambda: host._sock is not None)

    host.create("Alice's room", "Baseball Stars", public=True)
    assert _wait(app, lambda: events["host_created"]), "no room code"
    room = events["host_created"]

    guest = LobbyClient("127.0.0.1", port, "Bob")
    guest.game_list.connect(lambda g: events.__setitem__("guest_list", g))
    guest.joined.connect(lambda o: events.__setitem__("guest_joined", o))
    guest.start()
    assert _wait(app, lambda: guest._sock is not None)

    guest.refresh()
    assert _wait(app, lambda: events["guest_list"] is not None)
    listing = events["guest_list"]
    assert any(g["room"] == room and g["creator"] == "Alice"
               and g["game"] == "Baseball Stars" for g in listing)

    guest.join(room)
    assert _wait(app, lambda: events["host_joined"] and events["guest_joined"])
    assert events["guest_joined"].get("role") == "guest"
    assert events["host_joined"].get("peer") == "Bob"

    host.close(); guest.close()


def test_lobby_password_protects_private_game(app):
    port = _start_server()
    box = {"created": None, "error": None, "joined": None}

    host = LobbyClient("127.0.0.1", port, "Host")
    host.created.connect(lambda r: box.__setitem__("created", r))
    host.start()
    assert _wait(app, lambda: host._sock is not None)
    host.create("private", "Game", public=False, password="secret")
    assert _wait(app, lambda: box["created"])
    room = box["created"]

    guest = LobbyClient("127.0.0.1", port, "Guest")
    guest.error.connect(lambda m: box.__setitem__("error", m))
    guest.joined.connect(lambda o: box.__setitem__("joined", o))
    guest.start()
    assert _wait(app, lambda: guest._sock is not None)

    guest.join(room, password="wrong")
    assert _wait(app, lambda: box["error"]), "wrong password should be rejected"
    assert box["joined"] is None

    guest.join(room, password="secret")
    assert _wait(app, lambda: box["joined"]), "right password should join"

    host.close(); guest.close()


@pytest.mark.skipif(not (BIOS.exists() and ROM.exists()),
                    reason="needs the retail bios.bin and the probe ROM")
def test_lobby_relays_serial_between_two_consoles(app):
    from core.native_session import NativeSession

    port = _start_server()
    box = {"host_joined": None, "guest_joined": None, "room": None}

    host = LobbyClient("127.0.0.1", port, "P1")
    host.created.connect(lambda r: box.__setitem__("room", r))
    host.joined.connect(lambda o: box.__setitem__("host_joined", o))
    host.start()
    assert _wait(app, lambda: host._sock is not None)
    host.create("room", "probe", public=True)
    assert _wait(app, lambda: box["room"])

    guest = LobbyClient("127.0.0.1", port, "P2")
    guest.joined.connect(lambda o: box.__setitem__("guest_joined", o))
    guest.start()
    assert _wait(app, lambda: guest._sock is not None)
    guest.join(box["room"])
    assert _wait(app, lambda: box["host_joined"] and box["guest_joined"])

    a = NativeSession(ROM, bios_path=BIOS, autosave=False)
    b = NativeSession(ROM, bios_path=BIOS, autosave=False)
    link_a = LobbyLink(a.machine, host)
    link_b = LobbyLink(b.machine, guest)
    try:
        for _ in range(500):
            a.machine.write(0x00B0, bytes([0x11]))
            b.machine.write(0x00B0, bytes([0x22]))
            a.run_frames(1)
            b.run_frames(1)
            link_a.pump()
            link_b.pump()
            app.processEvents()          # let the socket threads move bytes
        assert a.machine.read(0x400A, 1)[0] == 0x22   # P1 got P2's byte via the server
        assert b.machine.read(0x400A, 1)[0] == 0x11
    finally:
        link_a.disconnect()
        link_b.disconnect()


# --- latency: what makes an online game run at full speed ---------------------
# A link game sends a byte and BLOCKS until the answer comes back, so the emulated
# game advances at one exchange per round trip. Latency we add ourselves is taken
# straight off the game's speed -- and only the game's: the sound driver runs free,
# which is why a slowed-down link game still sounds perfectly normal (issue #14.1).
# These two tests pin the mechanisms, not a stopwatch, so they cannot go flaky.

def test_queuing_a_frame_wakes_the_socket_thread():
    """A byte handed over while the thread sits in select() must not wait for the
    timeout. On Windows that timeout is quantised to the ~15.6 ms scheduler tick, so
    a 20 ms select is really ~31 ms -- measured, on loopback, with no network at all.
    """
    import select as _select

    c = LobbyClient("127.0.0.1", 1, "nobody")     # never started: no thread to race
    assert not _select.select([c._wake_r], [], [], 0)[0], "nothing queued yet"

    c.send_serial(b"\x42")
    assert _select.select([c._wake_r], [], [], 0)[0], (
        "send_serial must nudge the socket thread out of select()")

    c._wake_r.recv(64)
    c._send_control({"op": "list"})               # control frames travel the same way
    assert _select.select([c._wake_r], [], [], 0)[0]
    c.close()


def test_a_full_kernel_buffer_neither_drops_bytes_nor_kills_the_link(app):
    """`sendall()` on a NON-blocking socket raises BlockingIOError as soon as the
    kernel buffer is full -- and BlockingIOError is an OSError, so the loop read a
    momentary hiccup as "the link dropped" AND lost the frame it had already taken
    off the queue. send() reports what it took and the rest waits for the next pass.
    """
    class Trickle:
        """A socket that accepts one byte at a time, refusing every other call."""

        def __init__(self, sock):
            self._s = sock
            self._refuse = True

        def send(self, data):
            self._refuse = not self._refuse
            if self._refuse:
                raise BlockingIOError(10035, "would block")
            return self._s.send(data[:1])

        def sendall(self, data):
            """What the stdlib does on a non-blocking socket: loop over send() and
            let the BlockingIOError out. Spelled out here so this test exercises the
            OLD code path too -- delegating it to the real socket would have let the
            bug through untouched."""
            while data:
                data = data[self.send(data):]

        def __getattr__(self, name):
            return getattr(self._s, name)

    port = _start_server()
    box = {"room": None, "host_joined": None, "guest_joined": None, "lost": None}

    host = LobbyClient("127.0.0.1", port, "P1")
    host.created.connect(lambda r: box.__setitem__("room", r))
    host.joined.connect(lambda o: box.__setitem__("host_joined", o))
    host.disconnected.connect(lambda why: box.__setitem__("lost", why))
    host.start()
    assert _wait(app, lambda: host._sock is not None)
    host.create("room", "probe", public=True)
    assert _wait(app, lambda: box["room"])

    guest = LobbyClient("127.0.0.1", port, "P2")
    guest.joined.connect(lambda o: box.__setitem__("guest_joined", o))
    guest.start()
    assert _wait(app, lambda: guest._sock is not None)
    guest.join(box["room"])
    assert _wait(app, lambda: box["host_joined"] and box["guest_joined"])

    host._sock = Trickle(host._sock)              # from here on, sending is painful
    payload = bytes(range(32))
    host.send_serial(payload)

    assert _wait(app, lambda: len(guest._rx_serial) >= len(payload), timeout=10), (
        f"only {len(guest._rx_serial)}/{len(payload)} bytes crossed")
    assert guest.read_serial() == payload, "bytes must arrive whole and in order"
    assert box["lost"] is None, "a full send buffer is not a lost connection"

    host.close(); guest.close()


def test_the_relay_hop_has_nagle_off():
    """Both clients set TCP_NODELAY, but a relayed byte crosses TWO connections and
    the second one is the server's. Left on the default, Nagle holds each small write
    until the previous is acknowledged and the peer's delayed ACK sits on that for
    ~40 ms -- on traffic that is nothing but small writes."""
    import socket as _socket

    # A REAL TCP pair, not socketpair(): on Linux and macOS that gives an AF_UNIX pair,
    # where TCP_NODELAY does not exist and getsockopt raises "operation not supported".
    # (On Windows it is emulated over AF_INET, which is why this passed there and only
    # there.) The option under test is a TCP option, so the test needs a TCP socket.
    srv = _socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    b = _socket.create_connection(srv.getsockname())
    a, _ = srv.accept()
    srv.close()

    class Reader:
        async def readexactly(self, n):           # the client hangs up at once
            raise asyncio.IncompleteReadError(b"", n)

    class Writer:
        def get_extra_info(self, key):
            return a if key == "socket" else ("127.0.0.1", 0)

        def close(self):
            pass

    try:
        assert a.getsockopt(_socket.IPPROTO_TCP, _socket.TCP_NODELAY) == 0
        asyncio.run(lobby_server.client_task(lobby_server.Lobby(), Reader(), Writer()))
        assert a.getsockopt(_socket.IPPROTO_TCP, _socket.TCP_NODELAY) != 0, (
            "the server must disable Nagle on every accepted connection")
    finally:
        a.close(); b.close()
