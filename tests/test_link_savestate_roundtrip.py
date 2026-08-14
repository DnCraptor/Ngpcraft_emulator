"""A savestate must carry the link cable, and until 2026-08-04 it did not.

⛔ THE HOLE THESE TESTS WERE WRITTEN AGAINST. `AuxState` covers the sound CPU, the
T6W28 and the timers. It did NOT cover serial channel 0: `serial_tx` / `serial_rx`,
`serial_tx_busy`, `serial_rx_pending`, `serial_tx_cycles`, `serial_cts_high` all live
in `Machine` (cpp/src/core.cpp) and were reachable only through `ngpc_serial_state`,
which is READ-ONLY by design ("a read-only snapshot for the debugger's Link tab"). So
the player's save state -- cpu struct + AuxState + read(0, 0xC000) -- restored a console
whose cable was whatever the machine happened to be holding, not what was captured.

⚠️ WHERE IT WAS REACHABLE. `PlayPage._mirror_blocks()` refuses save states, rewind and
reset during MIRROR netplay, so the online mirror path was protected -- for an unrelated
reason (the other PC would not follow). It does NOT guard the local two-player cable
(`_link_peer`) or direct-IP cable play (`_net_link`), where F2 and rewind are live. The
rewind ring shares `_capture_state`, so every rewind step in a cabled session went
through this hole.

⚡ AND IT IS THE PREREQUISITE FOR ROLLBACK. Rolling back a mirror session means
restoring both consoles AND the bytes in flight between them. The second test is that
requirement stated as an experiment.

✅ CLOSED 2026-08-04. The cable is now its own versioned block (`ngpc_link_state_t` /
`native.LinkState`) and the player's save state is `NGPCST03` = cpu + aux + link +
image. These two tests were written FIRST, as `xfail(strict=True)`, against the numbers
quoted in each docstring below; they mirror `_capture_state`, so they moved with it and
now pass. They stay as the regression: whatever else changes about the format, a state
taken mid-transfer has to bring the channel back, and re-simulating from it has to
reproduce the cable byte for byte. See LINK_NETPLAY_STUDY.md.
"""

from __future__ import annotations

import ctypes
from pathlib import Path

import pytest

from core import native
from core.link import InProcessLink

REPO = Path(__file__).resolve().parent.parent
BIOS = REPO / "bios.bin"
ROM = REPO / "tests" / "roms" / "link_probe.ngc"

STATE_MEM_LEN = 0x00C000
G_RX_TOTAL = 0x400C

# The channel fields a complete savestate has to bring back. `ctse` and `cts_high` are
# included because the handshake lines decide whether the NEXT byte may leave at all.
SERIAL_FIELDS = ("tx_depth", "rx_depth", "tx_busy", "rx_pending", "cts_high",
                 "ctse", "tx_count", "wire_count", "rx_queued_count",
                 "rx_read_count")

requires_rom = pytest.mark.skipif(
    not (BIOS.exists() and ROM.exists()),
    reason="needs the retail bios.bin (gitignored) and the probe ROM",
)


def _capture(m) -> bytes:
    """Exactly what PlayPage._capture_state writes (the NGPCST03 body).

    Mirrored rather than imported: ngpc_shell pulls in the whole Qt application and this
    has to run headless. If the shell's layout changes this must follow it -- that is
    the point of the test, not an accident of it.
    """
    return (bytes(m.cpu()) + bytes(m.aux_state()) + bytes(m.link_state())
            + m.read(0, STATE_MEM_LEN))


def _restore(m, body: bytes) -> None:
    """Exactly what PlayPage._apply_state does: image, then CPU, then aux, then link."""
    cpu_len = ctypes.sizeof(type(m.cpu()))
    aux_len = ctypes.sizeof(native.AuxState)
    link_len = ctypes.sizeof(native.LinkState)
    head = cpu_len + aux_len + link_len
    cpu = type(m.cpu()).from_buffer_copy(body[:cpu_len])
    m.write(0, body[head:head + STATE_MEM_LEN])
    m.set_cpu(cpu)
    # Both setters REFUSE a blob from another build rather than half-apply it; a silent
    # False here would make the whole test vacuous.
    assert m.set_aux_state(
        native.AuxState.from_buffer_copy(body[cpu_len:cpu_len + aux_len]))
    assert m.set_link_state(
        native.LinkState.from_buffer_copy(body[cpu_len + aux_len:head]))


