# Link cable (serial channel 0) + its debug tools

Purpose:
- describe what the emulated NGPC link cable IS, so the transports and the
  debugger are read against the same model
- specify the read-only observation surface (`ngpc_serial_state`) and the
  Python-side instrumentation (`core/link_debug.py`) the debugger's **Link**
  tab is built on
- state which parts are hardware fidelity and which parts are deliberately
  synthetic (loopback, injection, impairment), so neither is mistaken for the
  other

⚡ **2026-07-25 — the cable no longer needs `bios.bin`.** The COM machinery above the
wire (the `0x10`–`0x1A` system calls, the rings at `0x6C80`/`0x6CC0`, the RX count at
`0x6D01` that SDK code reads directly, and the two serial ISRs) is implemented in the
clean-room BIOS image as well: `hle_bios/README.md`. Two consoles on that image exchange
bytes at the same rate as on the retail BIOS (1500 frames: 3083 vs 3070, three in flight
either way). Two things make or break it, both measured: the serial vectors are
**cross-wired** versus their SDK names — `0x18` receives, `0x19` transmits — and
`COMINIT` must `ei 6`, because the hand-off leaves the CPU at IFF 7 and masks them.
`tests/test_link_cable.py` covers both BIOSes, including an anti-loopback assertion.

Source references:
- `cpp/src/machine.hpp` (`Machine::serial_tick`, port 0xB1 read8), `cpp/src/core.cpp` (ABI)
- `hle_bios/gen_crt0.py` (the clean-room COM driver + serial ISRs)
- `core/link.py` (transports), `core/link_debug.py` (instrumentation), `core/lobby.py`
- `ngpc_debug.py` (the Link tab), `ngpc_shell.py` (`PlayPage._pump_link`)
- project memory: link cable / multiplayer, CFC link stall

## 1. The model

**The cable is a byte pipe between two independent consoles, not a shared
simulation.** Each console runs its own copy of the game; the only thing they
share is the bytes on the wire. There is therefore no determinism requirement,
no lockstep and no rollback — a link is a reliable, ordered byte relay that
honours the hardware handshake. Latency tolerance is naturally high: the BIOS
was written for a 19200 bps partner that may be slow.

Hardware path, per console:

```
game -> BIOS COM routine -> SC0BUF (0x50) write -> [core] one baud-time
     -> transmit FIFO -> HOST RELAY -> peer's receive FIFO
     -> [core] RTS gate (0xB2 bit0) -> SC0BUF read -> INTRX0 -> BIOS RX ring
```

Consequences that matter:

- **Game-agnostic.** The bridge lives at the BIOS COM layer, so every
  link-capable cartridge works with no per-game code.
- **Interrupt vectors are CROSSED on the retail BIOS.** Vector `0x18` FILLS the
  receive ring, `0x19` DRAINS the transmit ring. Raise them by BEHAVIOUR, not by
  the SDK's names.
- **0xB1 bit2 is the cable-DETECT input** (0 = a peer is connected). Games gate
  their handshake on it; it is forced from the link state, never from the I/O
  page.
- **Flow control.** Our RTS = 0xB2 bit0 (0 = ready to receive). The peer's RTS
  drives our CTS0 pin, which halts our transmitter when the game set
  SC0MOD<CTSE>.

## 2. Transports

All three present the same interface (`pump()` / `disconnect()` /
`bytes_out` / `bytes_in`) and all three accept an optional monitor:

| transport | where | use |
|---|---|---|
| `core.link.InProcessLink` | two machines in one process | two players on one PC |
| `core.link.TcpLink` | one machine + a socket | LAN / direct host-join |
| `core.lobby.LobbyLink` | one machine + the relay server | online lobby |
| `core.link_debug.LoopbackLink` | one machine, no peer | **debug only** |

The shell's local 2-player relay is inline in `PlayPage._pump_link` rather than
using `InProcessLink`; it carries the same tap.

**A linked frame is relayed every `PlayPage.LINK_SLICE` = 400 instructions, and that
number is a correctness figure, not a comfort setting.** Each page pumps after ITS OWN
frame and the two tick one after the other, so player 1's bytes reach player 2 before
player 2 runs — but player 2's answer waits for the next frame. The Last Blade's
handshake times out on exactly that one frame: measured on the game's own "message
received" byte `0x4B9D`, the console that speaks first waits from +0 to +6 for the
reply and gives up at +6, after which both consoles show **LINK ERROR**.

| slice | message received | consumed | link driver at +900 |
|---|---|---|---|
| whole frame | player 2 only, +6 | **never** | dead (`0xFF`) → LINK ERROR |
| 2000 instructions | player 2 only, +8 | **never** | dead (`0xFF`) |
| **400 instructions** | **both, +4** | **+6** | **alive (`0x14`)** |
| 100 instructions | both, +4 | +6 | alive (`0x14`) |

