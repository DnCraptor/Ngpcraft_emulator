"""Client for the NgpCraft link lobby + relay server (server/lobby_server.py).

`LobbyClient` owns a background socket thread and speaks the length-prefixed
frame protocol. The Qt side calls create()/refresh()/join() and receives lobby
updates via signals; serial bytes are exchanged through thread-safe buffers that
`LobbyLink.pump()` moves in and out of the emulated console each frame.

Wire protocol (mirrors the server): header = 1 byte TYPE + 4 bytes big-endian
LENGTH, then payload. TYPE 1 = JSON control, TYPE 2 = serial relay bytes.
"""

from __future__ import annotations

import json
import queue
import select
import socket
import threading
from collections import deque

from PyQt6.QtCore import QObject, pyqtSignal

from core.link_debug import deliver_injected

FRAME_CONTROL = 1
FRAME_RELAY = 2
_HDR = 5


def _frame(ftype: int, payload: bytes) -> bytes:
    return bytes([ftype]) + len(payload).to_bytes(4, "big") + payload


class LobbyClient(QObject):
    """A connection to the lobby server. Signals are emitted from the socket
    thread; Qt delivers them to the main thread via queued connections."""

    connected = pyqtSignal()
    disconnected = pyqtSignal(str)     # reason
    game_list = pyqtSignal(list)       # [{"room","name","game","creator","private"}]
    created = pyqtSignal(str)          # room code
    joined = pyqtSignal(dict)          # {"role","peer","game"?,"name"?}
    peer_left = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, host: str, port: int, pseudo: str):
        super().__init__()
        self._host = host
        self._port = port
        self._pseudo = pseudo[:24]
        self._sock: socket.socket | None = None
        self._out: queue.Queue[bytes] = queue.Queue()
        self._rx_serial: deque[int] = deque()      # received serial bytes
        self._running = False
        self._thread: threading.Thread | None = None
        # ⚡ THE WAKE-UP PIPE -- this is what keeps an online game at full speed.
        # The socket thread waits in select(); without a second thing to wait ON, a
        # byte queued from the Qt thread just after it went to sleep sat there for a
        # whole select timeout. That is not a rounding error: Windows quantises a
        # select timeout to the ~15.6 ms scheduler tick, so a 20 ms wait becomes ~31 ms,
        # each way, on top of the real network. Measured on loopback (no network at
        # all), one relayed byte took a median of 31 ms to cross -- 62 ms per exchange
        # that the wire never charged us for. A link game exchanges a byte per frame
        # and BLOCKS on the answer, so the game (and only the game -- the sound driver
        # runs free, which is why the audio still sounds right) runs at a fraction of
        # its speed. send_serial() now writes one byte here, select() returns at once,
        # and the frame leaves in microseconds.
        self._wake_r, self._wake_w = socket.socketpair()
        self._wake_w.setblocking(False)            # never stall the Qt thread
        self._wake_r.setblocking(False)

    # ---- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._running = False
        self._wake()                       # so the thread leaves select() now
        try:
            if self._sock is not None:
                self._sock.close()
        except OSError:
            pass

    def _wake(self) -> None:
        """Nudge the socket thread out of select(). Called from the Qt thread."""
        try:
            self._wake_w.send(b"\x01")
        except OSError:
            pass                           # full (thread is behind) or closed: harmless

    # ---- control API (called from the Qt thread) ---------------------------
    def _send_control(self, obj: dict) -> None:
        self._out.put(_frame(FRAME_CONTROL, json.dumps(obj).encode("utf-8")))
        self._wake()

    def create(self, name: str, game: str, public: bool, password: str = "") -> None:
        self._send_control({"op": "create", "pseudo": self._pseudo, "name": name,
                            "game": game, "public": public, "password": password})

    def refresh(self) -> None:
        self._send_control({"op": "list"})

    def join(self, room: str, password: str = "") -> None:
        self._send_control({"op": "join", "pseudo": self._pseudo,
                            "room": room, "password": password})

    def leave(self) -> None:
        self._send_control({"op": "leave"})

    # ---- serial relay (called from the Qt tick via LobbyLink) --------------
    def send_serial(self, data: bytes) -> None:
        if data:
            self._out.put(_frame(FRAME_RELAY, data))
            self._wake()

    def read_serial(self) -> bytes:
        n = len(self._rx_serial)
        if not n:
            return b""
        return bytes(self._rx_serial.popleft() for _ in range(n))

    # ---- socket thread -----------------------------------------------------
    def _run(self) -> None:
        try:
            self._sock = socket.create_connection((self._host, self._port), timeout=15)
        except Exception as e:  # noqa: BLE001
            self.disconnected.emit(str(e))
            return
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock.setblocking(False)
        self._running = True
        self._send_control({"op": "hello", "pseudo": self._pseudo})
        self.connected.emit()

        buf = bytearray()
        pending = bytearray()          # queued frames not yet accepted by the kernel
        reason = "closed"
        while self._running:
            # 1) send anything queued
            try:
                while True:
                    pending += self._out.get_nowait()
            except queue.Empty:
                pass
            if pending:
                try:
                    # NOT sendall(): this socket is non-blocking, where sendall's
                    # behaviour is undefined -- it raises BlockingIOError the moment
                    # the kernel buffer is full, and BlockingIOError IS an OSError, so
                    # a momentarily-full buffer used to be read as "the link dropped"
                    # AND ate the bytes it had already taken off the queue. send()
                    # reports what it took; the rest waits here for the next pass.
                    sent = self._sock.send(pending)
                    del pending[:sent]
                except (BlockingIOError, InterruptedError):
                    pass
                except OSError as e:
                    reason = str(e); break
            # 2) wait for: something to read, room to write (only while we owe bytes),
            # or the Qt thread waking us because it just queued a frame.
            try:
                r, _, _ = select.select(
                    [self._sock, self._wake_r], [self._sock] if pending else [],
                    [], 0.02)
            except OSError:
                break
            if self._wake_r in r:
                try:
                    self._wake_r.recv(4096)        # drain the nudges; the queue is
                except OSError:                    # what actually carries the data
                    pass
            if self._sock in r:
                try:
                    chunk = self._sock.recv(8192)
                except (BlockingIOError, InterruptedError):
                    continue
                except OSError as e:
                    reason = str(e); break
                if not chunk:
                    reason = "server closed"; break
                buf.extend(chunk)
                self._drain_frames(buf)
        self._running = False
        for s in (self._wake_r, self._wake_w):
            try:
                s.close()
            except OSError:
                pass
        self.disconnected.emit(reason)

    def _drain_frames(self, buf: bytearray) -> None:
        while len(buf) >= _HDR:
            ftype = buf[0]
            length = int.from_bytes(buf[1:5], "big")
            if len(buf) < _HDR + length:
                return
            payload = bytes(buf[_HDR:_HDR + length])
            del buf[:_HDR + length]
            if ftype == FRAME_RELAY:
                self._rx_serial.extend(payload)
            elif ftype == FRAME_CONTROL:
                self._dispatch(payload)

    def _dispatch(self, payload: bytes) -> None:
        try:
            obj = json.loads(payload.decode("utf-8"))
        except Exception:
            return
        op = obj.get("op")
        if op == "list":
            self.game_list.emit(obj.get("games", []))
        elif op == "created":
            self.created.emit(str(obj.get("room", "")))
        elif op == "joined":
            self.joined.emit(obj)
        elif op == "peer_left":
            self.peer_left.emit()
        elif op == "error":
            self.error.emit(str(obj.get("msg", "error")))


