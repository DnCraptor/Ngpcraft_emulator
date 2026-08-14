"""The core wakes the host when the cable moves (`set_serial_break`, ABI 17).

⚡ WHY THIS EXISTS. A host bridging two machines has to relay serial bytes, and
until ABI 17 it had no way to know *when*, so it guessed: pump the cable every N
instructions, N picked small enough for the worst known game (`CABLE_SLICE = 400`
-- The Last Blade breaks past it). N is cable time expressed in instructions,
which is the wrong unit, and the core already counts the right one: `serial_tick`
computes the byte-time from BR0CR/SC0MOD and knows the exact cycle a byte
finishes shifting out. It simply never told anyone.

These tests pin the contract:

* armed, `run()` hands back ON the event, well inside its instruction budget;
* the stop is a RENDEZVOUS -- the machine resumes and keeps talking;
* an RTS edge counts as traffic even though no byte moved (the peer's
  transmitter is held by its CTS until we relay it -- Card Fighters' Clash);
* and, the control that gives the rest its meaning, UNARMED the same run
  consumes its whole budget. Without it every assertion here would pass just as
  well against a core that stops for some unrelated reason.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core import native

REPO = Path(__file__).resolve().parent.parent
BIOS = REPO / "bios.bin"
ROM = REPO / "tests" / "roms" / "link_probe.ngc"

requires_rom = pytest.mark.skipif(
    not (BIOS.exists() and ROM.exists()),
    reason="needs the retail bios.bin (gitignored) and the probe ROM",
)

# Comfortably more than one byte-time (3200 cycles), so a run that reaches it has
# genuinely declined to stop rather than merely been given too little rope.
BUDGET = 50_000


def _talking_machine():
    """A machine booted far enough that the probe ROM is sending bytes."""
    from core.native_session import NativeSession

    s = NativeSession(ROM, bios_path=BIOS, autosave=False)
    m = s.machine
    m.serial_set_enabled(True)
    m.serial_set_cts(False)                  # peer ready, nothing held
    m.write(0x00B0, bytes([0x11]))           # something for the probe to send
    for _ in range(90):                      # boot, then let it start talking
        m.run_frames(1)
        m.serial_read_tx()                   # keep the FIFO from filling
    return s, m


@requires_rom
def test_unarmed_the_run_uses_its_whole_budget():
    """THE CONTROL. Off by default -- the pre-ABI-17 behaviour, unchanged."""
    _s, m = _talking_machine()
    summary, _ = m.run(BUDGET, record=False)
    assert summary.stop_status == native.STATUS_COUNT_REACHED
    assert summary.executed == BUDGET, (
        "an unarmed run must retire its whole quota, exactly as before")


@requires_rom
def test_armed_the_run_stops_on_the_byte_reaching_the_wire():
    _s, m = _talking_machine()
    m.set_serial_break(True)

    before = m.serial_state().wire_count
    summary, _ = m.run(BUDGET, record=False)

    assert summary.stop_status == native.STATUS_SERIAL_EVENT, (
        "the cable moved and the run should have said so")
    assert summary.executed < BUDGET, "it stopped early, on the event"
    assert m.serial_state().wire_count == before + 1, (
        "handed back on THE byte -- not after a batch of them")


@requires_rom
def test_the_stop_is_a_rendezvous_and_the_machine_keeps_talking():
    """Stopping must not disturb the console: it is a hand-back, not a trap."""
    _s, m = _talking_machine()
    m.set_serial_break(True)

    seen = 0
    for _ in range(8):
        summary, _ = m.run(BUDGET, record=False)
        if summary.stop_status == native.STATUS_SERIAL_EVENT:
            seen += 1
        m.serial_read_tx()                   # the host relays, as it would
    assert seen >= 4, (
        "every byte should produce its own hand-back, run after run")

    # And the machine is intact: disarm, and it behaves exactly as before.
    m.set_serial_break(False)
    summary, _ = m.run(BUDGET, record=False)
    assert summary.stop_status == native.STATUS_COUNT_REACHED
    assert summary.executed == BUDGET


@requires_rom
def test_an_rts_edge_is_cable_traffic_even_with_no_byte():
    """⚡ RTS drives the PEER's CTS, so an edge must be relayed like a byte.

    A host that only woke on bytes would leave the peer's transmitter held
    against a handshake we had already released -- each console waiting for the
    other, which is the shape of the Card Fighters' Clash hang.

    Driven here by poking 0xB2 directly: what matters is that the core notices
    the level CHANGED, not which code changed it.
    """
    _s, m = _talking_machine()
    m.set_serial_break(True)
    m.run(BUDGET, record=False)              # settle on the current level
    m.serial_read_tx()

    rts = m.read(0x00B2, 1)[0]
    m.write(0x00B2, bytes([rts ^ 0x01]))     # flip RTS, send nothing

    before = m.serial_state().wire_count
    summary, _ = m.run(200, record=False)    # far too few for a byte-time
    assert summary.stop_status == native.STATUS_SERIAL_EVENT, (
        "an RTS edge is cable traffic and must hand back")
    assert m.serial_state().wire_count == before, (
        "...and it must be the EDGE that did it, with no byte on the wire")
