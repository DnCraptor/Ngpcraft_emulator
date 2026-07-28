"""Two consoles, two windows: same size, side by side -- and a single-frame mode.

⛔ WHAT THIS FIXES. "le mode link sur la meme machine ouvre une deuxieme fenetre, mais
elle est souvent ouverte par defaut par dessus la premiere et plus petite" -- request
2026-07-28. Player 2's window took a hard-coded 3x size (480x496) and whatever position
the window manager chose, which is on top of player 1. So the second player got a
smaller screen, placed over the first player's.

Two rules, and the first one is not cosmetic: the consoles are the same machine, and a
two-player game where one player has the bigger screen is not a fair one. So they are
always equal, and they never cover each other.

The single-frame mode is the same request taken further -- "un grand cadre qui gere la
taille des deux en meme temps et les maintient a taille egal". One row, equal stretch
factors: the layout owns both widths, so there is no size to keep in sync by hand.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

import ngpc_settings as cfg  # noqa: E402
import ngpc_shell as shell  # noqa: E402

GAP = shell.LINK_WINDOW_GAP


# ---- the geometry, with the screen as an argument -------------------------
# No Qt, no display: the awkward cases are the point, and none of them are reachable
# by opening a window on the machine that happens to run the tests.

def test_both_windows_are_the_same_size_always():
    one, two = shell.side_by_side((100, 50, 600, 500), (0, 0, 1920, 1080))
    assert one[2:] == two[2:], "the two consoles must be the same size"


def test_the_second_window_does_not_cover_the_first():
    one, two = shell.side_by_side((100, 50, 600, 500), (0, 0, 1920, 1080))
    assert two[0] >= one[0] + one[2], "player 2 overlaps player 1"
    assert two[0] == one[0] + one[2] + GAP


def test_player_one_is_left_alone_when_the_pair_already_fits():
    primary = (100, 50, 600, 500)
    one, _ = shell.side_by_side(primary, (0, 0, 1920, 1080))
    assert one == primary, "player 1 was moved or resized for no reason"


def test_a_narrow_screen_shrinks_BOTH_windows_not_one():
    # 2 x 600 + gap does not fit in 1000. Shrinking only player 2 would be the old bug
    # with extra steps.
    one, two = shell.side_by_side((0, 0, 600, 500), (0, 0, 1000, 800))
    assert one[2] == two[2], "one console was shrunk and the other was not"
    assert one[2] + two[2] + GAP <= 1000
    assert two[0] + two[2] <= 1000, "player 2 hangs off the right edge"


def test_the_pair_slides_back_onto_the_screen():
    # Player 1 sits far right; the pair would run off the edge, so it slides left.
    one, two = shell.side_by_side((1500, 50, 600, 500), (0, 0, 1920, 1080))
    assert two[0] + two[2] <= 1920
    assert one[0] >= 0


def test_a_screen_that_does_not_start_at_zero():
    # A second monitor to the left has a negative origin; a taskbar gives a non-zero y.
    avail = (-1920, 40, 1920, 1000)
    one, two = shell.side_by_side((-1900, 60, 600, 500), avail)
    for r in (one, two):
        assert r[0] >= avail[0] and r[0] + r[2] <= avail[0] + avail[2]
        assert r[1] >= avail[1] and r[1] + r[3] <= avail[1] + avail[3]


def test_a_window_taller_than_the_screen_is_brought_back_in():
    one, two = shell.side_by_side((0, 0, 400, 2000), (0, 0, 1920, 1000))
    assert one[3] == two[3] == 1000


# ---- the windows themselves ------------------------------------------------

@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _clean_settings():
    s = cfg.make_settings()
    s.clear()
    yield
    s.clear()


def test_the_frame_gives_the_two_consoles_equal_stretch(app):
    # The mechanism, not a pixel count: equal stretch is what makes "keeps them the
    # same size" true at every frame size instead of just at the one we opened at.
    p1 = shell.PlayPage(cfg.make_settings(), None)
    p2 = shell.PlayPage(cfg.make_settings(), None)
    frame = shell._Link2PFrame(p1, p2, cfg.make_settings())
    try:
        row = frame.centralWidget().layout()
        assert row.count() == 2
        assert row.stretch(0) == row.stretch(1) == 1
        assert row.itemAt(0).widget() is p1 and row.itemAt(1).widget() is p2
    finally:
        frame.close()
        frame.deleteLater()
        app.processEvents()


# ---- the shell actually launching each mode --------------------------------
# A synthetic cartridge, so this runs on any machine rather than skipping on the user's
# ROM folder -- the old Shell 2-player test skips for want of one, and a skipped test
# proves nothing about the thing the player complained about.

def _rom(path):
    rom = bytearray(b"\x00" * 0x8000)
    rom[0:28] = b" LICENSED BY SNK CORPORATION"
    rom[0x1C:0x20] = (0x200040).to_bytes(4, "little")
    rom[0x23] = 0x10
    rom[0x24:0x30] = b"LINKTEST\x00"
    rom[0x40:0x44] = b"\x00\x68\xFE\x00"          # nop ; jr $
    path.write_bytes(bytes(rom))
    return path


@pytest.fixture
def linked(app, tmp_path, monkeypatch, request):
    """A shell with player 1 running and player 2 launched, in the requested mode."""
    if not shell.DEFAULT_BIOS.is_file():
        pytest.skip("needs the local BIOS image to have player 1 running")
    from PyQt6.QtWidgets import QFileDialog
    rom2 = _rom(tmp_path / "p2.ngc")
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(rom2), "")))
    w = shell.Shell()
    w.library._stop_worker()          # a second native core is its own crash
    w.play._frames_due = lambda: 1
    w.play.start_bios()
    w.show(); app.processEvents()
    w._start_link_2p(framed=request.param)
    assert w._link2p is not None, "player 2 never opened"
    app.processEvents()
    yield w
    if w._link2p is not None:
        w._link2p.close()
    w.play.stop()
    w.library._stop_worker()
    w.close(); w.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("linked", [False], indirect=True)
def test_player_two_opens_the_same_size_as_player_one(linked):
    # THE unconditional rule, and the one the player actually complained about. It holds
    # on any screen, which is why it is asserted here on whatever screen runs the tests.
    w = linked
    one, two = w.frameGeometry(), w._link2p.frameGeometry()
    assert (two.width(), two.height()) == (one.width(), one.height()), (
        f"player 2 opened at {two.width()}x{two.height()} against player 1's "
        f"{one.width()}x{one.height()}")


@pytest.mark.parametrize("linked", [False], indirect=True)
def test_player_two_is_beside_player_one_when_the_screen_allows(linked):
    # Conditional BY CONTRACT, not to dodge a failure: side by side is "quand c'est
    # possible", and the offscreen platform this runs under reports a screen too narrow
    # for two windows at their real minimum, where the contract says cascade. Asserting
    # non-overlap unconditionally would be asserting something the design does not
    # promise -- the geometry tests above pin the fitting case exactly.
    w = linked
    one, two = w.frameGeometry(), w._link2p.frameGeometry()
    avail = (w.screen() or QApplication.primaryScreen()).availableGeometry()
    if 2 * one.width() + GAP <= avail.width():
        assert not one.intersects(two), "they fit side by side, and still overlapped"
    else:
        assert (two.x(), two.y()) != (one.x(), one.y()), (
            "too narrow for side by side, so they must at least be offset -- player 2 "
            "sitting exactly on player 1 is the original complaint")


@pytest.mark.parametrize("linked", [True], indirect=True)
def test_the_framed_mode_holds_both_consoles_in_one_window(linked):
    w = linked
    frame = w._link2p
    assert isinstance(frame, shell._Link2PFrame)
    row = frame.centralWidget().layout()
    assert row.itemAt(0).widget() is w.play, "player 1 is not in the frame"
    assert row.itemAt(1).widget() is frame.play
    assert w.isHidden(), "the shell window is still up behind its own frame"


@pytest.mark.parametrize("linked", [True], indirect=True)
def test_the_framed_consoles_end_up_the_same_width_on_screen(linked, app):
    """The OUTCOME, not the layout's settings -- and the difference is not academic.

    With correct 1:1 stretch factors the rendered frame still came out 767 against 726:
    a page taken out of a QStackedWidget stays hidden (and gets nothing), and equal
    stretch only shares the SURPLUS above each widget's own minimum, which differ
    because player 2 hides the 🔗 button. Both were found by looking at the picture,
    neither by reading `stretch()`.
    """
    w = linked
    frame = w._link2p
    row = frame.centralWidget().layout()
    p1, p2 = row.itemAt(0).widget(), row.itemAt(1).widget()
    for size in (1600, 1200, 2000):          # and it must hold at ANY frame size
        frame.resize(size, 620)
        app.processEvents()
        assert p1.width() == p2.width(), (
            f"frame {frame.width()}px: player 1 got {p1.width()}, player 2 {p2.width()}")
        assert p1.isVisible() and p2.isVisible(), "a console is hidden inside the frame"


@pytest.mark.parametrize("linked", [True], indirect=True)
def test_leaving_the_frame_gives_player_one_back_to_the_shell(linked):
    w = linked
    page = w.play
    w._link2p.close()
    assert w._stack.indexOf(page) != -1, "player 1 never went back into the shell"
    assert w._stack.currentWidget() is page
    assert not w.isHidden(), "the shell stayed hidden after the frame closed"
    assert page.machine is not None, "player 1's console was torn down with the frame"


def test_closing_the_frame_stops_only_player_two(app):
    # Player 1 belongs to the shell and goes back into it; stopping it here would end
    # the session the player is still in.
    stopped = []
    p1 = shell.PlayPage(cfg.make_settings(), None)
    p2 = shell.PlayPage(cfg.make_settings(), None)
    p1.stop = lambda: stopped.append("p1")
    p2.stop = lambda: stopped.append("p2")
    frame = shell._Link2PFrame(p1, p2, cfg.make_settings())
    try:
        frame.close()
        assert stopped == ["p2"], f"the frame stopped the wrong console(s): {stopped}"
    finally:
        frame.deleteLater()
        app.processEvents()
