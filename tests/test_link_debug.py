"""Link-cable debugging tools: the tap, the fake peer, the broken wire.

Four layers, cheapest first:

1. :class:`core.link_debug.LinkMonitor` on its own -- logging, totals, and the
   deliberate impairments (latency, loss, cut).
2. The monitor wired into a real relay (:class:`core.link.InProcessLink`) with
   fake machines: what the tap sees must be what crossed, and an injected byte
   must reach the console that has no peer to send it.
3. :class:`core.link_debug.LoopbackLink`: a console plugged into itself.
4. The core's own :meth:`NativeMachine.serial_state` counters, held against the
   tap on the full hardware path (real BIOS COM routines, the probe ROM). This
   is the gate that matters: the counters exist to say WHERE a byte stopped, so
   they have to agree with the bytes that actually moved. Skips without the
   retail BIOS.

The verdict text the debugger shows is tested too -- it is the part a user
actually reads, and it is pure logic over the counters, so it costs nothing.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.link import InProcessLink
from core.link_debug import (RX, TX, Impairment, LinkMonitor, LoopbackLink,
                             deliver_injected)

REPO = Path(__file__).resolve().parent.parent
BIOS = REPO / "bios.bin"
ROM = REPO / "tests" / "roms" / "link_probe.ngc"
G_LAST_RX = 0x400A          # probe ROM globals, from its .map (see test_link_cable)


class FakeSerial:
    """The serial slice a relay uses -- same shape as tests/test_link_cable.py."""

    def __init__(self, rts: bool = True):
        self.tx = bytearray()
        self.rx = bytearray()
        self._rts = rts
        self.enabled = False
        self.cts_high = False

    def serial_set_enabled(self, on: bool) -> None:
        self.enabled = on

    def serial_read_tx(self, max_bytes: int = 256) -> bytes:
        out = bytes(self.tx[:max_bytes])
        del self.tx[: len(out)]
        return out

    def serial_write_rx(self, data: bytes) -> None:
        self.rx.extend(data)

    def serial_rts(self) -> bool:
        return self._rts

    def serial_set_cts(self, high: bool) -> None:
        self.cts_high = high


# --------------------------------------------------------------------------- #
# 1. The monitor alone
# --------------------------------------------------------------------------- #
def test_monitor_logs_both_directions_and_totals():
    mon = LinkMonitor()
    mon.frame = 7
    assert mon.on_tx(b"\x01\x02") == b"\x01\x02"     # perfect wire: unchanged
    mon.frame = 9
    mon.on_rx(b"\xAB")

    assert mon.bytes_tx == 2 and mon.bytes_rx == 1
    assert [(e.frame, e.direction, e.data) for e in mon.log] == [
        (7, TX, b"\x01\x02"), (9, RX, b"\xAB")]
    assert mon.raw(TX) == b"\x01\x02" and mon.raw(RX) == b"\xAB"
    dump = mon.dump()
    assert "01 02" in dump and "AB" in dump

    mon.clear()
    assert not mon.log and mon.bytes_tx == 0 and mon.bytes_rx == 0


def test_monitor_cut_cable_swallows_everything():
    mon = LinkMonitor()
    mon.impair = Impairment(cut=True)
    assert mon.on_tx(b"hello") == b""
    assert mon.bytes_dropped == 5
    assert mon.bytes_tx == 5          # it was still SENT -- the wire ate it


def test_monitor_full_loss_drops_every_byte():
    mon = LinkMonitor(seed=1)
    mon.impair = Impairment(drop=1.0)
    assert mon.on_tx(b"abcd") == b""
    assert mon.bytes_dropped == 4


def test_monitor_latency_holds_bytes_then_releases_them_in_order():
    """Two frames of latency: nothing crosses for two pumps, then everything
    does, in the order it was sent."""
    mon = LinkMonitor()
    mon.impair = Impairment(delay_frames=2)

    mon.frame = 0
    assert mon.on_tx(b"\x01") == b""
    mon.frame = 1
    assert mon.on_tx(b"\x02") == b""
    mon.frame = 2                       # byte 1 is due (sent at 0, +2)
    assert mon.on_tx(b"") == b"\x01"
    mon.frame = 3
    assert mon.on_tx(b"") == b"\x02"
    mon.frame = 4
    assert mon.on_tx(b"") == b""        # nothing left held


def test_injected_bytes_reach_the_machine_with_no_peer():
    m = FakeSerial()
    mon = LinkMonitor()
    mon.inject(b"\x99")

    assert deliver_injected(m, mon) == 1
    assert bytes(m.rx) == b"\x99"
    assert mon.bytes_injected == 1
    assert mon.raw(RX) == b"\x99"       # logged as an arrival, which is what it is
    assert deliver_injected(m, mon) == 0    # the queue is drained, not repeated


# --------------------------------------------------------------------------- #
# 2. The monitor inside a real relay
# --------------------------------------------------------------------------- #
def test_relay_tap_sees_what_crossed_from_each_console_view():
    """A monitor belongs to a console, not to the wire: A's monitor records A's
    byte as TX and B's byte as RX, and B's monitor sees the mirror image."""
    a, b = FakeSerial(), FakeSerial()
    mon_a, mon_b = LinkMonitor(), LinkMonitor()
    link = InProcessLink(a, b, monitor_a=mon_a, monitor_b=mon_b)

    a.tx.extend(b"\x11")
    b.tx.extend(b"\x22")
    link.pump()

    assert mon_a.raw(TX) == b"\x11" and mon_a.raw(RX) == b"\x22"
    assert mon_b.raw(TX) == b"\x22" and mon_b.raw(RX) == b"\x11"


def test_relay_impairment_stops_the_bytes_for_real():
    a, b = FakeSerial(), FakeSerial()
    mon_a = LinkMonitor()
    mon_a.impair = Impairment(cut=True)
    link = InProcessLink(a, b, monitor_a=mon_a)

    a.tx.extend(b"\x11")
    b.tx.extend(b"\x22")
    link.pump()

    assert bytes(b.rx) == b""          # A's side is cut...
    assert bytes(a.rx) == b"\x22"      # ...B's is not


def test_relay_delivers_injected_bytes():
    a, b = FakeSerial(), FakeSerial()
    mon_a = LinkMonitor()
    link = InProcessLink(a, b, monitor_a=mon_a)
    mon_a.inject(b"\x77")
    link.pump()
    assert bytes(a.rx) == b"\x77"      # fed to A, the console it was aimed at
    assert bytes(b.rx) == b""


# --------------------------------------------------------------------------- #
# 3. Loopback: one console, no peer
# --------------------------------------------------------------------------- #
def test_loopback_echo_returns_the_bytes_to_the_sender():
    m = FakeSerial()
    mon = LinkMonitor()
    loop = LoopbackLink(m, monitor=mon)
    assert m.enabled                    # arms the hardware path

    m.tx.extend(b"\xA5\x5A")
    loop.pump()

    assert bytes(m.rx) == b"\xA5\x5A"
    assert loop.bytes_out == 2 and loop.bytes_in == 2
    assert mon.raw(TX) == b"\xA5\x5A" and mon.raw(RX) == b"\xA5\x5A"


def test_loopback_sink_drains_without_answering():
    m = FakeSerial()
    loop = LoopbackLink(m, echo=False)
    m.tx.extend(b"\x01\x02")
    loop.pump()
    assert bytes(m.rx) == b""           # the partner has gone silent
    assert loop.bytes_out == 2 and bytes(m.tx) == b""


def test_loopback_disconnect_disarms_the_channel():
    m = FakeSerial()
    LoopbackLink(m).disconnect()
    assert not m.enabled


# --------------------------------------------------------------------------- #
# 4. The core's own counters
# --------------------------------------------------------------------------- #
def test_serial_state_counts_what_the_host_queued():
    from core.native import NativeMachine

    m = NativeMachine(b"\x00" * 0x10000)
    m.serial_set_enabled(True)
    m.serial_write_rx(b"\x41\x42")
    st = m.serial_state().as_dict()

    assert st["enabled"] == 1
    assert st["rx_depth"] == 2 and st["rx_queued_count"] == 2
    assert st["rx_read_count"] == 0        # nothing has run to read them
    assert st["rts_low"] == 1              # 0xB2 bit0 low out of reset


def test_serial_state_reports_cable_detect_as_the_game_reads_it():
    """0xB1 bit2 is the cable-DETECT input (0 = peer connected) -- the line Card
    Fighters' Clash gates its handshake on. The snapshot has to show it the way
    read8 presents it, not the way it happens to sit in the I/O page, or the tab
    would say "no cable" on a working one."""
    from core.native import NativeMachine

    m = NativeMachine(b"\x00" * 0x10000)
    assert m.serial_state().port_b1 & 0x04, "no cable -> detect reads 1"
    m.serial_set_enabled(True)
    st = m.serial_state()
    assert not (st.port_b1 & 0x04), "cable armed -> detect reads 0"
    assert st.port_b1 & 0x02, "the sub-battery bit is forced high, as in read8"


def test_serial_state_counters_reset_with_the_cable():
    """Counters are per-cable-session, so a fresh link starts from a clean
    reading instead of carrying the previous session's totals."""
    from core.native import NativeMachine

    m = NativeMachine(b"\x00" * 0x10000)
    m.serial_set_enabled(True)
    m.serial_write_rx(b"\x01\x02\x03")
    assert m.serial_state().rx_queued_count == 3
    m.serial_set_enabled(False)
    m.serial_set_enabled(True)
    assert m.serial_state().rx_queued_count == 0


