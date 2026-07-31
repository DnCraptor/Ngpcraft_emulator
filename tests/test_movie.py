# -*- coding: utf-8 -*-
"""Input recording and replay: the file format, the guards, and the button layout.

Pure -- bytes in, bytes out. The guards get the most attention here because a movie
that replays against the wrong cartridge produces something that looks exactly like
an emulation bug, and that is the one failure this feature must never cause.
"""

import pytest

from core import movie as mv


def _movie(frames=10, state=b"S" * 8, **header):
    head = {"rom_name": "game.ngc", "rom_sha": "abc123", **header}
    m = mv.Movie(head, state, bytearray(range(frames)))
    return m


# ---------------------------------------------------------------- format
def test_a_movie_survives_a_round_trip():
    m = _movie()
    back = mv.load(mv.dump(m))
    assert back.inputs == m.inputs
    assert back.state == m.state
    assert back.rom_name == "game.ngc" and back.rom_sha == "abc123"


def test_the_frame_count_is_written_by_the_dumper_not_trusted_from_the_caller():
    """A header field the caller could set and the body could contradict is a lie
    waiting to happen."""
    m = _movie(frames=7)
    m.header["frames"] = 999
    assert mv.load(mv.dump(m)).header["frames"] == 7


def test_an_empty_state_is_allowed_and_round_trips():
    m = mv.Movie({"rom_name": "x"}, b"", bytearray([1, 2, 3]))
    assert mv.load(mv.dump(m)).state == b""


def test_rubbish_is_refused_clearly():
    for blob in (b"", b"nope", b"NGPCMOV1", mv.MAGIC + b"\xff\xff\xff\xff"):
        with pytest.raises(mv.BadMovie):
            mv.load(blob)


def test_a_movie_from_a_newer_build_is_refused_rather_than_half_read():
    """Hand-built, because `dump` always stamps the CURRENT version — which is
    right, and means this guard can only be reached from a file someone else's
    build wrote."""
    import json

    head = json.dumps({"version": mv.FORMAT_VERSION + 1}).encode()
    blob = (mv.MAGIC + len(head).to_bytes(4, "little") + head
            + (0).to_bytes(4, "little") + b"\x00\x01")
    with pytest.raises(mv.BadMovie, match="newer build"):
        mv.load(blob)


def test_a_truncated_file_is_refused_not_silently_short():
    blob = mv.dump(_movie())
    with pytest.raises(mv.BadMovie):
        mv.load(blob[:12])


# ---------------------------------------------------------------- guards
def _fatal(problems):
    return [p.text for p in problems if p.fatal]


def test_a_different_cartridge_is_a_fatal_problem():
    """This is the failure that would discredit the whole feature: garbage that
    looks like an emulation bug."""
    problems = mv.check(_movie(), rom_sha="deadbeef", rom_name="other.ngc")
    assert _fatal(problems) and "different cartridge" in _fatal(problems)[0]


def test_the_same_bytes_under_another_name_is_only_a_note():
    problems = mv.check(_movie(), rom_sha="abc123", rom_name="renamed.ngc")
    assert not _fatal(problems)
    assert any("different file name" in p.text for p in problems)


def test_a_state_of_the_wrong_size_is_fatal():
    """It would be applied field-by-field onto a struct of another shape."""
    problems = mv.check(_movie(state=b"S" * 8), rom_sha="abc123", state_len=64)
    assert _fatal(problems) and "different version of the core" in _fatal(problems)[0]


def test_a_stateless_movie_is_allowed_but_says_what_it_needs():
    problems = mv.check(mv.Movie({}, b"", bytearray([0])))
    assert not _fatal(problems)
    assert any("reset first" in p.text for p in problems)


def test_an_empty_movie_is_fatal():
    assert _fatal(mv.check(mv.Movie({}, b"", bytearray())))


def test_a_matching_cartridge_raises_nothing():
    assert mv.check(_movie(), rom_sha="abc123", rom_name="game.ngc",
                    state_len=8) == []


# ---------------------------------------------------------------- record/play
def test_recording_keeps_the_button_bits_and_drops_power():
    """0x80 is POWER, not a button. Recording it would replay a power press."""
    r = mv.Recorder({"rom_name": "x"})
    r.record(0x81)
    r.record(0x00)
    assert list(r.movie.inputs) == [0x01, 0x00]


def test_replay_hands_back_the_bytes_in_order_then_stops():
    p = mv.Player(mv.Movie({}, b"", bytearray([1, 2, 3])))
    assert [p.next() for _ in range(3)] == [1, 2, 3]
    assert p.done
    assert p.next() is None, (
        "past the end it must report finished, not hold the last byte -- a replay "
        "that keeps pressing what was held on the final frame walks the game into "
        "a wall and calls it a reproduction")


def test_progress_is_reportable_while_playing():
    p = mv.Player(mv.Movie({}, b"", bytearray([0] * 4)))
    assert p.progress == 0.0
    p.next(); p.next()
    assert p.progress == 0.5


def test_an_empty_movie_player_is_immediately_done():
    p = mv.Player(mv.Movie({}, b"", bytearray()))
    assert p.done and p.next() is None and p.progress == 1.0


# ---------------------------------------------------------------- buttons
def test_button_names_match_the_bits_the_console_receives():
    """`ngpc_input` builds the byte written to 0x00B0. The names here come from
    ONE table (`core.hwregs.JOYPAD_BITS`) so the two cannot drift -- this table was
    first written from memory with A/B/Option a bit too high, and nothing else in
    the emulator would ever have contradicted it."""
    import ngpc_input
    from core.hwregs import JOYPAD_BITS

    expected = {"Up": ngpc_input.UP, "Down": ngpc_input.DOWN,
                "Left": ngpc_input.LEFT, "Right": ngpc_input.RIGHT,
                "A": ngpc_input.A, "B": ngpc_input.B, "Option": ngpc_input.OPTION}
    assert {name: 1 << bit for name, bit in JOYPAD_BITS} == expected


def test_buttons_text_reads_a_frame_out_loud():
    import ngpc_input
    assert mv.buttons_text(0) == "—"
    assert mv.buttons_text(ngpc_input.A) == "A"
    assert mv.buttons_text(ngpc_input.RIGHT | ngpc_input.A) == "Right+A"


def test_the_summary_says_how_long_and_how_busy():
    m = mv.Movie({"rom_name": "game.ngc"}, b"", bytearray([0, 1, 0, 1]))
    text = mv.summary(m)
    assert "4 frames" in text and "game.ngc" in text
    assert "2 frames with a button held" in text


def test_a_fingerprint_is_stable_and_short():
    assert mv.rom_fingerprint(b"abc") == mv.rom_fingerprint(b"abc")
    assert len(mv.rom_fingerprint(b"abc")) == 16
    assert mv.rom_fingerprint(b"abc") != mv.rom_fingerprint(b"abd")
