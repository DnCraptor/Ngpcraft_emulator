# -*- coding: utf-8 -*-
"""The hardware-register dictionary: bit extraction, the meanings, and the checks.

Pure data -- no Qt, no emulator. The machine is a dict, which is the point: the
decoder has to be usable from a script and from a test, not only from the window.
"""

from core import hwregs


def _reader(values: dict[int, int]):
    """A fake bus: {address: byte}. Unset addresses read 0, like cleared RAM."""
    def read(addr: int, n: int = 1) -> bytes:
        return bytes(values.get(addr + i, 0) & 0xFF for i in range(n))
    return read


# ---------------------------------------------------------------- the map itself
def test_every_register_declares_a_source():
    for reg in hwregs.all_registers():
        assert reg.source, f"{reg.name} has no source"


def test_no_duplicate_addresses():
    addrs = [r.addr for r in hwregs.all_registers()]
    assert len(addrs) == len(set(addrs))


def test_fields_stay_inside_their_register():
    for reg in hwregs.all_registers():
        top = reg.width * 8 - 1
        for f in reg.fields:
            assert 0 <= f.lo <= f.hi <= top, f"{reg.name}.{f.name} is out of range"


def test_reverse_derived_registers_say_so():
    """The provenance tag is load-bearing: 0x8000 and 0x8400 are NOT in any
    manufacturer document we hold, and a table that presented them next to the
    spec-sourced ones with no distinction would launder a guess into a fact."""
    by_addr = {r.addr: r for r in hwregs.all_registers()}
    assert by_addr[0x008000].source == hwregs.REVERSE
    assert by_addr[0x008400].source == hwregs.REVERSE
    assert by_addr[0x008118].source == hwregs.SPEC


# ---------------------------------------------------------------- decoding
def test_bgc_enabled_encoding():
    reg = next(r for r in hwregs.all_registers() if r.addr == 0x008118)
    view = hwregs.decode(reg, 0x87)
    fields = {f.name: f for f in view.fields}
    assert fields["BGON"].raw == 0b10
    assert "ON" in fields["BGON"].text
    assert fields["BGC"].raw == 7


def test_bgc_disabled_encodings_are_all_named():
    """b7=1,b6=0 is the ONLY enabled encoding -- the other three must not read as
    'on'. One bit apart, opposite meaning: exactly the confusion this tab exists
    to remove."""
    reg = next(r for r in hwregs.all_registers() if r.addr == 0x008118)
    for raw, expect_on in ((0x80, True), (0x00, False), (0x40, False), (0xC0, False)):
        text = {f.name: f.text for f in hwregs.decode(reg, raw).fields}["BGON"]
        assert ("ON" in text) is expect_on, f"0x{raw:02X} decoded as {text!r}"


def test_interrupt_level_zero_reads_as_disabled():
    """The single fact that cost a 69->56 corpus regression when it was assumed:
    level 0 is OFF, not 'lowest priority'."""
    reg = next(r for r in hwregs.all_registers() if r.addr == 0x000071)
    fields = {f.name: f for f in hwregs.decode(reg, 0x30).fields}
    assert fields["INT4"].raw == 0
    assert "DISABLED" in fields["INT4"].text
    assert fields["INT5"].raw == 3 and "level 3" in fields["INT5"].text


def test_inte45_as_the_games_write_it():
    """Sonic, Puyo Pop and Metal Slug all write 0x32: VBlank at 2, INT5 at 3."""
    reg = next(r for r in hwregs.all_registers() if r.addr == 0x000071)
    fields = {f.name: f for f in hwregs.decode(reg, 0x32).fields}
    assert fields["INT4"].raw == 2
    assert fields["INT5"].raw == 3


def test_timer_clock_source_names_the_cascade():
    reg = next(r for r in hwregs.all_registers() if r.addr == 0x000024)
    fields = {f.name: f for f in hwregs.decode(reg, 0x40).fields}
    assert fields["T01M"].text == "16-bit timer"
    assert fields["T0CLK"].text == "external TI0"


def test_dma_vector_index_is_named_not_numbered():
    reg = next(r for r in hwregs.all_registers() if r.addr == 0x00007C)
    assert "INTT0" in hwregs.decode(reg, 0x10).fields[0].text
    assert hwregs.decode(reg, 0x00).fields[0].text == "off"


def test_word_registers_read_little_endian():
    read = _reader({0x006F80: 0xFF, 0x006F81: 0x03})
    values = hwregs.read_all(read)
    assert values[0x006F80] == 0x03FF


# ---------------------------------------------------------------- the checks
def _titles(values):
    return [c.title for c in hwregs.checks(values)]


def test_window_overflow_is_flagged():
    values = {0x008002: 100, 0x008004: 100, 0x008003: 0, 0x008005: 0}
    assert any("overflows horizontally" in t for t in _titles(values))


def test_a_legal_window_is_not_flagged():
    values = {0x008002: 0, 0x008004: 160, 0x008003: 0, 0x008005: 152}
    assert not any("overflow" in t for t in _titles(values))


def test_stopped_prescaler_is_flagged():
    assert any("Prescaler stopped" in t for t in _titles({0x000020: 0x00}))
    assert not any("Prescaler stopped" in t for t in _titles({0x000020: 0x81}))


def test_disabled_vblank_is_flagged():
    assert any("VBlank" in t for t in _titles({0x000071: 0x30}))
    assert not any("VBlank" in t for t in _titles({0x000071: 0x32}))


def test_armed_dma_channel_is_reported():
    """Not a fault -- an explanation. A handler that 'never runs' because DMA is
    eating its interrupt is otherwise invisible."""
    titles = _titles({0x00007C: 0x10})
    assert any("DMA0V armed on INTT0" in t for t in titles)


def test_character_over_is_flagged():
    assert any("Character Over" in t for t in _titles({0x008010: 0x80}))


def test_checks_never_raise_on_a_partial_machine():
    """A dict with nothing in it is what a detached window reads. It must produce
    a report, not a traceback."""
    assert isinstance(hwregs.checks({}), list)


# ---------------------------------------------------------------- report
def test_report_covers_every_group_and_survives_a_dead_bus():
    def dead(addr: int, n: int = 1) -> bytes:
        raise RuntimeError("unmapped")
    text = hwregs.format_report(dead)
    for group in hwregs.GROUPS:
        assert group.name in text


def test_report_shows_decoded_fields():
    read = _reader({0x008118: 0x87, 0x000071: 0x32})
    text = hwregs.format_report(read)
    assert "BGC" in text and "backdrop palette entry 7" in text
    assert "level 2" in text