requires_rom = pytest.mark.skipif(
    not (BIOS.exists() and ROM.exists()),
    reason="needs the retail bios.bin (gitignored) and the probe ROM",
)


@requires_rom
def test_counters_and_tap_agree_on_the_hardware_path():
    """The gate: two real consoles running the BIOS COM routines, tapped.

    Everything the tap logged as leaving A must be counted by A's core as
    shifted out, and everything it logged as arriving at A must be counted as
    queued -- and the interrupts that carry them must actually have fired. If
    these drift apart, the debugger's Link tab is lying about where bytes stop.
    """
    from core.native_session import NativeSession

    pad_a, pad_b = 0x11, 0x22
    a = NativeSession(ROM, bios_path=BIOS, autosave=False)
    b = NativeSession(ROM, bios_path=BIOS, autosave=False)
    mon_a, mon_b = LinkMonitor(), LinkMonitor()
    link = InProcessLink(a.machine, b.machine, monitor_a=mon_a, monitor_b=mon_b)

    for frame in range(400):
        a.machine.write(0x00B0, bytes([pad_a]))
        b.machine.write(0x00B0, bytes([pad_b]))
        a.run_frames(1)
        b.run_frames(1)
        mon_a.frame = mon_b.frame = frame
        link.pump()

    assert a.machine.read(G_LAST_RX, 1)[0] == pad_b     # the cable still works
    st = a.machine.serial_state().as_dict()

    assert mon_a.bytes_tx > 100 and mon_a.bytes_rx > 100
    assert st["wire_count"] == mon_a.bytes_tx           # what left A, as A counts it
    assert st["rx_queued_count"] == mon_a.bytes_rx      # what arrived, as A counts it
    assert st["irq_tx_count"] > 0 and st["irq_rx_count"] > 0
    assert st["rx_read_count"] > 100                    # the BIOS handler ran
    assert st["enabled"] == 1


