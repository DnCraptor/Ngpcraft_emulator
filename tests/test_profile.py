# -*- coding: utf-8 -*-
"""The profiler: cycles per function, from a recorded instruction trace.

Pure -- fake records and a fake symbol table. The unit under test is the ACCOUNTING,
which is where a profiler earns or loses its usefulness: rank by the wrong cost and
it points at the wrong routine with total confidence.
"""

from dataclasses import dataclass

from core import profile as pr


@dataclass
class _Rec:
    pc: int
    cycles: int = 4
    n_reads: int = 0
    n_writes: int = 0


class _Sym:
    def __init__(self, name, address):
        self.name = name
        self.address = address


class _Table:
    """Nearest symbol at or below the PC, like the real one."""

    def __init__(self, entries):
        self._entries = sorted(entries, key=lambda s: s.address)

    def lookup_address(self, addr, max_span=0x4000):
        best = None
        for s in self._entries:
            if s.address <= addr:
                best = s
            else:
                break
        if best is None or addr - best.address > max_span:
            return None
        return best


# ---------------------------------------------------------------- the unit
def test_ranking_is_by_cycles_not_by_instruction_count():
    """On this machine instruction cost varies by a factor of ten, and the cart bus
    is slow. Ranking by instruction count puts a tight loop above the routine that
    is actually eating the frame."""
    recs = ([_Rec(0x200000, cycles=2)] * 100        # 200 cycles, 100 instructions
            + [_Rec(0x201000, cycles=40)] * 20)     # 800 cycles, 20 instructions
    rep = pr.profile(recs, block=0x1000)
    assert rep.buckets[0].cycles == 800
    assert rep.buckets[0].instructions == 20, "the SHORTER one leads -- it costs more"


def test_symbols_name_the_buckets_when_a_map_is_loaded():
    table = _Table([_Sym("update_player", 0x201000), _Sym("draw_hud", 0x202000)])
    recs = [_Rec(0x201010)] * 3 + [_Rec(0x202004)]
    rep = pr.profile(recs, table)
    assert rep.symbols_used and rep.resolved == 4
    assert {b.name for b in rep.buckets} == {"update_player", "draw_hud"}
    top = rep.buckets[0]
    assert top.name == "update_player" and top.lo == 0x201010 and top.hi == 0x201010


def test_without_symbols_it_still_answers_by_address_block():
    """Refusing to say anything without a .map would make the tool useless on every
    commercial cartridge -- which is most of the corpus."""
    rep = pr.profile([_Rec(0x2044C4)] * 5, block=0x40)
    assert not rep.symbols_used
    assert rep.buckets[0].name == "2044C0..2044FF"


def test_a_pc_far_past_the_last_symbol_is_not_attributed_to_it():
    """The real table refuses an attribution beyond `max_span`. A profiler that
    swallowed that would credit the BIOS's work to the last function in the ROM."""
    table = _Table([_Sym("tiny", 0x200000)])
    rep = pr.profile([_Rec(0xFF1234)], table)
    assert rep.resolved == 0
    assert rep.buckets[0].name.startswith("FF12")


# ---------------------------------------------------------------- regions
def test_region_attribution_answers_where_the_frame_went():
    """'40% of this frame is inside the BIOS' is something no function list can
    say -- and on this console it is often the answer."""
    recs = [_Rec(0x201000, cycles=10)] * 6 + [_Rec(0xFF2000, cycles=10)] * 4
    rep = pr.profile(recs)
    assert rep.by_region["cartridge"] == 60
    assert rep.by_region["BIOS"] == 40


def test_regions_cover_the_places_a_pc_can_legitimately_be():
    assert pr.region_of(0x004100) == "work RAM"
    assert pr.region_of(0x007123) == "shared Z80 RAM"
    assert pr.region_of(0x009000) == "video RAM"
    assert pr.region_of(0x800000) == "cartridge (2nd chip)"
    assert pr.region_of(0x100000) == "unmapped"


# ---------------------------------------------------------------- the frame
def test_the_capture_is_measured_in_frames():
    recs = [_Rec(0x200000, cycles=1)] * (pr.CYCLES_PER_FRAME * 2)
    rep = pr.profile(recs)
    assert abs(rep.frames - 2.0) < 1e-9


def test_cycles_per_frame_is_not_the_percentage_wearing_a_hat():
    """A bucket's share of one frame is arithmetically the same number as its share
    of the capture, so the report gives the ABSOLUTE per-frame cost instead: that
    one is a fact about the console, not about how long you recorded."""
    half = pr.CYCLES_PER_FRAME // 2
    recs = ([_Rec(0x200000, cycles=1)] * half           # half a frame in A
            + [_Rec(0x300000, cycles=1)] * (half * 3))  # one and a half in B
    rep = pr.profile(recs)
    a = next(b for b in rep.buckets if b.lo == 0x200000)
    assert abs(rep.share(a) - 25.0) < 0.1
    # Two frames captured, so A costs a quarter of the total = ~a quarter frame each.
    assert abs(rep.per_frame(a) - half / 2) < 2


def test_memory_accesses_are_counted_per_bucket():
    """Reads and writes are what the slow cartridge bus charges for, so a routine
    can be expensive for a reason its instruction count never shows."""
    rep = pr.profile([_Rec(0x200000, cycles=4, n_reads=2, n_writes=1)] * 3)
    assert rep.buckets[0].reads == 6 and rep.buckets[0].writes == 3


def test_cycles_per_instruction_is_reported():
    rep = pr.profile([_Rec(0x200000, cycles=10)] * 2)
    assert rep.buckets[0].cycles_per_instruction == 10.0


# ---------------------------------------------------------------- edges
def test_an_empty_trace_is_an_empty_report_not_a_division_by_zero():
    rep = pr.profile([])
    assert rep.buckets == [] and rep.total_cycles == 0
    assert rep.frames == 0.0
    assert "0 instructions" in pr.format_report(rep)


def test_the_report_says_when_it_had_no_symbols():
    assert "no symbols loaded" in pr.format_report(pr.profile([_Rec(0x200000)]))


def test_the_report_says_how_much_fell_outside_every_symbol():
    table = _Table([_Sym("known", 0x200000)])
    rep = pr.profile([_Rec(0x200004)] * 3 + [_Rec(0xFF0000)] * 2, table)
    text = pr.format_report(rep)
    assert "2 instructions fell outside every known symbol" in text
    assert "known" in text and "BIOS" in text
