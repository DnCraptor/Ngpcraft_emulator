"""NgpCraft link-cable LOBBY + RELAY server.

A tiny rendezvous server so two players anywhere on the internet can find each
other and link two NGPC consoles. It does two jobs:

  1. LOBBY   -- keeps the list of open games (name, game title, creator,
                public/private) and hands it to whoever asks.
  2. RELAY   -- once two players are paired, it forwards the raw serial bytes
                between them, so it works through any NAT/firewall (neither
                player needs a public IP or port-forwarding).

The link is a byte pipe (no shared simulation, no rollback), and NGPC serial is
a few hundred bytes/second, so the load is tiny -- this fits any free tier.

Wire protocol: length-prefixed frames, stdlib only (no dependencies).
  header = 1 byte TYPE + 4 bytes big-endian LENGTH, then LENGTH bytes payload.
    TYPE 1 = JSON control message (utf-8)
    TYPE 2 = serial relay bytes (forwarded verbatim to the paired peer)

Run:  python lobby_server.py [--host 0.0.0.0] [--port 7788]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import socket

FRAME_CONTROL = 1
FRAME_RELAY = 2
MAX_FRAME = 1 << 16          # 64 KiB is far more than any control/relay frame


class Session:
    """One connected client."""

    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer
        self.pseudo = "?"
        self.room: str | None = None      # the room this client HOSTS, if any
        self.peer: "Session | None" = None
        self.lock = asyncio.Lock()        # serialise writes to this socket

    async def send(self, ftype: int, payload: bytes) -> None:
        header = bytes([ftype]) + len(payload).to_bytes(4, "big")
        async with self.lock:
            self.writer.write(header + payload)
            await self.writer.drain()

    async def send_control(self, obj: dict) -> None:
        await self.send(FRAME_CONTROL, json.dumps(obj).encode("utf-8"))


class Lobby:
    def __init__(self):
        # room code -> {"host": Session, "name", "game", "creator", "password",
        #               "public", "mode", "delay"}
        self.games: dict[str, dict] = {}

    def open_list(self) -> list[dict]:
        out = []
        for room, g in self.games.items():
            if g["host"].peer is None:        # still waiting for a player
                out.append({
                    "room": room, "name": g["name"], "game": g["game"],
                    "creator": g["creator"], "private": bool(g["password"]),
                    "public": g["public"],
                    # WHICH LINK the room is for. The relay carries opaque bytes either
                    # way -- but the two clients have to agree on what those bytes MEAN,
                    # and only the host knows. "cable" = the console's own serial
                    # stream; "mirror" = core/netplay's session records. `delay` is the
                    # mirror's input delay, which must be identical on both PCs (the
                    # handshake refuses a mismatch), so the joiner adopts the host's.
                    "mode": g["mode"], "delay": g["delay"],
                })
        # public games (and private ones so a friend with the code can still see it)
        return out

    def new_room(self) -> str:
        while True:
            code = secrets.token_hex(3).upper()   # 6 hex chars, e.g. "9F3A2C"
            if code not in self.games:
                return code


async def handle_control(lobby: Lobby, s: Session, obj: dict) -> None:
    op = obj.get("op")
    if op == "hello":
        s.pseudo = str(obj.get("pseudo", "?"))[:24]

    elif op == "create":
        s.pseudo = str(obj.get("pseudo", s.pseudo))[:24]
        room = lobby.new_room()
        lobby.games[room] = {
            "host": s,
            "name": str(obj.get("name", "game"))[:32],
            "game": str(obj.get("game", "?"))[:32],
            "creator": s.pseudo,
            "password": str(obj.get("password", "")),
            "public": bool(obj.get("public", True)),
            # Absent -> "cable": a client that predates the mirror rooms says nothing
            # here, and the cable is what it means.
            "mode": "mirror" if obj.get("mode") == "mirror" else "cable",
            "delay": max(0, min(30, int(obj.get("delay", 0) or 0))),
        }
        s.room = room
        await s.send_control({"op": "created", "room": room})

    elif op == "list":
        await s.send_control({"op": "list", "games": lobby.open_list()})

    elif op == "join":
        room = str(obj.get("room", "")).upper()
        g = lobby.games.get(room)
        if g is None or g["host"].peer is not None:
            await s.send_control({"op": "error", "msg": "game not found or already full"})
            return
        if g["password"] and str(obj.get("password", "")) != g["password"]:
            await s.send_control({"op": "error", "msg": "wrong password"})
            return
        # pair them
        host = g["host"]
        host.peer = s
        s.peer = host
        s.pseudo = str(obj.get("pseudo", s.pseudo))[:24]
        await host.send_control({"op": "joined", "peer": s.pseudo, "role": "host",
                                 "mode": g["mode"], "delay": g["delay"]})
        await s.send_control({"op": "joined", "peer": host.pseudo, "role": "guest",
                              "game": g["game"], "name": g["name"],
                              "mode": g["mode"], "delay": g["delay"]})

    elif op == "leave":
        await drop_pairing(lobby, s)


async def drop_pairing(lobby: Lobby, s: Session) -> None:
    """Undo a pairing / remove a hosted game and notify the peer."""
    peer = s.peer
    if peer is not None:
        peer.peer = None
        try:
            await peer.send_control({"op": "peer_left"})
        except Exception:
            pass
        s.peer = None
    if s.room and s.room in lobby.games:
        del lobby.games[s.room]
        s.room = None


async def read_frame(reader):
    header = await reader.readexactly(5)
    ftype = header[0]
    length = int.from_bytes(header[1:5], "big")
    if length > MAX_FRAME:
        raise ValueError("frame too large")
    payload = await reader.readexactly(length) if length else b""
    return ftype, payload


async def client_task(lobby: Lobby, reader, writer):
    s = Session(reader, writer)
    peer_addr = writer.get_extra_info("peername")
    # ⚡ TURN NAGLE OFF ON THE RELAY HOP. Both clients set TCP_NODELAY on their end,
    # but a relayed byte crosses TWO connections and the second one is this socket,
    # which asyncio leaves on the default. Nagle holds a small write until the
    # previous one is acknowledged, and the peer's delayed ACK holds that ACK back
    # ~40 ms: exactly the pathological pair, on traffic that is nothing BUT small
    # writes (one serial byte in a 6-byte frame). Latency here is not a comfort
    # setting -- a link game blocks on the answer, so every millisecond added to the
    # round trip is taken off the game's speed.
    sock = writer.get_extra_info("socket")
    if sock is not None:
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
    try:
        while True:
            ftype, payload = await read_frame(reader)
            if ftype == FRAME_CONTROL:
                try:
                    obj = json.loads(payload.decode("utf-8"))
                except Exception:
                    continue
                await handle_control(lobby, s, obj)
            elif ftype == FRAME_RELAY:
                peer = s.peer
                if peer is not None:
                    try:
                        await peer.send(FRAME_RELAY, payload)
                    except Exception:
                        pass
            # unknown frame types are ignored
    except (asyncio.IncompleteReadError, ConnectionError, ValueError):
        pass
    finally:
        await drop_pairing(lobby, s)
        try:
            writer.close()
        except Exception:
            pass
    _ = peer_addr


async def main_async(host: str, port: int) -> None:
    lobby = Lobby()
    server = await asyncio.start_server(
        lambda r, w: client_task(lobby, r, w), host, port)
    addrs = ", ".join(str(sock.getsockname()) for sock in server.sockets)
    print(f"NgpCraft lobby server listening on {addrs}", flush=True)
    async with server:
        await server.serve_forever()


def main() -> None:
    ap = argparse.ArgumentParser(description="NgpCraft link lobby + relay server")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=7788)
    args = ap.parse_args()
    try:
        asyncio.run(main_async(args.host, args.port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
