# -*- coding: utf-8 -*-
"""Named cheats: the values, the shareable text format, and the warnings.

Pure -- a fake machine and a temp file. The warnings get real attention because on
this console a write to the wrong region is not a no-op: the cartridge is FLASH,
and a write there goes to the chip's command latch.
"""

from core import cheats as ch


class _FakeMachine:
    def __init__(self):
        self.writes: list = []

    def write(self, addr, data):
        self.writes.append((addr, bytes(data)))


# ---------------------------------------------------------------- values
def test_values_are_written_little_endian_like_every_other_store():
    m = _FakeMachine()
    ch.Cheat("x", [ch.Entry(0x4812, 2, 0x03E7)], enabled=True).apply(m)
    assert m.writes == [(0x4812, b"\xe7\x03")]


def test_a_value_is_masked_to_its_size_rather_than_overflowing_the_neighbour():
    assert ch.Entry(0x4000, 1, 0x1FF).bytes() == b"\xff"
    assert len(ch.Entry(0x4000, 4, 0xFFFFFFFF).bytes()) == 4


def test_an_unknown_size_falls_back_to_one_byte_not_to_a_crash():
    assert ch.Entry(0x4000, 3, 0xAB).bytes() == b"\xab"


def test_only_enabled_cheats_with_addresses_are_applied():
    m = _FakeMachine()
    s = ch.CheatSet()
    s.cheats = [
        ch.Cheat("on", [ch.Entry(0x4000, 1, 1)], enabled=True),
        ch.Cheat("off", [ch.Entry(0x4001, 1, 2)], enabled=False),
        ch.Cheat("empty", [], enabled=True),
    ]
    s.apply(m)
    assert m.writes == [(0x4000, b"\x01")]


# ---------------------------------------------------------------- warnings
def test_a_cartridge_address_is_called_out_as_flash():
    """This is the one that matters on this machine: the cart is NOR flash, so a
    write there does not change memory — it goes to the command latch, which is
    worse than doing nothing."""
    problems = ch.validate(ch.Cheat("x", [ch.Entry(0x201234, 1, 0)]))
    assert any("FLASH" in p and "command latch" in p for p in problems)


def test_a_hardware_register_is_called_out_as_not_a_variable():
    problems = ch.validate(ch.Cheat("x", [ch.Entry(0x000020, 1, 0)]))
    assert any("hardware register" in p for p in problems)


def test_an_unmapped_address_is_called_out():
    assert any("not mapped" in p
               for p in ch.validate(ch.Cheat("x", [ch.Entry(0x100000, 1, 0)])))


def test_work_ram_raises_nothing():
    assert ch.validate(ch.Cheat("x", [ch.Entry(0x004812, 2, 0x3E7)])) == []


def test_an_empty_cheat_says_it_does_nothing():
    assert any("does nothing" in p for p in ch.validate(ch.Cheat("x", [])))


def test_a_value_too_wide_for_its_size_is_reported():
    assert any("does not fit" in p
               for p in ch.validate(ch.Cheat("x", [ch.Entry(0x4000, 1, 0x100)])))


def test_warnings_never_stop_a_cheat_from_running():
    """A debugger that refused an address because it looked wrong would be useless
    the day the address is right."""
    m = _FakeMachine()
    bad = ch.Cheat("into the cart", [ch.Entry(0x201234, 1, 0xFF)], enabled=True)
    assert ch.validate(bad)
    bad.apply(m)
    assert m.writes, "told, not enforced"


# ---------------------------------------------------------------- text format
def test_the_shareable_format_round_trips():
    text = "# Infinite health\n4812:1 = 63\n481A:2 = 03E7\n"
    cheats, problems = ch.parse_text(text)
    assert problems == []
    assert len(cheats) == 1 and cheats[0].name == "Infinite health"
    assert [(e.addr, e.size, e.value) for e in cheats[0].entries] == [
        (0x4812, 1, 0x63), (0x481A, 2, 0x03E7)]
    again, _ = ch.parse_text(ch.format_text(cheats))
    assert [(e.addr, e.size, e.value) for e in again[0].entries] == \
           [(e.addr, e.size, e.value) for e in cheats[0].entries]


def test_several_cheats_separated_by_their_names():
    cheats, _ = ch.parse_text("# one\n4000=01\n\n# two\n4001=02\n4002=03\n")
    assert [c.name for c in cheats] == ["one", "two"]
    assert len(cheats[1].entries) == 2


def test_the_format_is_forgiving_about_how_people_actually_type():
    cheats, problems = ch.parse_text(
        "; semicolon comment\n0x004812 : 2 = 0x03E7\n  4813=FF  \n")
    assert problems == []
    assert [(e.addr, e.size, e.value) for e in cheats[0].entries] == [
        (0x4812, 2, 0x03E7), (0x4813, 1, 0xFF)]


def test_a_size_is_optional_and_defaults_to_one_byte():
    cheats, _ = ch.parse_text("# x\n4000=07\n")
    assert cheats[0].entries[0].size == 1


def test_entries_before_any_name_still_load():
    cheats, _ = ch.parse_text("4000=01\n")
    assert len(cheats) == 1 and cheats[0].entries


def test_a_bad_line_is_reported_with_its_number_never_skipped_quietly():
    """A pasted code with one bad character that quietly loads three of its four
    addresses is a cheat that half-works — the state that wastes the most time."""
    cheats, problems = ch.parse_text("# x\n4000=01\nnonsense here\n4002=03\n")
    assert len(problems) == 1 and "line 3" in problems[0]
    assert len(cheats[0].entries) == 2, "the readable lines still load"


# ---------------------------------------------------------------- persistence
def test_cheats_survive_a_save_and_load(tmp_path):
    path = tmp_path / "game.cheats.json"
    s = ch.CheatSet()
    s.cheats = [ch.Cheat("hp", [ch.Entry(0x4812, 2, 0x3E7)], enabled=True,
                         note="from the RAM search")]
    s.save(path)
    back = ch.CheatSet()
    back.load(path)
    assert len(back.cheats) == 1
    c = back.cheats[0]
    assert c.name == "hp" and c.enabled and c.note == "from the RAM search"
    assert (c.entries[0].addr, c.entries[0].size, c.entries[0].value) == \
           (0x4812, 2, 0x3E7)


def test_saving_an_empty_set_removes_a_stale_file(tmp_path):
    """Same rule as the watches: leave no empty map behind pretending to be one."""
    path = tmp_path / "game.cheats.json"
    path.write_text("[]", encoding="utf-8")
    ch.CheatSet().save(path)
    assert not path.exists()


def test_a_corrupt_file_loads_as_empty_rather_than_raising(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{{{not json", encoding="utf-8")
    s = ch.CheatSet()
    s.load(path)
    assert s.cheats == []


def test_loading_a_missing_file_is_simply_no_cheats(tmp_path):
    s = ch.CheatSet()
    s.load(tmp_path / "nope.json")
    assert s.cheats == []
