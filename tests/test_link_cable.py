"""Link cable (serial channel 0) — the emulated cable is a byte pipe.

Two layers are covered:

* the :class:`core.link.InProcessLink` relay logic, with fake machines (always
  runs, no ROM/BIOS needed);
* the full hardware path end to end: two real machines running the retail BIOS
  COM routines, wired by an InProcessLink, must each receive the byte the OTHER
  transmitted. This needs the real BIOS (COMINIT lives in it) and the compiled
  probe ROM, so it SKIPS when either is absent.

The probe ROM (tests/roms/link_probe.ngc, source link_probe_main.c) sends its raw
controller byte (port 0xB0, injected per-machine here) every loop and records the
last byte it received plus a running total, at these globals (from its .map):

    g_last_rx  @ 0x400A   (u8)
    g_rx_total @ 0x400C   (u16, little-endian)
    g_tx_count @ 0x400E   (u16)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.link import InProcessLink

REPO = Path(__file__).resolve().parent.parent
BIOS = REPO / "bios.bin"
ROM = REPO / "tests" / "roms" / "link_probe.ngc"

G_LAST_RX = 0x400A
G_RX_TOTAL = 0x400C
G_TX_COUNT = 0x400E


# --------------------------------------------------------------------------- #
# 1. Relay logic — fake machines, no core needed.
# --------------------------------------------------------------------------- #
class FakeSerial:
    """A minimal machine exposing the serial slice InProcessLink uses."""

    def __init__(self, rts: bool = True):
        self.tx = bytearray()      # bytes this machine "transmitted"
        self.rx = bytearray()      # bytes delivered to this machine
        self._rts = rts
        self.enabled = False

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
        # CTS0 handshake input, driven by the peer's RTS. Recorded so a test can
        # assert the bridge cross-wires it; the byte relay itself is unaffected.
        self.cts_high = high


def test_inprocess_link_relays_both_directions():
    a, b = FakeSerial(), FakeSerial()
    link = InProcessLink(a, b)
    assert a.enabled and b.enabled            # constructor arms the hardware path

    a.tx.extend(b"\x11")
    b.tx.extend(b"\x22")
    link.pump()

    assert bytes(b.rx) == b"\x11"             # A's byte reached B
    assert bytes(a.rx) == b"\x22"             # B's byte reached A
    assert link.bytes_ab == 1 and link.bytes_ba == 1


def test_inprocess_link_honours_rts_backpressure():
    """A receiver holding RTS high must not be fed; the bytes wait in the sender."""
    a = FakeSerial()
    b = FakeSerial(rts=False)                 # B is NOT ready to receive
    link = InProcessLink(a, b)

    a.tx.extend(b"\x5A")
    link.pump()

    assert bytes(b.rx) == b""                 # nothing pushed at B
    assert bytes(a.tx) == b"\x5A"             # still queued in A (back-pressure)

    b._rts = True                             # B lowers RTS (COMONRTS)
    link.pump()
    assert bytes(b.rx) == b"\x5A"             # now it flows


# --------------------------------------------------------------------------- #
# 2. Full hardware path — real BIOS COM routines, two machines.
# --------------------------------------------------------------------------- #
requires_rom = pytest.mark.skipif(
    not (BIOS.exists() and ROM.exists()),
    reason="needs the retail bios.bin (gitignored) and the probe ROM",
)


@requires_rom
def test_link_cable_bidirectional_hardware_path():
    from core.native_session import NativeSession

    def rd8(m, addr):
        return m.read(addr, 1)[0]

    def rd16(m, addr):
        d = m.read(addr, 2)
        return d[0] | (d[1] << 8)

    pad_a, pad_b = 0x11, 0x22
    a = NativeSession(ROM, bios_path=BIOS, autosave=False)
    b = NativeSession(ROM, bios_path=BIOS, autosave=False)
    link = InProcessLink(a.machine, b.machine)

    for _ in range(400):
        a.machine.write(0x00B0, bytes([pad_a]))
        b.machine.write(0x00B0, bytes([pad_b]))
        a.run_frames(1)
        b.run_frames(1)
        link.pump()

    # Each console received the OTHER's controller byte, continuously.
    assert rd8(a.machine, G_LAST_RX) == pad_b
    assert rd8(b.machine, G_LAST_RX) == pad_a
    assert rd16(a.machine, G_RX_TOTAL) > 100
    assert rd16(b.machine, G_RX_TOTAL) > 100
    assert link.bytes_ab > 100 and link.bytes_ba > 100


@requires_rom
def test_tcp_link_relays_over_socket():
    """The online path: two consoles wired by core.link.TcpLink over a real socket
    pair each receive the other's transmitted controller byte."""
    import socket
    from core.native_session import NativeSession
    from core.link import TcpLink

    sock_a, sock_b = socket.socketpair()
    a = NativeSession(ROM, bios_path=BIOS, autosave=False)
    b = NativeSession(ROM, bios_path=BIOS, autosave=False)
    link_a = TcpLink(a.machine, sock_a)
    link_b = TcpLink(b.machine, sock_b)
    try:
        for _ in range(400):
            a.machine.write(0x00B0, bytes([0x11]))
            b.machine.write(0x00B0, bytes([0x22]))
            a.run_frames(1)
            b.run_frames(1)
            link_a.pump()
            link_b.pump()

        assert a.machine.read(0x400A, 1)[0] == 0x22   # A got B's byte over TCP
        assert b.machine.read(0x400A, 1)[0] == 0x11
        assert link_a.bytes_out > 50 and link_b.bytes_out > 50
        assert link_a.bytes_in > 50 and link_b.bytes_in > 50
    finally:
        link_a.disconnect()
        link_b.disconnect()


