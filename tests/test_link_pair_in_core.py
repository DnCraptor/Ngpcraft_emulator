"""THE CABLED PAIR, INSIDE THE CORE — `ngpc_run_linked`.

⛔ WHAT THIS REPLACES, AND WHY IT IS NOT A PERFORMANCE CHANGE. The host used to own the
relay: run console A for a slice of INSTRUCTIONS, cross the FFI boundary, move the bytes,
run console B for a slice, cross back. An instruction count is not cable time, and the
study of how the Game Boy scene solved this same problem (LINK_NETPLAY_STUDY.md §4, L3)
found one answer everywhere: both consoles and the cable go in the core, paced by the
hardware's serial clock.

The probe ROM (tests/roms/link_probe.ngc) transmits its own controller byte and records
what came back: g_last_rx @ 0x400A, g_rx_total @ 0x400C. Every assertion below is made
from THOSE — the cartridge's own variables — rather than from a relay counter, because a
relay that moved bytes the CPU never saw would satisfy a counter and fail these.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.link import CABLE_SLICE, InProcessLink

REPO = Path(__file__).resolve().parent.parent
BIOS = REPO / "bios.bin"
ROM = REPO / "tests" / "roms" / "link_probe.ngc"

G_LAST_RX = 0x400A
G_RX_TOTAL = 0x400C

PAD_A, PAD_B = 0x11, 0x22

requires_rom = pytest.mark.skipif(
    not (BIOS.exists() and ROM.exists()),
    reason="needs the retail bios.bin (gitignored) and the probe ROM",
)


def rd8(m, addr):
    return m.read(addr, 1)[0]


def rd16(m, addr):
    d = m.read(addr, 2)
    return d[0] | (d[1] << 8)


def _pair():
    """Two consoles with the cable already in — plugged BEFORE either boots."""
    from core.native_session import NativeSession

    a = NativeSession(ROM, bios_path=BIOS, autosave=False)
    b = NativeSession(ROM, bios_path=BIOS, autosave=False)
    a.machine.serial_set_enabled(True)
    b.machine.serial_set_enabled(True)
    return a, b


def _play_in_core(frames: int):
    from core import native

    a, b = _pair()
    for _ in range(frames):
        a.machine.write(0x00B0, bytes([PAD_A]))
        b.machine.write(0x00B0, bytes([PAD_B]))
        native.run_linked(a.machine, b.machine, 1)
    return a, b


def _play_host_side(frames: int):
    """The previous arrangement, kept here as the thing being compared against: a slice
    of INSTRUCTIONS each, with the relay done from Python between slices."""
    a, b = _pair()
    link = InProcessLink(a.machine, b.machine)
    for _ in range(frames):
        a.machine.write(0x00B0, bytes([PAD_A]))
        b.machine.write(0x00B0, bytes([PAD_B]))
        for machine in (a.machine, b.machine):
            start = machine.run(0, record=False)[0].frame_count
            for _ in range(256):
                summ, _ = machine.run(CABLE_SLICE, record=False)
                link.pump()
                if summ.executed == 0 or summ.frame_count != start:
                    break
            else:
                machine.run_frames(1)
                link.pump()
    return a, b


@requires_rom
def test_bytes_cross_both_ways_with_the_relay_in_the_core():
    """Each cartridge must end up holding the OTHER side's controller byte. A relay
    wired back to its own console — the loopback bug that looks exactly like success on
    a byte counter — cannot pass this."""
    a, b = _play_in_core(400)

    assert rd8(a.machine, G_LAST_RX) == PAD_B, "A did not receive B's byte"
    assert rd8(b.machine, G_LAST_RX) == PAD_A, "B did not receive A's byte"
    assert rd16(a.machine, G_RX_TOTAL) > 100
    assert rd16(b.machine, G_RX_TOTAL) > 100


@requires_rom
def test_the_pair_advances_together():
    """⚡ THE PROPERTY "A SLICE EACH" NEVER HAD.

    Running a whole frame each leaves one console ahead of the other for the whole of
    that frame, so an answer crosses immediately in one direction and a frame late in
    the other. The core now advances whichever console is behind in cycles, so both
    land on the same frame count — and neither cartridge can be ahead of its peer in
    bytes exchanged by more than a handful, since neither can run on alone.
    """
    from core import native

    a, b = _pair()
    for _ in range(200):
        a.machine.write(0x00B0, bytes([PAD_A]))
        b.machine.write(0x00B0, bytes([PAD_B]))
        sa, sb = native.run_linked(a.machine, b.machine, 1)
        assert sa.frame_count == sb.frame_count, (
            f"the two consoles are on different frames: {sa.frame_count} vs "
            f"{sb.frame_count} — the pair is not being interleaved"
        )

    got_a = rd16(a.machine, G_RX_TOTAL)
    got_b = rd16(b.machine, G_RX_TOTAL)
    assert abs(got_a - got_b) <= max(8, (got_a + got_b) // 20), (
        f"one console is {abs(got_a - got_b)} bytes ahead of the other "
        f"({got_a} vs {got_b}) — one of them is running away"
    )


@requires_rom
def test_the_relay_in_the_core_is_at_least_as_live_as_the_host_relay():
    """The measurement the change exists for: the same ROM, the same frames, counted
    from the cartridge — the in-core pair must exchange at least as many bytes as the
    host-side slicing did.

    Stated as "at least" on purpose. The point of moving the cable in is that the relay
    happens on the cable's own clock instead of on an instruction count; a number that
    went DOWN would mean the pair is now less live than what it replaced, which is the
    failure worth catching. A big gain is welcome but is not what is being promised.
    """
    frames = 300
    core_a, core_b = _play_in_core(frames)
    host_a, host_b = _play_host_side(frames)

    core_total = rd16(core_a.machine, G_RX_TOTAL) + rd16(core_b.machine, G_RX_TOTAL)
    host_total = rd16(host_a.machine, G_RX_TOTAL) + rd16(host_b.machine, G_RX_TOTAL)
    assert core_total >= host_total, (
        f"the in-core relay exchanged {core_total} bytes where the host relay it "
        f"replaces exchanged {host_total}"
    )


@requires_rom
def test_the_pair_is_deterministic():
    """Two runs of the same pair from the same start must agree byte for byte.

    Mirror netplay simulates this same pair on two PCs and compares checksums, so any
    scheduling decision that depended on something other than the machines themselves
    would surface there as an unexplained desync rather than as a failure here.
    """
    from core import native

    def play():
        a, b = _pair()
        for i in range(120):
            a.machine.write(0x00B0, bytes([PAD_A if i % 3 else 0]))
            b.machine.write(0x00B0, bytes([PAD_B]))
            native.run_linked(a.machine, b.machine, 1)
        return (a.machine.read(0x4000, 0x2C00), b.machine.read(0x4000, 0x2C00),
                rd16(a.machine, G_RX_TOTAL), rd16(b.machine, G_RX_TOTAL))

    first = play()
    second = play()
    assert first[2] == second[2] and first[3] == second[3], "byte counts differ"
    assert first[0] == second[0] and first[1] == second[1], "work RAM differs"


@requires_rom
def test_which_console_goes_first_still_decides_the_outcome():
    """⛔ THE RULE MIRROR NETPLAY LIVES OR DIES BY, now that the core owns the pair.

    Mirror sessions run the SAME two consoles on both PCs and compare checksums, so both
    must simulate them in the same order — player 1 first, always, not "mine first".
    `run_two_consoles_interleaved` documents that and `MirrorSession.step` picks the
    order from who hosts.

    Moving the interleaving into the core could quietly have made the order irrelevant
    (in which case the rule above would be dead code nobody maintains) or, worse, made it
    ignore the caller's order (in which case one PC simulates a different pair from the
    other and the match dies a second in, saying only "desync"). MEASURED: same order
    twice gives the same work RAM on both consoles; swapping it does not.
    """
    import zlib

    from core.link import InProcessLink, run_two_consoles_interleaved

    def play(swapped: bool) -> int:
        a, b = _pair()
        link = InProcessLink(a.machine, b.machine)
        first, second = ((b.machine, a.machine) if swapped
                         else (a.machine, b.machine))
        for _ in range(200):
            a.machine.write(0x00B0, bytes([PAD_A]))
            b.machine.write(0x00B0, bytes([PAD_B]))
            run_two_consoles_interleaved(first, second, link)
        crc = zlib.crc32(a.machine.read(0x4000, 0x2C00))
        return zlib.crc32(b.machine.read(0x4000, 0x2C00), crc) & 0xFFFFFFFF

    assert play(False) == play(False), (
        "the same order twice gave different results — the pair is not deterministic, "
        "and mirror netplay stands on it being"
    )
    assert play(False) != play(True), (
        "the order of the two consoles no longer changes anything, so nothing would "
        "catch two PCs simulating the same match in opposite orders"
    )


@requires_rom
def test_a_console_that_stops_does_not_freeze_its_peer():
    """⛔ FOUND BY RE-READING MY OWN CHANGE, NOT BY A FAILING TEST.

    Ending the whole call on the first stop looks tidy and is wrong twice over. Putting a
    breakpoint on player 1 is an ordinary thing to do -- the two-player debugger exists --
    and it left player 2 with `executed = 0`: it never ran at all. On screen that is a
    frozen console, and worse, the shell files the summary as a frame that happened, so
    the frame accounting quietly starts lying.

    A stopped console now stops being a CANDIDATE; its peer runs on to its own frame
    boundary and the call ends when both are done or stopped.
    """
    from core import native

    a, b = _pair()
    native.run_linked(a.machine, b.machine, 2)
    a.machine.set_breakpoints([a.machine.cpu().pc])

    sa, sb = native.run_linked(a.machine, b.machine, 1)

    assert sa.stop_status == native.STATUS_BREAKPOINT, "player 1 should have stopped"
    assert sb.executed > 1000, (
        f"player 2 retired {sb.executed} instructions while its peer sat on a "
        "breakpoint -- it is frozen, and the shell will record a frame it never ran"
    )
    assert sb.frame_count == sa.frame_count + 1, (
        "player 2 did not finish the frame it was in the middle of"
    )


@requires_rom
def test_a_drifted_pair_is_still_interleaved():
    """⛔ THE REGRESSION THE PLAYER FOUND, and the reason it was never the same twice.

    The lag between the two consoles used to be judged on their LIFETIME cycle counts.
    They can drift apart outside a linked call -- the shell runs one alone whenever its
    peer is paused, rewinding or already holds queued frames, and a restored save state
    moves one wholesale -- and after that the console that was "behind" ran its whole
    frame first while its peer stood still. Card Fighters' Clash then failed DIFFERENTLY
    EACH ATTEMPT (white screen, frozen screen, a character select that never ends),
    because it depended on how the frame pacer had batched its work. A deterministic core
    failing differently each run means the decision depended on something outside it.

    ⚠️ AND THE OBVIOUS ASSERTION DOES NOT CATCH IT. Comparing the two consoles' cycle
    TOTALS for the call passes either way: a whole frame each, in sequence, costs exactly
    the same cycles as taking turns. The first version of this test did that and was
    green against the bug it was written for. What discriminates is the widest GAP that
    opened during the call -- about one quantum when interleaved, a whole frame when not.
    """
    from core import native

    a, b = _pair()
    native.run_linked(a.machine, b.machine, 2)
    for _ in range(5):                     # the drift the shell can create
        a.machine.run_frames(1)

    native.run_linked(a.machine, b.machine, 1)

    frame_cycles = 515 * 199
    gap = a.machine.link_pair_max_gap()
    assert gap < frame_cycles // 4, (
        f"the two consoles drifted {gap} cycles apart inside one frame "
        f"({frame_cycles} cycles): one of them is frozen while the other plays"
    )