class LobbyLink:
    """Serial transport over the lobby relay. Same interface as core.link.TcpLink
    (pump / disconnect / bytes_out / bytes_in) so PlayPage.attach_net_link reuses
    the same code path."""

    def __init__(self, machine, client: LobbyClient, *, monitor=None):
        self.machine = machine
        self.client = client
        # Optional core.link_debug.LinkMonitor -- the same tap the local and LAN
        # links carry, so the debugger's Link tab reads a lobby game too.
        self.monitor = monitor
        self.bytes_out = 0
        self.bytes_in = 0
        self.machine.serial_set_enabled(True)

    def pump(self) -> None:
        tx = self.machine.serial_read_tx(256)
        # Call the monitor even on an empty drain: it may be holding bytes back
        # for a simulated latency, and this is where they are released.
        out = self.monitor.on_tx(tx) if self.monitor is not None else tx
        if out:
            self.client.send_serial(out)
            self.bytes_out += len(out)
        rx = self.client.read_serial()
        if rx:
            self.machine.serial_write_rx(rx)
            self.bytes_in += len(rx)
            if self.monitor is not None:
                self.monitor.on_rx(rx)
        deliver_injected(self.machine, self.monitor)

    def disconnect(self) -> None:
        try:
            self.machine.serial_set_enabled(False)
        except Exception:
            pass
        self.client.leave()
        self.client.close()