def _serial(m) -> dict[str, int]:
    st = m.serial_state()
    return {f: int(getattr(st, f)) for f in SERIAL_FIELDS}


def _rd16(m, addr: int) -> int:
    d = m.read(addr, 2)
    return d[0] | (d[1] << 8)


def _run_frames(a, b, link, n: int) -> None:
    for _ in range(n):
        a.machine.write(0x00B0, bytes([0x11]))
        b.machine.write(0x00B0, bytes([0x22]))
        a.run_frames(1)
        b.run_frames(1)
        link.pump()


def _boot_pair(frames: int = 400):
    """Two probe consoles on a cable, run until they are talking steadily."""
    from core.native_session import NativeSession

    a = NativeSession(ROM, bios_path=BIOS, autosave=False)
    b = NativeSession(ROM, bios_path=BIOS, autosave=False)
    link = InProcessLink(a.machine, b.machine)
    _run_frames(a, b, link, frames)
    assert _rd16(a.machine, G_RX_TOTAL) > 100, "the cable never came up"
    return a, b, link


def _advance_to_inflight(a, b) -> None:
    """Stop on a moment where the channel is actually holding something."""
    for _ in range(20000):
        a.machine.run(20, record=False)
        b.machine.run(20, record=False)
        s = _serial(a.machine)
        if s["tx_busy"] or s["tx_depth"] or s["rx_depth"] or s["rx_pending"]:
            return
    pytest.skip("never caught the channel with anything in flight")


@requires_rom
def test_savestate_roundtrip_restores_the_serial_channel():
    """Capture with bytes in the channel, diverge, restore: the channel must return.

    MEASURED 2026-08-04 BEFORE the fix, on 44e286b: the restore was a no-op on the
    channel. Captured at rx_depth=2 / rx_read_count=1021, ran 30 frames to rx_depth=3 /
    rx_read_count=1097, restored -- and every field stayed at the post-30-frame value.
    Nothing of the cable was carried.
    """
    a, b, link = _boot_pair()
    _advance_to_inflight(a, b)
    at_capture = _serial(a.machine)

    body = _capture(a.machine)
    _run_frames(a, b, link, 30)          # let the channel move on
    assert _serial(a.machine) != at_capture, "the channel did not change; test is blind"

    _restore(a.machine, body)
    assert _serial(a.machine) == at_capture


@requires_rom
def test_resimulation_from_a_restored_state_reproduces_the_cable():
    """The rollback requirement: same state in, same cable traffic out.

    Both consoles are captured, 60 frames are run and the traffic recorded, both are
    restored, and the SAME 60 frames are run again. A complete savestate makes the two
    runs identical.

    MEASURED 2026-08-04 BEFORE the fix, on 44e286b: run 1 ended at rx_total=1174 on both
    consoles, run 2 at 1175 -- one extra byte crossed the cable, because the bytes
    sitting in the RX FIFO at capture were never restored. One byte of drift in 60
    frames is a desync, not a rounding error.
    """
    a, b, link = _boot_pair()
    _advance_to_inflight(a, b)

    body_a, body_b = _capture(a.machine), _capture(b.machine)
    ab0, ba0 = link.bytes_ab, link.bytes_ba

    def run_and_observe() -> dict[str, int]:
        _run_frames(a, b, link, 60)
        return {
            "a_rx_total": _rd16(a.machine, G_RX_TOTAL),
            "b_rx_total": _rd16(b.machine, G_RX_TOTAL),
            "bytes_ab": link.bytes_ab,
            "bytes_ba": link.bytes_ba,
        }

    first = run_and_observe()
    _restore(a.machine, body_a)
    _restore(b.machine, body_b)
    link.bytes_ab, link.bytes_ba = ab0, ba0     # the relay's own counters are ours
    second = run_and_observe()

    assert first == second


@requires_rom
def test_a_savestate_from_a_console_with_no_cable_still_round_trips():
    """CONTROL. Without this, the two tests above could pass on a broken `_serial`.

    A machine that never had a cable must come back with the channel still inert -- and
    `set_link_state` must not arm it as a side effect of being called.
    """
    from core.native_session import NativeSession

    s = NativeSession(ROM, bios_path=BIOS, autosave=False)
    s.run_frames(20)
    before = _serial(s.machine)
    assert int(s.machine.serial_state().enabled) == 0, (
        "no link was set up; the channel should be inert")

    body = _capture(s.machine)
    s.run_frames(20)
    _restore(s.machine, body)
    assert _serial(s.machine) == before