@requires_rom
def test_cut_cable_stops_a_running_link():
    """Impairment on a REAL link: cut the wire mid-session and the peer stops
    hearing anything, without either console being torn down."""
    from core.native_session import NativeSession

    a = NativeSession(ROM, bios_path=BIOS, autosave=False)
    b = NativeSession(ROM, bios_path=BIOS, autosave=False)
    mon_a = LinkMonitor()
    link = InProcessLink(a.machine, b.machine, monitor_a=mon_a)

    def run(frames: int) -> None:
        for _ in range(frames):
            a.machine.write(0x00B0, b"\x11")
            b.machine.write(0x00B0, b"\x22")
            a.run_frames(1)
            b.run_frames(1)
            link.pump()

    run(300)
    before = b.machine.serial_state().rx_queued_count
    assert before > 0

    mon_a.impair = Impairment(cut=True)
    run(200)
    assert b.machine.serial_state().rx_queued_count == before   # B hears nothing more
    assert mon_a.bytes_dropped > 0


# --------------------------------------------------------------------------- #
# 5. The verdict line the debugger shows
# --------------------------------------------------------------------------- #
def _state(**over) -> dict:
    st = dict(enabled=1, tx_depth=0, rx_depth=0, tx_busy=0, rx_pending=0,
              cts_high=0, rts_low=1, ctse=0, tx_count=0, wire_count=0,
              rx_queued_count=0, rx_read_count=0, irq_tx_count=0, irq_rx_count=0,
              cts_hold_ticks=0, rts_hold_ticks=0, sc0buf=0, sc0cr=0, sc0mod=0,
              br0cr=0, port_b1=0, port_b2=0)
    st.update(over)
    return st


@pytest.fixture(scope="module")
def verdict():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt6.QtWidgets")
    from ngpc_debug import DebugWindow
    return DebugWindow._link_verdict_text


def test_verdict_names_the_earliest_stuck_stage(verdict):
    # No cable at all is not a fault, just a state.
    text, bad = verdict(_state(enabled=0), 0)
    assert "No cable" in text and not bad

    # Armed but nobody has spoken: both consoles must be on the VS screen.
    text, bad = verdict(_state(), 0)
    assert "silence" in text and not bad

    # A byte is held because the peer says "not ready".
    text, bad = verdict(_state(tx_count=1, tx_busy=1, cts_high=1, ctse=1), 0)
    assert "HELD" in text and bad

    # Bytes are waiting but WE never lowered RTS.
    text, bad = verdict(_state(tx_count=1, rx_depth=3, rts_low=0), 0)
    assert "RTS" in text and bad

    # They arrive, but the receive interrupt never fires.
    text, bad = verdict(_state(rx_queued_count=4), 0)
    assert "INTRX0 has never fired" in text and bad

    # It fires, nothing reads it -- and at iff 6 we can say why.
    text, bad = verdict(_state(rx_queued_count=4, irq_rx_count=4), 6)
    assert "never reads SC0BUF" in text and "ei 6" in text and bad

    # All moving.
    text, bad = verdict(_state(tx_count=9, wire_count=9, rx_queued_count=9,
                               rx_read_count=9, irq_rx_count=9, irq_tx_count=9), 0)
    assert "flowing" in text and not bad
