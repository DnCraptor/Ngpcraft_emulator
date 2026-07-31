# -*- coding: utf-8 -*-
"""Execution coverage: unpacking the core's bitmap, the gaps, and the map picture.

Pure numpy. The bit layout is the one `Machine::note_exec` writes -- LSB of a cell
is the LOWEST address in it -- and the tests say so, because unpacking it the other
way shuffles every run of eight bytes and still produces a plausible-looking map.
"""

import numpy as np

from core import coverage_map as cm


def _bitmap(*addresses, span=cm.COVERAGE_SPAN):
    """A core-shaped bitmap with an instruction start at each CPU address."""
    raw = bytearray((span + 7) // 8)
    for a in addresses:
        i = a - cm.COVERAGE_LO
        raw[i >> 3] |= 1 << (i & 7)
    return bytes(raw)


# ---------------------------------------------------------------- unpacking
def test_bits_unpack_least_significant_first():
    """`note_exec` uses `1 << (i & 7)`: bit 0 of a cell is the lowest address.
    Unpacking big-endian reverses every group of eight and looks fine."""
    bits = cm.unpack(_bitmap(cm.COVERAGE_LO), size=16)
    assert bits[0] and not bits[1:8].any()


def test_unpacking_an_empty_bitmap_is_empty_not_a_crash():
    """Coverage that was never armed returns b"" from the core."""
    assert len(cm.unpack(b"")) == 0
    assert cm.gaps(cm.unpack(b""), 1024) == []


# ---------------------------------------------------------------- stats
def test_the_denominator_is_what_the_window_can_see():
    """A 4 MiB cartridge has a second chip the coverage window does not watch.
    Reporting hits against the whole file would show a game that runs perfectly
    as 40% dead."""
    st = cm.stats(reached=1000, rom_size=4 * 1024 * 1024)
    assert st.covered_span == cm.COVERAGE_SPAN
    assert "second chip" in st.unreachable_note


def test_a_small_rom_is_measured_against_itself():
    st = cm.stats(reached=512, rom_size=1024)
    assert st.covered_span == 1024
    assert st.percent == 50.0
    assert st.unreachable_note == "", "nothing is out of the window here"


# ---------------------------------------------------------------- gaps
def test_gaps_are_the_runs_that_never_executed():
    bits = np.ones(1024, bool)
    bits[200:500] = False
    holes = cm.gaps(bits, 1024)
    assert len(holes) == 1
    assert holes[0].addr == cm.COVERAGE_LO + 200
    assert holes[0].length == 300
    assert holes[0].end == cm.COVERAGE_LO + 499
    assert holes[0].rom_offset == 200


def test_short_gaps_are_not_reported_because_they_prove_nothing():
    """A bit means 'an instruction STARTED here', so the bytes inside a multi-byte
    instruction are cold. A gap of a few bytes is the encoding, not dead code —
    reporting it manufactures a finding out of instruction lengths."""
    bits = np.ones(1024, bool)
    bits[100:105] = False                     # 5 bytes: an instruction interior
    assert cm.gaps(bits, 1024) == []
    bits[300:400] = False                     # 100 bytes: this one is real
    assert [g.length for g in cm.gaps(bits, 1024)] == [100]


def test_gaps_at_either_end_are_not_dropped():
    """A sentinel bug here silently loses the two most likely gaps of all: the
    header area before the entry point, and everything after the last routine."""
    bits = np.ones(1024, bool)
    bits[:80] = False
    bits[900:] = False
    lengths = sorted(g.length for g in cm.gaps(bits, 1024))
    assert lengths == [80, 124]


def test_gaps_come_back_largest_first():
    bits = np.ones(4096, bool)
    bits[100:200] = False
    bits[1000:1500] = False
    bits[3000:3070] = False
    assert [g.length for g in cm.gaps(bits, 4096)] == [500, 100, 70]


def test_gaps_stop_at_the_end_of_the_rom():
    """The bitmap always covers 2 MiB. A 4 KiB ROM must not report the remaining
    2 044 KiB of empty address space as dead code."""
    bits = cm.unpack(_bitmap(cm.COVERAGE_LO))
    holes = cm.gaps(bits, rom_size=4096)
    assert holes and max(g.end for g in holes) < cm.COVERAGE_LO + 4096


# ---------------------------------------------------------------- picture
def test_the_map_colours_reached_cold_and_absent():
    rom = 64 * 1024
    bits = np.zeros(rom, bool)
    bits[:1024] = True
    img = cm.image(bits, rom, width=64, rows=64)
    assert img.shape == (64, 64, 3)
    flat = img.reshape(-1, 3)
    assert tuple(flat[0]) == cm.COLOUR_HOT
    assert tuple(flat[-1]) == cm.COLOUR_COLD, "inside the ROM, never executed"

    small = cm.image(np.zeros(512, bool), 512, width=64, rows=64)
    assert tuple(small.reshape(-1, 3)[-1]) == cm.COLOUR_ABSENT, \
        "past the end of a small cartridge is ABSENT, not merely unexecuted"


def test_a_block_is_hot_if_any_byte_in_it_was_an_instruction_start():
    """The right rule at this granularity: executed code has a start every few
    bytes, so requiring every byte would paint running code as dead."""
    rom = 4096
    bits = np.zeros(rom, bool)
    bits[7] = True                              # one start, inside the first block
    img = cm.image(bits, rom, width=8, rows=8)
    assert cm.block_size(rom, 8, 8) == 64
    assert tuple(img[0, 0]) == cm.COLOUR_HOT


def test_hover_maps_a_pixel_back_to_an_address_range():
    addr, block = cm.address_at(2, 1, rom_size=4096, width=8, rows=8)
    assert block == 64
    assert addr == cm.COVERAGE_LO + (1 * 8 + 2) * 64


# ---------------------------------------------------------------- report
def test_the_report_leads_with_the_number_and_names_the_limitation():
    bits = np.zeros(2048, bool)
    bits[:512] = True
    text = cm.format_report(512, bits, rom_size=4 * 1024 * 1024)
    assert "reached 512 instruction addresses" in text
    assert "second chip" in text
    assert "never-executed runs" in text
