# NgpCraft link — lobby + relay server

A tiny rendezvous server so two players anywhere can find each other and link two
NGPC consoles over the internet. It keeps the list of open games and relays the
raw serial bytes between paired players, so it works through any NAT/firewall
(no port-forwarding, no public IP needed on the players' side).

- **Pure Python stdlib** — no dependencies.
- **Tiny load** — NGPC serial is a few hundred bytes/second per game, so this fits
  any free tier comfortably.
- Protocol: length-prefixed TCP frames (type 1 = JSON control, type 2 = serial
  relay). See the docstring in `lobby_server.py`.

## Run it locally (test before deploying)

```
python lobby_server.py --port 7788
```

Then in the emulator: 🔗 ▸ *Online lobby…*, Server = `127.0.0.1:7788`, Connect.
Open two emulator instances on your PC to try create/join end to end.

## Deploy free — Fly.io (recommended: stays up, raw TCP supported)

1. Install flyctl and log in: https://fly.io/docs/hands-on/
2. From this `server/` folder:
   ```
   fly launch --no-deploy      # pick a unique app name; keep the provided fly.toml
   fly deploy
   ```
3. Your address is `your-app-name.fly.dev:7788`. Put that in the lobby's *Server*
   field. Share it with your friend.

`fly.toml` keeps one small machine always running so the lobby is never cold.

## Deploy free — Oracle Cloud "Always Free" VM (alternative)

1. Create an Always-Free VM (Ampere/AMD), open TCP port 7788 in the security list
   AND the instance firewall (`sudo firewall-cmd --add-port=7788/tcp --permanent`).
2. Copy `lobby_server.py` to the VM, run it (Python 3.10+ is enough):
   ```
   python3 lobby_server.py --port 7788
   ```
   For a long-running service, wrap it in a systemd unit or `tmux`/`screen`.
3. Your address is `<public-ip>:7788`.

## Notes

- The two players must run a **compatible game** (same title) — the lobby shows
  each game's name for that reason. It links whatever cartridge each side is
  currently running.
- Latency: great for turn-based / trade games; real-time fighters feel it beyond
  a low-latency connection (same as any netplay).
- No accounts, no database — games live only while their host is connected.