# --------------------------------------------------------------------------- #
# 3. The same cable, with NO real BIOS — the clean-room HLE image drives it.
# --------------------------------------------------------------------------- #
HLE_IMAGE = REPO / "hle_bios" / "bios_hle.bin"

requires_hle = pytest.mark.skipif(
    not (HLE_IMAGE.exists() and ROM.exists()),
    reason="needs hle_bios/bios_hle.bin and the probe ROM",
)


@requires_hle
def test_link_cable_works_without_a_real_bios():
    """Two consoles on the HLE image must exchange bytes exactly as on the retail
    BIOS. The COM vectors used to be "cable idle" stubs, so nothing was ever handed
    to SC0BUF and NOT ONE BYTE went on the wire — while the game's own TX counter
    kept climbing, which is why a counter inside the game proves nothing.

    This needs the whole chain: COMINIT arming INTES0 and lowering IFF, the rings at
    0x6C80/0x6CC0, the RX count at 0x6D01 the probe reads itself, and the two serial
    ISRs — which are CROSS-WIRED versus their SDK names (0x18 receives, 0x19
    transmits). Wire them by name instead and each console receives its OWN byte:
    the assertions below are on the OTHER console's byte precisely so that a
    self-loopback fails the test.
    """
    from core.native_session import NativeSession

    a = NativeSession(ROM, bios_path=HLE_IMAGE, autosave=False)
    b = NativeSession(ROM, bios_path=HLE_IMAGE, autosave=False)
    link = InProcessLink(a.machine, b.machine)

    for _ in range(400):
        a.machine.write(0x00B0, bytes([0x11]))
        b.machine.write(0x00B0, bytes([0x22]))
        a.run_frames(1)
        b.run_frames(1)
        link.pump()

    def rd16(m, addr):
        d = m.read(addr, 2)
        return d[0] | (d[1] << 8)

    assert a.machine.read(G_LAST_RX, 1)[0] == 0x22, "A did not receive B's byte"
    assert b.machine.read(G_LAST_RX, 1)[0] == 0x11, "B did not receive A's byte"
    assert rd16(a.machine, G_RX_TOTAL) > 100
    assert rd16(b.machine, G_RX_TOTAL) > 100
    assert link.bytes_ab > 100 and link.bytes_ba > 100, "nothing reached the wire"


@requires_hle
def test_hle_link_is_not_a_loopback():
    """Enabled, but with no peer bridging its TX: it must receive nothing."""
    from core.native_session import NativeSession

    a = NativeSession(ROM, bios_path=HLE_IMAGE, autosave=False)
    a.machine.serial_set_enabled(True)
    for _ in range(200):
        a.machine.write(0x00B0, bytes([0x33]))
        a.run_frames(1)

    d = a.machine.read(G_RX_TOTAL, 2)
    assert (d[0] | (d[1] << 8)) == 0, "the console received its own transmission"


@requires_rom
def test_link_cable_disabled_is_unplugged():
    """With no peer wired in, a machine must never receive its own transmission
    (the self-loopback the cross-wired serial vectors originally caused)."""
    from core.native_session import NativeSession

    def rd16(m, addr):
        d = m.read(addr, 2)
        return d[0] | (d[1] << 8)

    a = NativeSession(ROM, bios_path=BIOS, autosave=False)
    a.machine.serial_set_enabled(True)        # enabled, but nothing bridges its TX
    for _ in range(200):
        a.machine.write(0x00B0, bytes([0x33]))
        a.run_frames(1)

    assert rd16(a.machine, G_RX_TOTAL) == 0    # received nothing — no loopback