An earlier attempt used 2000 and judged on the final screen alone; it failed, and the
conclusion drawn from it — "sub-frame relaying does not help" — was wrong. Do not raise
the slice without re-running that table. Slicing is not free (a few percent of host
time, plus the speed table above `_flash_overlay`), so a console with **no peer** keeps
the plain one-call-per-frame path. See also [NETPLAY_MIRROR.md §0](NETPLAY_MIRROR.md).

⚠️ `TcpLink` writes with `send()` plus a pending buffer, **never `sendall()`**: on a
non-blocking socket `sendall` raises `BlockingIOError` the moment the kernel buffer
fills and does not say how much it already handed over, so the old code dropped a whole
write mid-exchange and the peer waited for a packet that was never sent.
`core/lobby.py` had the same fault and was fixed there first.

**There is a second online mode.** Relaying the cable means the game waits for the
network and slows down with it (0.56x speed at a 67 ms round trip, measured). Mirror
netplay runs BOTH consoles on each PC and sends only the controller bytes, so the
cable is local and the latency is spent on input delay instead —
see [NETPLAY_MIRROR.md](NETPLAY_MIRROR.md). Both modes ship; they are mutually
exclusive at runtime (`Shell._one_link_at_a_time`).

Delivery into the receive FIFO is **unconditional**. The core's `serial_tick` is
the authoritative flow-control gate (it only PRESENTS a byte once our RTS is
low), so holding bytes back in the host can strand a handshake byte and read as
"no cable".

## 3. Observation: `ngpc_serial_state` (read-only)

How many bytes crossed is already visible to whoever relays them. What is NOT
visible from Python is **where a byte that is not crossing got stuck**. The core
therefore counts each stage; nothing here feeds back into emulation.

`ngpc_serial_state_t` / `core.native.SerialState`:

- channel: `enabled`, `tx_depth`, `rx_depth`, `tx_busy`, `rx_pending`
- handshake: `cts_high`, `rts_low`, `ctse`, `cts_hold_ticks`, `rts_hold_ticks`
- bytes: `tx_count` (written to SC0BUF) → `wire_count` (shifted out) …
  `rx_queued_count` (pushed at us) → `rx_read_count` (read by the CPU)
- interrupts: `irq_tx_count` (0x19), `irq_rx_count` (0x18)
- registers: `sc0buf`, `sc0cr`, `sc0mod`, `br0cr`, `port_b1`, `port_b2`
  — `port_b1` is presented **as read8 presents it** (detect + sub-battery bits
  forced), not as the raw I/O page byte.

Counters are **per cable session**: `ngpc_serial_set_enabled` zeroes them, so a
reading answers "since this link came up".

The reduction of those counters to one sentence lives in
`DebugWindow._link_verdict_text` and is ordered so that the first test to fire
names the EARLIEST stuck stage: no cable → total silence → held by peer CTS →
held by our own RTS → arrived but no INTRX0 → INTRX0 but never read (and, at
interrupt mask level 6, why: `COMOFFRTS` does `ei 6`) → nothing shifted out →
flowing.

## 4. Instrumentation: `core/link_debug.py`

`LinkMonitor` is a per-CONSOLE tap placed in a relay. `on_tx` sees what leaves,
`on_rx` what arrives, both stamped with the host's frame number and kept in a
bounded ring (`dump()` for hex+ASCII, `raw()` for a byte capture). Attaching one
costs a deque append per frame.

Two of its three powers are deliberately **synthetic** and must never be
confused with hardware behaviour:

- **Injection** (`inject`, `deliver_injected`) — a FAKE PEER, not a fake cable.
  Bytes enter the real receive path (RTS gate → SC0BUF → INTRX0 → BIOS ring), so
  a game that reacts proves the receive chain end to end with no second console.
- **Impairment** (`Impairment`: `delay_frames`, `drop`, `cut`) — applied to the
  OUTGOING direction only, at the console that owns the monitor. The emulated
  cable is instant and lossless; a real online session is neither, and this is
  how a game's tolerance is rehearsed. Order is preserved under latency (every
  byte waits the same number of pumps).

`LoopbackLink` plugs a console into itself (`echo`) or into a wire that never
answers (`sink`). It exercises the whole hardware path on one machine; it is not
a peer, and a game expecting a partner's protocol will not be fooled for long.

## 5. Validation

- `tests/test_link_cable.py` — the transport and the hardware path.
- `tests/test_link_debug.py` — the monitor, the impairments, injection,
  loopback, the counters, the verdict logic, and the gate that matters: on the
  full hardware path (real BIOS COM routines, probe ROM) the core's counters and
  the tap's totals must agree — `wire_count == bytes_tx`,
  `rx_queued_count == bytes_rx` — plus a mid-session cut that really stops the
  peer hearing.
- `tests/test_link_play.py` — the shell's own relay, tapped, with split input.
- `tests/test_shell_ui.py` — the Link tab renders and drives its three pokes.
