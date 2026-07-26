"""The modern shell (`ngpc_shell.py`) — Qt-offscreen structure + settings tests.

Skips cleanly when PyQt6 is absent, like the other UI tests. Runs under the
offscreen QPA platform so it needs no display. It does NOT boot a ROM here (that
is exercised elsewhere / by hand); it checks the shell wiring and the settings
round-trip, which is what the front-end contract is.
"""

from __future__ import annotations

import os
import pathlib
import tempfile

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt, QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel  # noqa: E402

import ngpc_settings as cfg  # noqa: E402
import ngpc_shell as shell  # noqa: E402
import ngpc_theme as th  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    yield a


@pytest.fixture(autouse=True)
def _clean_settings():
    # This clears the REAL scope -- `make_settings()` has no test mode. What keeps it
    # off the user's registry is `pytest_configure` in the root conftest, which points
    # QSettings at a temp .ini before collection. Without that redirect these two
    # lines delete the user's BIOS path and ROM folder on every test.
    s = cfg.make_settings()
    s.clear()
    yield
    s.clear()


def test_the_suite_never_touches_real_settings():
    """The guard for the conftest redirect. This fixture calls `.clear()` on
    `QSettings("NgpCraft", "Emulator")` around EVERY test in this file; if that ever
    resolves to the user's own scope again, a test run eats their configuration --
    silently, because wiping settings is not something a passing test complains about.
    """
    from PyQt6.QtCore import QSettings

    s = cfg.make_settings()
    assert s.format() == QSettings.Format.IniFormat, \
        "settings must not resolve to the native store (the Windows registry)"
    where = pathlib.Path(s.fileName()).resolve()
    tmp = pathlib.Path(tempfile.gettempdir()).resolve()
    assert where.is_relative_to(tmp), f"tests would write real settings at {where}"


def test_shell_builds_with_three_pages(app):
    w = shell.Shell()
    try:
        assert w._stack.count() == 3
        # rail nav toggles page + checked state
        w._go(1)
        assert w._stack.currentWidget() is w.settings
        assert w._nav_set.isChecked() and not w._nav_lib.isChecked()
    finally:
        w.close()


def test_key_binding_round_trips_through_settings(app):
    s = cfg.make_settings()
    cfg.set_binding(s, "A", int(Qt.Key.Key_J))
    mapping = cfg.key_bindings(s)
    assert mapping.get(int(Qt.Key.Key_J)) == 0x10, "A must map to the joypad A bit"


def test_settings_defaults_are_sane(app):
    s = cfg.make_settings()
    assert cfg.lcd_scale(s) == 3
    assert cfg.audio_enabled(s) is True
    assert cfg.language(s) == "en"
    # every button has a default key
    m = cfg.key_bindings(s)
    assert {0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40} <= set(m.values())


def test_language_switch_retranslates_the_rail(app):
    w = shell.Shell()
    try:
        s = cfg.make_settings()
        s.setValue("general/language", "fr")
        w._retranslate()
        assert "Bibliothèque" in w._nav_lib.text()
        assert "Réglages" in w._nav_set.text()
    finally:
        w.close()


def test_the_rail_fits_every_language_it_ships(app):
    """The rail used to be a fixed 190 px while its labels are TRANSLATED -- so a
    language with longer words (Portuguese "Ferramentas de depuração", but French
    "Bibliothèque" already) had its entries cut off. It now measures itself.

    The contract, per language: every nav entry shows its text IN FULL -- on one line
    if it fits, wrapped onto two if it does not -- or, only when even two lines cannot
    hold it, carries a tooltip. The rail is capped, so it can never eat the window.

    Wrapping rather than shortening is the point: `m_debug` in Portuguese is
    "Ferramentas de depuração", and rewording a contributor's translation to fit a
    sidebar is not the layout's call to make.
    """
    from PyQt6.QtGui import QFont, QFontMetrics

    w = shell.Shell()
    try:
        w.show()
        s = cfg.make_settings()
        assert set(cfg.STRINGS) >= {"en", "fr"}, "at least the two original languages"
        for lang in sorted(cfg.STRINGS):
            s.setValue("general/language", lang)
            w._retranslate()
            rail = w._rail.width()
            assert shell.RAIL_MIN_W <= rail <= shell.RAIL_MAX_W
            for b, label in w._nav_text.items():
                b.ensurePolished()
                f = QFont(b.font()); f.setBold(True)
                fm = QFontMetrics(f)
                # whatever the layout, the words shown are the words translated
                assert b.text().split() == label.split(), f"[{lang}] {label!r} altered"
                need = max(fm.horizontalAdvance(ln) for ln in b.text().split("\n"))
                assert need + shell.RAIL_TEXT_PAD <= rail or b.toolTip() == label, (
                    f"[{lang}] {label!r} needs {need + shell.RAIL_TEXT_PAD}px of a "
                    f"{rail}px rail and has no tooltip to fall back on")
        # collapsing still wins over any measured width, and expanding restores it
        w._toggle_rail(False)
        assert w._rail.width() == shell.RAIL_COLLAPSED_W
        w._toggle_rail(True)
        assert w._rail.width() == w._rail_w
    finally:
        w.close()


def test_a_long_nav_label_wraps_instead_of_being_clipped(app):
    """A label no single line can hold is split over two, at the balanced boundary --
    the one that leaves the widest line narrowest, since that is what the rail costs."""
    from PyQt6.QtGui import QFont, QFontMetrics

    w = shell.Shell()
    try:
        w.show()
        b = w._nav_dbg
        b.ensurePolished()
        f = QFont(b.font()); f.setBold(True)
        fm = QFontMetrics(f)
        # Build a label that overflows one rail line in the font Qt WILL PAINT WITH,
        # rather than hard-coding a string and betting it is wide enough. That bet lost
        # on the CI: its offscreen QPA falls back to a narrower font than Windows, so
        # "Ferramentas de depuração" (the original literal) fit one line there, the wrap
        # under test never fired, and this failed on Linux/Mac only.
        #
        # SHORT, uniform words, grown one at a time until the line JUST passes the cap.
        # Two properties the assertions below need, on any font: overflowing by a single
        # short word keeps the balanced split's widest line under the cap (so no tooltip
        # fires), and equal words make that split a clean near-middle break. A long seed
        # word ("Ferramentas"/"depuração") could already blow past twice the cap on a
        # wide font, leaving no two-line fit -- which is the trap this avoids.
        cap = shell.RAIL_MAX_W - shell.RAIL_TEXT_PAD
        word = "log"
        words = [word, word]
        while fm.horizontalAdvance(shell.RAIL_INDENT + " ".join(words)) <= cap:
            words.append(word)
        long_label = " ".join(words)
        w._nav_text = dict(w._nav_text)          # leave the real labels alone
        w._nav_text[b] = long_label
        w._fit_rail()

        assert "\n" in b.text(), "a label this long must not stay on one line"
        assert b.text().split() == long_label.split(), "wrapping must not drop a word"
        assert not b.toolTip(), "it fits on two lines -- no tooltip needed"
        assert b.sizeHint().height() > w._nav_lib.sizeHint().height(), \
            "the two-line entry is taller than a one-line one"
        # The split is the width-minimising one (what `_wrap_nav` promises), not a greedy
        # first-line fill. Check the chosen widest line equals the best any single break
        # can do -- computed the same way the code does, so it holds in any font.
        best = min(
            max(fm.horizontalAdvance(shell.RAIL_INDENT + " ".join(words[:i])),
                fm.horizontalAdvance(shell.RAIL_INDENT + " ".join(words[i:])))
            for i in range(1, len(words)))
        chosen = max(fm.horizontalAdvance(ln) for ln in b.text().split("\n"))
        assert chosen == best, f"wrap must pick the width-minimising split ({chosen} vs {best})"
    finally:
        w.close()


def test_theme_switch_restyles_the_window(app):
    w = shell.Shell()
    try:
        s = cfg.make_settings()
        s.setValue("general/theme", th.THEME_LIGHT)
        w._restyle()
        assert shell.PALETTE is th.LIGHT
        assert th.LIGHT.bg_window in w.styleSheet()
        s.setValue("general/theme", th.THEME_DARK)
        w._restyle()
        assert shell.PALETTE is th.DARK
        assert th.DARK.bg_window in w.styleSheet()
    finally:
        w.close()


def test_no_widget_falls_through_to_the_os_palette(app):
    """The bug this theming exists to kill.

    Every widget class the app instantiates must get a background from OUR
    stylesheet. One that does not gets the OS's colours while `*` still forces
    our text colour onto it -- which is invisible when the user's Windows theme
    runs opposite to the app's, and which looks perfectly fine to a developer
    whose OS theme happens to match. Only a test catches that."""
    for palette in (th.DARK, th.LIGHT):
        css = th.build_style(palette)
        for widget in ("QTableWidget", "QPlainTextEdit", "QTabWidget::pane",
                       "QMenu", "QToolTip", "QDialog",
                       "QComboBox QAbstractItemView"):
            assert widget in css, f"{widget} has no rule: it will use OS colours"


def test_every_palette_colour_actually_parses(app):
    """Qt accepts no 8-digit #RRGGBBAA: it parsed "#4aa3ff22" as #a3ff22 and
    painted the selected rail item lime green for the whole life of the code,
    with no warning. An unparseable colour must fail here, not on screen."""
    from PyQt6.QtGui import QColor

    for palette in (th.DARK, th.LIGHT):
        for field in palette.__dataclass_fields__:
            value = getattr(palette, field)
            if not isinstance(value, str) or not value.startswith("#"):
                continue
            c = QColor()
            c.setNamedColor(value)
            assert c.isValid() and c.name() == value.lower(), (
                f"{field}={value!r} does not round-trip: Qt reads it as {c.name()}")


def test_light_theme_never_reuses_a_dark_colour(app):
    """A light theme built by copy-paste keeps a few dark values by accident, and
    each one is an unreadable patch. Nothing may be shared but the fixed
    console-screen colours, which are deliberately theme-independent."""
    shared = {f.name for f in th.DARK.__dataclass_fields__.values()
              if getattr(th.DARK, f.name) == getattr(th.LIGHT, f.name)}
    assert shared == set(), f"light theme still carries dark values: {shared}"


def test_console_art_loads_and_is_declared_to_pyinstaller(app):
    """The key map is a picture; without it the panel is fields floating in space.

    Two ways that breaks, both silent: the file goes missing, or it exists in the
    repo but is absent from the .spec -- PyInstaller follows imports, not file
    reads, so an asset opened by path is invisible to it and never reaches the
    .exe. The packaged app would show an empty console and no error."""
    import ngpc_bindmap

    assert ngpc_bindmap.ART.is_file(), f"missing console art: {ngpc_bindmap.ART}"
    from PyQt6.QtGui import QPixmap
    assert not QPixmap(str(ngpc_bindmap.ART)).isNull(), "console art will not decode"

    spec = (pathlib.Path(__file__).resolve().parent.parent / "NgpCraftEmulator.spec")
    assert ngpc_bindmap.ART.name in spec.read_text(encoding="utf-8"), (
        f"{ngpc_bindmap.ART.name} is not in the .spec datas: it will be missing "
        "from the built .exe even though the tests pass from source")


def test_bind_map_covers_every_joypad_button(app):
    """Every bindable button needs a field, or a binding becomes unreachable from
    the UI. POWER is deliberately absent: only 7 joypad bits exist (0x80 is POWER
    and the core drives it), so a POWER field would be a dead control."""
    w = shell.Shell()
    try:
        fields = set(w.settings._bindmap.buttons)
        assert fields == {lbl for lbl, _mask in cfg.JOYPAD_BUTTONS}
        assert "Power" not in fields
    finally:
        w.close()


def test_settings_page_writes_graphics_scale(app):
    w = shell.Shell()
    try:
        w.settings._scale.setValue(5)
        assert cfg.lcd_scale(cfg.make_settings()) == 5
    finally:
        w.close()


def test_controls_panel_has_seven_capture_buttons(app):
    w = shell.Shell()
    try:
        assert len(w.settings._keybtns) == 7
    finally:
        w.close()


def test_console_boot_defaults_off(app):
    # Default hand-off: the real-BIOS boot loops on "SUB BATTERY DEAD" (RTC /
    # sub-battery not modelled yet), so games boot via hand-off until that lands.
    assert cfg.real_bios(cfg.make_settings()) is False


# ⚠️ A ROM folder that EXISTS is not a ROM folder that has ROMs in it. A clean
# checkout ships `roms/` with only a README (cartridge images are never
# distributed), so testing `is_dir()` alone let these tests run with nothing to
# load and fail on `assert rom is not None` -- a red suite that means "you have
# no ROMs", not "the emulator is broken". Require an actual cartridge.
_HAVE_ROMS = (
    shell.DEFAULT_BIOS.is_file()
    and shell.DEFAULT_ROM_DIR.is_dir()
    and any(shell.DEFAULT_ROM_DIR.glob("*.ng[cp]"))
)


@pytest.mark.skipif(not _HAVE_ROMS, reason="needs the local ROM folder + BIOS")
def test_handoff_boot_reaches_the_cartridge(app):
    from pathlib import Path
    rom = next(iter(sorted(shell.DEFAULT_ROM_DIR.glob("*.ngc"))), None)
    assert rom is not None
    w = shell.Shell()
    try:
        w.play._frames_due = lambda: 6      # bypass the wall-clock pacer
        w.play.start(Path(rom))
        assert w.play._real_bios is False   # default hand-off
        for _ in range(30):
            w.play._tick()
        pc = w.play.machine.cpu().pc
        assert 0x200000 <= pc < 0x400000, f"should be in cartridge code, got 0x{pc:06X}"
    finally:
        w.play.stop()
        w.close()


@pytest.mark.skipif(not _HAVE_ROMS, reason="needs the local BIOS image")
def test_bios_alone_boots_without_a_cartridge(app):
    w = shell.Shell()
    try:
        w.play._frames_due = lambda: 6
        w.play.start_bios()
        assert w.play.session is None and w.play._raw is not None
        for _ in range(120):
            w.play._tick()
        assert w.play._power_pressed is True
        assert len(set(w.play.machine.framebuffer())) > 3
    finally:
        w.play.stop()
        w.close()


# ---- video filter pipeline (no ROM needed) ----
def test_video_filters_produce_the_right_size_and_darken(app):
    import numpy as np
    import ngpc_video as v
    fb = [((x & 0xF) | ((y & 0xF) << 4) | (((x + y) & 0xF) << 8))
          for y in range(v.SCREEN_H) for x in range(v.SCREEN_W)]
    for filt in v.FILTERS:
        a = v.render_array(fb, 4, filt, v.COLOR_RAW)
        assert a.shape == (v.SCREEN_H * 4, v.SCREEN_W * 4, 3)
    base = v.render_array(fb, 4, v.FILTER_NONE, v.COLOR_RAW).astype(int).sum()
    scan = v.render_array(fb, 4, v.FILTER_SCANLINES, v.COLOR_RAW).astype(int).sum()
    assert scan < base, "scanlines must darken the image"


def test_video_settings_round_trip(app):
    import ngpc_video as v
    s = cfg.make_settings()
    s.setValue("gfx/filter", v.FILTER_CRT)
    s.setValue("gfx/color", v.COLOR_LCD)
    s.setValue("gfx/aspect", v.ASPECT_FIT)
    assert cfg.video_filter(s) == v.FILTER_CRT
    assert cfg.color_profile(s) == v.COLOR_LCD
    assert cfg.aspect_mode(s) == v.ASPECT_FIT
    # bad values fall back to safe defaults
    s.setValue("gfx/filter", "garbage")
    assert cfg.video_filter(s) == v.FILTER_NONE


@pytest.mark.skipif(not _HAVE_ROMS, reason="needs the local ROM folder + BIOS")
def test_escape_opens_the_pause_menu_and_keeps_the_game_alive(app):
    from pathlib import Path
    from PyQt6.QtCore import QEvent
    from PyQt6.QtGui import QKeyEvent
    rom = next(iter(sorted(shell.DEFAULT_ROM_DIR.glob("*.ngc"))), None)
    assert rom is not None
    w = shell.Shell()
    try:
        w.play._frames_due = lambda: 4
        w._launch(str(rom))
        for _ in range(8):
            w.play._tick()
        w.play.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, int(Qt.Key.Key_Escape),
                                       Qt.KeyboardModifier.NoModifier))
        # THE FIX: Escape pauses into a menu; it does NOT unload the game.
        assert w.play._menu_open and w.play.paused
        assert w.play.machine is not None
        assert w._stack.currentWidget() is w.play
        # in-game options keep the game alive and jump to its settings
        w.play._on_menu_choice("video")
        assert w._stack.currentWidget() is w.settings
        assert w.play.machine is not None and w.play.paused
        assert w.settings._cats.currentRow() == 1
        # resume returns to the live game
        w.settings.resume_requested.emit()
        assert w._stack.currentWidget() is w.play and not w.play.paused
        # quit is the only thing that unloads it
        w.play._on_menu_choice("quit")
        assert w.play.machine is None
        assert w._stack.currentWidget() is w.library
    finally:
        w.play.stop()
        w.close()


@pytest.mark.skipif(not _HAVE_ROMS, reason="needs the local ROM folder + BIOS")
def test_debug_window_reads_every_tab(app):
    from pathlib import Path
    rom = next(iter(sorted(shell.DEFAULT_ROM_DIR.glob("*.ngc"))), None)
    assert rom is not None
    w = shell.Shell()
    try:
        w.play._frames_due = lambda: 4
        w._launch(str(rom))
        for _ in range(40):
            w.play._tick()
        w._open_debug()
        dbg = w._debug_win
        for i in range(dbg._tabs.count()):
            dbg._tabs.setCurrentIndex(i)
            dbg.refresh()      # must not raise on any tab
        assert "PC" in dbg._cpu_text.toPlainText()
        assert len(dbg._dis_text.toPlainText().splitlines()) > 5
        assert len(dbg._mem_text.toPlainText().splitlines()) == 24
        dbg._step()            # single-frame step must not raise
    finally:
        if w._debug_win is not None:
            w._debug_win.close()
        w.play.stop()
        w.close()


@pytest.mark.skipif(not _HAVE_ROMS, reason="needs the local ROM folder + BIOS")
def test_debug_exports_and_trace_to_file(app, tmp_path, monkeypatch):
    from pathlib import Path
    import ngpc_debug
    rom = next(iter(sorted(shell.DEFAULT_ROM_DIR.glob("*.ngc"))), None)
    assert rom is not None

    def fake_save(parent, title, default, filt):
        return (str(tmp_path / Path(default).name), "")
    monkeypatch.setattr(ngpc_debug.QFileDialog, "getSaveFileName", staticmethod(fake_save))

    w = shell.Shell()
    try:
        w.play._frames_due = lambda: 4
        w._launch(str(rom))
        for _ in range(40):
            w.play._tick()
        w._open_debug()
        dbg = w._debug_win
        # trace a run of instructions to a file
        dbg._trace_count.setValue(2000)
        dbg._trace_to_file()
        trace = (tmp_path / "trace.txt").read_text(encoding="utf-8").splitlines()
        assert len(trace) > 1000, "trace file should hold a long run of instructions"
        # text + image exports land on disk
        dbg._tabs.setCurrentIndex(0); dbg.refresh()
        dbg._save_text(dbg._cpu_text.toPlainText(), "cpu_state.txt")
        dbg._tabs.setCurrentIndex(3); dbg.refresh(); dbg._save_png(dbg._pal_arr, "palette.png")
        dbg._tabs.setCurrentIndex(4); dbg.refresh(); dbg._save_png(dbg._tiles_arr, "tiles.png")
        assert (tmp_path / "cpu_state.txt").stat().st_size > 0
        assert (tmp_path / "palette.png").stat().st_size > 0
        assert (tmp_path / "tiles.png").stat().st_size > 0
        # freeze stops auto-refresh
        dbg._freeze.setChecked(True)
        assert dbg._frozen
        dbg._on_timer()        # a timer tick while frozen must be a no-op (not raise)
    finally:
        if w._debug_win is not None:
            w._debug_win.close()
        w.play.stop()
        w.close()


def test_tile_hover_reports_address_and_click_copies(app):
    """The tile viewer answers 'which tile is this, where does it live, who uses it,
    what are its bytes' on hover, and a click puts that on the clipboard -- the numbers
    you need to poke or replace a tile. Pure data path, no ROM: the caches hover reads
    are set directly and `_tile_info` is asked for a specific cell."""
    import numpy as np
    import ngpc_debug as dbg_mod
    from PyQt6.QtWidgets import QApplication

    dbg = dbg_mod.DebugWindow(None, cfg.make_settings())
    try:
        n = 300                                    # past 255, so the sprite-only note fires
        char = bytearray(n * dbg_mod.TILE_BYTES)
        char[5 * 16:5 * 16 + 3] = b"\xDE\xAD\xBE"  # a fingerprint in tile 5's bytes
        usage = np.zeros(dbg_mod.CHAR_RAM_TILES, np.uint8)
        usage[5] = dbg_mod.USE_SCR1 | dbg_mod.USE_SPRITE     # shared, to exercise the label
        dbg._tiles_char = bytes(char)
        dbg._tiles_usage = usage
        dbg._tiles_n = n

        # tile 5 -> col 5, row 0. Address is CHAR_RAM + 5*16 = 0x00A050.
        info = dbg._tile_info(5, 0)
        assert "tile 5 (0x005)" in info
        assert "0x00A050" in info and "0x00A05F" in info
        assert "shared" in info and "SCR1" in info and "sprites" in info
        assert "DE AD BE" in info

        # a tile past the last one present has nothing to say
        assert dbg._tile_info(0, n // 16 + 1) is None

        # a high tile carries the 9-bit sprite-addressing note
        assert "sprite ref" in dbg._tile_info(299 % 16, 299 // 16)

        # a click copies the block and the status line confirms it
        dbg._tile_status(info, copy=True)
        assert QApplication.clipboard().text() == info
        assert dbg._tile_status_line.text().startswith("✔ copied")
        # a hover just shows it, without touching the clipboard
        dbg._tile_status(dbg._tile_info(0, 0), copy=False)
        assert not dbg._tile_status_line.text().startswith("✔")

        # the grid's hit size is locked to the sheet geometry, so a click lands on the
        # tile under the cursor and not its neighbour.
        assert dbg._tile_label._cell == dbg_mod.TILE_ATLAS_PITCH * dbg_mod.TILE_ATLAS_SCALE
    finally:
        dbg.close()


def test_text_tab_decodes_and_searches_via_a_loaded_table(app, tmp_path):
    """The Text tab is the fan-translation half of the debugger, and it works on ANY
    ROM through a user .tbl. With a table loaded and a stub machine standing in for the
    core, it decodes a region into strings, finds a phrase by its exact bytes, and --
    with no table -- cracks the encoding by letter spacing. No emulator, no real ROM."""
    import ngpc_debug as dbg_mod
    from core.texttable import parse_tbl

    class _FakeMem:                       # the slice of address space the tab reads
        def __init__(self, blob): self._blob = blob
        def read(self, addr, n): return bytes(self._blob[addr:addr + n])

    class _FakePlay:                      # `_m` is a property off `_play.machine`
        def __init__(self, mem): self.machine = mem

    # 0x10='h' 0x11='i', FF terminates. "hi"<end> planted at offset 0x20.
    blob = bytearray(0x400)
    blob[0x20:0x23] = bytes([0x10, 0x11, 0xFF])
    dbg = dbg_mod.DebugWindow(None, cfg.make_settings())
    try:
        dbg._play = _FakePlay(_FakeMem(blob))
        dbg._txt_table = parse_tbl("10=h\n11=i\n/FF=<end>")

        # decode: the string and its address show up
        dbg._txt_addr.setText("000020"); dbg._txt_len.setValue(16); dbg._txt_decode()
        assert "000020" in dbg._txt_out.toPlainText()
        assert "'hi'" in dbg._txt_out.toPlainText()

        # table search: the exact bytes are found at 0x20
        dbg._txt_from.setText("000000"); dbg._txt_size.setValue(1)
        dbg._txt_find.setText("hi"); dbg._txt_mode.setCurrentText("Table")
        dbg._txt_search()
        hits = dbg._txt_hits.toPlainText()
        assert "1 match" in hits and "000020" in hits

        # relative search: no table needed, and it derives the encoding it found
        dbg._txt_mode.setCurrentText("Relative"); dbg._txt_search()
        rel = dbg._txt_hits.toPlainText()
        assert "000020" in rel
        assert "'h'=10" in rel and "'i'=11" in rel, "relative hit hands back the bytes"
    finally:
        dbg.close()


def test_fullscreen_is_exited_by_escape_and_double_click(app, monkeypatch):
    """Regression for 'stuck in fullscreen': Escape and a double-click both return to
    windowed. The real fullscreen transition crashes under offscreen QPA, so the window
    state is mocked and the heavy apply is stubbed -- what is checked is that both routes
    clear the fullscreen setting, base the flip on the window's real state, and that
    Escape only intercepts WHILE fullscreen."""
    from PyQt6.QtCore import QEvent, QPointF
    from PyQt6.QtGui import QKeyEvent, QMouseEvent

    class _FakeWin:
        def __init__(self, fs): self._fs = fs
        def isFullScreen(self): return self._fs

    w = shell.Shell()
    try:
        p = w.play
        state = {"fs": True}
        monkeypatch.setattr(p, "window", lambda: _FakeWin(state["fs"]))
        monkeypatch.setattr(p, "apply_settings", lambda: None)   # skip the real transition
        monkeypatch.setattr(p, "_reblit_soon", lambda: None)

        esc = QKeyEvent(QEvent.Type.KeyPress, int(Qt.Key.Key_Escape),
                        Qt.KeyboardModifier.NoModifier)

        # Escape while fullscreen -> the setting is cleared
        p._settings.setValue("gfx/fullscreen", True)
        p.keyPressEvent(esc)
        assert not cfg.fullscreen(p._settings), "Escape in fullscreen returns to windowed"

        # double-click on the canvas while fullscreen -> cleared too
        p._settings.setValue("gfx/fullscreen", True)
        dbl = QMouseEvent(QEvent.Type.MouseButtonDblClick, QPointF(5, 5), QPointF(5, 5),
                          Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                          Qt.KeyboardModifier.NoModifier)
        p.lcd.mouseDoubleClickEvent(dbl)
        assert not cfg.fullscreen(p._settings), "double-click in fullscreen returns to windowed"

        # a double-click while WINDOWED goes the other way (into fullscreen)
        state["fs"] = False
        p._settings.setValue("gfx/fullscreen", False)
        p.lcd.mouseDoubleClickEvent(dbl)
        assert cfg.fullscreen(p._settings), "double-click windowed -> fullscreen"
    finally:
        w.close()


def test_toolbar_auto_hides_when_idle_and_returns_on_move(app):
    """Feature: the player toolbar hides after the mouse goes still and comes back on any
    move, staying available without sitting over the game. The idle hide is transient and
    kept apart from the user's saved show/hide preference. State is driven directly (no
    real timer/mouse), and `isHidden()` is checked -- `isVisible()` also needs the window
    shown, which a headless test is not."""
    w = shell.Shell()
    try:
        p = w.play
        p.machine = object()                       # a game is 'running'
        w._settings.setValue("gfx/toolbar", True)
        w._settings.setValue("gfx/toolbar_autohide", True)

        p.refresh_toolbar()
        assert not p.toolbar.isHidden(), "windowed + preference on -> toolbar up"
        assert p._autohide_timer.isActive(), "the idle countdown is armed"

        # the mouse goes still -> the idle timeout hides it (but not the preference)
        p._idle_hide_toolbar()
        assert p._idle_hidden and p.toolbar.isHidden()

        # any move brings it back and re-arms the countdown
        p._on_pointer_activity()
        assert not p._idle_hidden and not p.toolbar.isHidden()
        assert p._autohide_timer.isActive()

        # option off -> a still mouse must NOT hide it
        w._settings.setValue("gfx/toolbar_autohide", False)
        p._idle_hide_toolbar()
        assert not p.toolbar.isHidden()

        # a manual hide is the preference (nub shown), never an idle hide, and stops the timer
        w._settings.setValue("gfx/toolbar_autohide", True)
        p._toggle_toolbar(False)
        assert p.toolbar.isHidden() and not p._bar_show.isHidden()
        assert not p._autohide_timer.isActive(), "nothing up to auto-hide"
    finally:
        p.machine = None
        w.close()


def test_both_windows_can_be_made_small(app):
    """Long, unwrapped help/description labels used to force an enormous minimum window
    (main ~1732 wide, debugger ~3266) -- you could not shrink either. They wrap now, so
    both windows honour a small size. Regression for 'let me make the windows smaller'."""
    import ngpc_debug as dbg_mod
    from PyQt6.QtWidgets import QApplication

    w = shell.Shell()
    try:
        w.show(); QApplication.processEvents()
        w.resize(420, 360); QApplication.processEvents()
        assert w.width() <= 460 and w.height() <= 400, \
            f"main window stuck large: {w.width()}x{w.height()}"

        d = dbg_mod.DebugWindow(w, cfg.make_settings())
        try:
            d.show(); QApplication.processEvents()
            d.resize(400, 340); QApplication.processEvents()
            assert d.width() <= 440 and d.height() <= 380, \
                f"debug window stuck large: {d.width()}x{d.height()}"
        finally:
            d.close()
    finally:
        w.close()


def test_sync_fullscreen_chrome_is_safe_before_the_ui_exists():
    """A WindowStateChange can fire mid-construction (restoreGeometry / first show) before
    the rail and play page exist. `_sync_fullscreen_chrome` must no-op then, not crash --
    regression for the AttributeError on `_rail` at startup. Called on a bare object so it
    exercises the guard without a full window."""
    class _Bare:
        pass
    shell.Shell._sync_fullscreen_chrome(_Bare())   # must not raise


def test_fullscreen_hides_and_restores_sidebar_and_toolbar(app, monkeypatch):
    """Feature: fullscreen can hide the sidebar and the player toolbar so the game gets
    the whole screen, and leaving fullscreen puts them back — the toolbar to the user's
    saved preference, never forced on. Driven by `_sync_fullscreen_chrome`; the window
    state is mocked so no real (and offscreen-crashy) fullscreen transition is needed.
    `isHidden()` is checked rather than `isVisible()` because the test window is not
    shown, which would make everything report not-visible regardless."""
    w = shell.Shell()
    try:
        state = {"fs": False}
        monkeypatch.setattr(w, "isFullScreen", lambda: state["fs"])
        w._settings.setValue("gfx/fs_hide_ui", True)
        w._settings.setValue("gfx/toolbar", True)      # user keeps the toolbar normally

        state["fs"] = False; w._sync_fullscreen_chrome()
        assert not w._rail.isHidden() and not w.play.toolbar.isHidden(), "windowed: chrome shown"

        state["fs"] = True; w._sync_fullscreen_chrome()
        assert w._rail.isHidden() and w.play.toolbar.isHidden(), "fullscreen: chrome hidden"
        assert w.play._bar_show.isHidden(), "no 'show toolbar' nub either"

        # ...but the toolbar is only AUTO-hidden: a mouse move brings it back over the game
        # (the sidebar stays gone). This is the fix for 'toolbar never shows in fullscreen'.
        w.play.machine = object()
        w.play._on_pointer_activity()
        assert not w.play.toolbar.isHidden(), "a move reveals the fullscreen toolbar"
        assert w._rail.isHidden(), "...but not the sidebar"
        w.play.machine = None
        w.play._idle_hidden = True     # back to the resting hidden state for the next step

        state["fs"] = False; w._sync_fullscreen_chrome()
        assert not w._rail.isHidden() and not w.play.toolbar.isHidden(), "restored on exit"

        # the option off -> fullscreen keeps the chrome
        w._settings.setValue("gfx/fs_hide_ui", False)
        state["fs"] = True; w._sync_fullscreen_chrome()
        assert not w._rail.isHidden() and not w.play.toolbar.isHidden(), "opt off: chrome kept"

        # option on, but the toolbar was hidden by choice -> exit must not force it back
        w._settings.setValue("gfx/fs_hide_ui", True)
        w._settings.setValue("gfx/toolbar", False)
        state["fs"] = True; w._sync_fullscreen_chrome()
        assert w._rail.isHidden()
        state["fs"] = False; w._sync_fullscreen_chrome()
        assert not w._rail.isHidden() and w.play.toolbar.isHidden(), "toolbar stays as the user left it"
    finally:
        w.close()


def test_paused_frame_refits_after_a_layout_change(app):
    """Hiding the toolbar / going fullscreen resizes the canvas, but only a RUNNING tick
    re-blits — so a paused game kept an old, mis-scaled (stretched) frame. These paths now
    schedule a deferred re-fit once the layout has settled. Regression for the user report
    'aspect stretches after fullscreen and hiding the sidebar/toolbar'."""
    from PyQt6.QtWidgets import QApplication

    w = shell.Shell()
    try:
        play = w.play
        play.machine = object()                 # non-None so the blit guard passes
        calls = []
        play._blit = lambda: calls.append(1)     # count re-fits; skip the real numpy/Qt path

        # two requests in one turn collapse to a single deferred blit (no drag storm)
        play._reblit_soon(); play._reblit_soon()
        assert calls == [], "the re-fit is deferred, not immediate"
        QApplication.processEvents()
        assert len(calls) == 1, "exactly one deferred re-fit ran"

        # hiding the toolbar fires no resizeEvent of its own, yet must still re-fit
        calls.clear()
        play._toggle_toolbar(False)
        QApplication.processEvents()
        assert calls, "hiding the toolbar must re-fit the paused frame"
    finally:
        play.machine = None
        w.close()


def test_load_tab_gauges_read_vram_and_frame_rate(app):
    """The Load tab reads exact VRAM budgets (sprites, tiles) and shows the frame-rate
    as the honest overload signal, greyed when nothing is moving. Stub machine, no core."""
    import ngpc_debug as dbg_mod

    # A machine whose OAM has 2 active sprites and whose tilemaps reference a few tiles.
    mem = bytearray(0x10000)
    # OAM at 0x8800: sprite 0 active (priority bits set), sprite 1 active (has position)
    mem[0x8800 + 1] = 0x08          # sprite 0: priority != 0 -> active
    mem[0x8804 + 2] = 40            # sprite 1: H position set -> active
    # SCR1 map at 0x9000: make one entry point at tile 5
    mem[0x9000] = 5

    class _FakeMem:
        def read(self, addr, n): return bytes(mem[addr:addr + n])

    class _FakePlay:
        def __init__(self, mem_): self.machine = mem_; self._perf = {}
        def perf(self): return self._perf
    play = _FakePlay(_FakeMem())

    dbg = dbg_mod.DebugWindow(None, cfg.make_settings())
    try:
        dbg._play = play

        play._perf = {"game_fps": 60.0}
        dbg._refresh_load()
        assert "2 / 64" in dbg._g_spr._caption, "two active sprites counted"
        assert not dbg._g_spr._neutral and dbg._g_spr._value == 2 / 64
        assert "/ 512 tiles" in dbg._g_tile._caption
        # keeping up at 60 -> health gauge full, not neutral
        assert dbg._g_cpu._value == 1.0 and not dbg._g_cpu._neutral

        # a still screen (no sprite movement) -> frame-rate gauge goes neutral/grey
        play._perf = {"game_fps": 0.0}
        dbg._refresh_load()
        assert dbg._g_cpu._neutral, "nothing moving -> can't tell the rate -> grey"
    finally:
        dbg.close()


def test_gauge_colour_runs_green_to_red():
    """Low severity is green-ish, high severity is red-ish (independent of Qt state)."""
    import ngpc_debug as dbg_mod
    lo = dbg_mod._Gauge._severity_colour(0.0)
    hi = dbg_mod._Gauge._severity_colour(1.0)
    assert lo.green() > lo.red(), "low = green"
    assert hi.red() > hi.green(), "high = red"


def test_fantrad_tabs_crack_pointers_compare(app, tmp_path):
    """The Crack / Pointers / Compare tabs, end to end against a stub machine: crack a
    table from a readable word, find a pointer to an address, and diff a second ROM.
    All ROM-agnostic; no emulator, no real cartridge."""
    import ngpc_debug as dbg_mod
    from core.texttable import parse_tbl

    letters = {c: 0xA4 + i for i, c in enumerate("abcdefghijklmnopqrstuvwxyz")}
    enc = lambda s: bytes(letters[c] for c in s)

    class _FakeMem:
        def __init__(self, blob): self._blob = blob
        def read(self, addr, n): return bytes(self._blob[addr:addr + n])

    class _FakePlay:
        def __init__(self, mem): self.machine = mem

    # A little cart image: a word to crack, and a 32-bit pointer to address 0x000040.
    blob = bytearray(0x800)
    blob[0x10:0x10 + 5] = enc("magic")
    blob[0x100:0x104] = (0x000040).to_bytes(4, "little")
    dbg = dbg_mod.DebugWindow(None, cfg.make_settings())
    try:
        dbg._play = _FakePlay(_FakeMem(blob))

        # -- Crack: one readable word -> a table with its letters
        dbg._crack_words.setPlainText("magic")
        dbg._crack_from.setText("000000"); dbg._crack_size.setValue(1)
        dbg._crack_run()
        out = dbg._crack_out.toPlainText()
        assert "A8=e" not in out                      # 'e' not in "magic"
        assert f"{letters['m']:02X}=m".upper() in out.upper()
        dbg._crack_use()                              # adopt it in the Text tab
        assert dbg._txt_table is not None and dbg._txt_table.encode("magic") == enc("magic")

        # -- Pointers: find the reference to 0x000040
        dbg._ptr_width.setCurrentIndex(0)             # 32-bit LE
        dbg._ptr_base.setText("000000")
        dbg._ptr_from.setText("000000"); dbg._ptr_size.setValue(1)
        dbg._ptr_target.setText("000040"); dbg._ptr_tol.setValue(0)
        dbg._ptr_find()
        assert "000100" in dbg._ptr_out.toPlainText()

        # -- Compare: a second ROM that differs in one spot. Use a full-alphabet table
        # so BOTH sides decode (the cracked one only knew "magic"'s letters).
        dbg._txt_table = parse_tbl("".join(f"{v:02X}={c}\n" for c, v in letters.items()))
        romb = bytearray(blob)
        romb[0x10:0x15] = enc("power")                # "magic" -> "power"
        romb_path = tmp_path / "romB.ngc"
        romb_path.write_bytes(bytes(romb))
        dbg._cmp_path = str(romb_path)
        dbg._cmp_from.setText("000000"); dbg._cmp_size.setValue(1)
        dbg._cmp_run()
        diff = dbg._cmp_out.toPlainText()
        assert "000010" in diff and "'magic'" in diff and "'power'" in diff
    finally:
        dbg.close()


def test_custom_cover_survives_a_cache_version_bump(app, tmp_path, monkeypatch):
    """The bug a user hit: a title screen they placed by hand came back as the
    default rendered one after every update. The cover cache prunes anything that
    is not the CURRENT render version, and it used to prune by "is a .png" — so an
    update that bumped THUMB_VERSION deleted the user's file too.

    Two guarantees checked here: a chosen cover in `covers/` is what the library
    shows and is never re-rendered over, and the prune only ever removes files the
    thumbnail worker itself wrote.
    """
    from PyQt6.QtGui import QImage

    monkeypatch.setattr(shell, "THUMB_DIR", tmp_path / "thumbnails")
    monkeypatch.setattr(shell, "COVER_DIR", tmp_path / "covers")
    shell.THUMB_DIR.mkdir()
    shell.COVER_DIR.mkdir()

    rom = tmp_path / "roms" / "My Game.ngc"
    rom.parent.mkdir()
    rom.write_bytes(b"\xff" * 64)

    # what the user supplies, and what an older render version left behind
    mine = QImage(4, 4, QImage.Format.Format_RGB32)
    mine.fill(0xFF00FF00)
    assert mine.save(str(shell.COVER_DIR / "My Game.png"), "PNG")
    stale_auto = shell.THUMB_DIR / f"My Game.{shell._path_tag(rom)}.v1.png"
    assert mine.save(str(stale_auto), "PNG")
    hand_placed = shell.THUMB_DIR / "My Game.png"      # the pre-`covers/` workflow
    assert mine.save(str(hand_placed), "PNG")

    assert shell.custom_cover(rom) == shell.COVER_DIR / "My Game.png"

    seen: list[tuple[str, QImage]] = []
    worker = shell.ThumbWorker([rom], None)
    worker.ready.connect(lambda r, i: seen.append((r, i)))
    worker.run()

    # the chosen cover was served -- no ROM was booted to render one over it
    assert [r for r, _ in seen] == [str(rom)]
    assert seen[0][1].size() == mine.size()
    assert not shell._cover_path(rom).exists(), "must not render over a chosen cover"
    # the prune took the worker's own stale file and NOTHING else
    assert not stale_auto.exists()
    assert hand_placed.exists(), "a file the worker did not write is not its to delete"
    assert (shell.COVER_DIR / "My Game.png").exists()


def test_custom_cover_is_scoped_when_two_roms_share_a_name(app, tmp_path, monkeypatch):
    """Every NgpCraft project builds a `main.ngc`. A cover chosen for one of them
    must not become the cover of all of them."""
    monkeypatch.setattr(shell, "COVER_DIR", tmp_path / "covers")
    shell.COVER_DIR.mkdir()
    a = tmp_path / "projA" / "main.ngc"
    b = tmp_path / "projB" / "main.ngc"
    for p in (a, b):
        p.parent.mkdir()
        p.write_bytes(b"\xff" * 64)

    (shell.COVER_DIR / f"main.{shell._path_tag(a)}.png").write_bytes(b"x")
    assert shell.custom_cover(a) is not None
    assert shell.custom_cover(b) is None, "a path-scoped cover must not leak to a twin"

    # the plain name is the drop-in / move-proof form: it answers for both
    (shell.COVER_DIR / "main.png").write_bytes(b"x")
    assert shell.custom_cover(b) == shell.COVER_DIR / "main.png"
    assert shell.custom_cover(a).name.startswith("main."), "the scoped one still wins"
    assert shell.custom_cover(a) != shell.COVER_DIR / "main.png"


def test_choose_and_reset_cover_round_trip(app, tmp_path, monkeypatch):
    """The menu path end to end: choosing an image writes it under `covers/` and
    paints it now; resetting drops it and falls back to the rendered cache."""
    from PyQt6.QtGui import QImage
    from PyQt6.QtWidgets import QFileDialog

    monkeypatch.setattr(shell, "THUMB_DIR", tmp_path / "thumbnails")
    monkeypatch.setattr(shell, "COVER_DIR", tmp_path / "covers")
    shell.THUMB_DIR.mkdir()

    rom = tmp_path / "roms" / "Game.ngc"
    rom.parent.mkdir()
    rom.write_bytes(b"\xff" * 64)

    auto = QImage(4, 4, QImage.Format.Format_RGB32); auto.fill(0xFF0000FF)
    assert auto.save(str(shell._cover_path(rom)), "PNG")     # pretend it was rendered
    picked = tmp_path / "title.png"
    mine = QImage(8, 8, QImage.Format.Format_RGB32); mine.fill(0xFF00FF00)
    assert mine.save(str(picked), "PNG")

    # No ROM folder configured -> the page builds without starting the thumbnail
    # worker (which would boot a core), which is all this test needs.
    page = shell.LibraryPage(cfg.make_settings(), shell.lib.Library(tmp_path / "library.json"))
    try:
        page._all_roms = [rom]
        monkeypatch.setattr(QFileDialog, "getOpenFileName",
                            staticmethod(lambda *a, **k: (str(picked), "")))
        page.set_cover(str(rom))
        assert (shell.COVER_DIR / "Game.png").is_file()
        assert page._images[str(rom)].size() == mine.size(), "the new cover is shown at once"

        page.reset_cover(str(rom))
        assert shell.custom_cover(rom) is None
        assert page._images[str(rom)].size() == auto.size(), "fell back to the rendered one"
    finally:
        page._stop_worker()
        page.deleteLater()


class _FlatMachine:
    """A core whose screen never shows anything -- one solid colour, forever. That
    is literally what a cartridge booted WITHOUT a BIOS puts on screen."""

    def __init__(self, colour: int = 0xFFF) -> None:
        self._fb = [colour] * (shell.SCREEN_W * shell.SCREEN_H)
        self.frames = 0

    def run_frames(self, n: int) -> None:
        self.frames += n

    def framebuffer(self) -> list[int]:
        return self._fb


class _FlatSession:
    def __init__(self, rom, bios_path=None, autosave=False, colour=0xFFF) -> None:
        self.machine = _FlatMachine(colour)

    def close(self) -> None:
        pass


@pytest.mark.parametrize("colour", [0xFFF, 0x000])
def test_a_blank_capture_never_becomes_a_cover(app, tmp_path, monkeypatch, colour):
    """The bug: every cover in the grid was a white box. A ROM that never reaches
    its title screen renders one flat colour, and that frame used to be saved as
    the cover -- CACHED, so it stayed a white box even after the cause was fixed.
    A capture with no picture in it is now no cover at all: the card keeps its
    placeholder and the next launch tries again."""
    monkeypatch.setattr(shell, "THUMB_DIR", tmp_path / "thumbnails")
    monkeypatch.setattr(shell, "COVER_DIR", tmp_path / "covers")
    shell.THUMB_DIR.mkdir()
    monkeypatch.setattr(shell, "NativeSession",
                        lambda *a, **k: _FlatSession(*a, colour=colour, **k))

    rom = tmp_path / "roms" / "Blank.ngc"
    rom.parent.mkdir()
    rom.write_bytes(b"\xff" * 64)

    worker = shell.ThumbWorker([rom], tmp_path / "bios.bin")
    monkeypatch.setattr(worker, "_bios", tmp_path / "bios.bin")   # pretend it exists
    seen: list[str] = []
    worker.ready.connect(lambda r, _i: seen.append(r))
    worker.run()

    assert seen == [], "a blank frame must not be shown as a cover"
    assert not shell._cover_path(rom).exists(), "...and must not be cached to disk"


def test_without_a_bios_no_rom_is_booted_for_a_cover(app, tmp_path, monkeypatch):
    """Covers are rendered by BOOTING the game, and no game boots without a BIOS.
    Rendering anyway spends a full core boot per ROM to produce a blank box, so
    with no BIOS the pass does not run at all -- but a cover the user CHOSE is
    still served, since that one costs no boot."""
    from PyQt6.QtGui import QImage

    monkeypatch.setattr(shell, "THUMB_DIR", tmp_path / "thumbnails")
    monkeypatch.setattr(shell, "COVER_DIR", tmp_path / "covers")
    shell.THUMB_DIR.mkdir()
    shell.COVER_DIR.mkdir()

    def _boom(*a, **k):
        raise AssertionError("booted a ROM with no BIOS to render a cover")
    monkeypatch.setattr(shell, "NativeSession", _boom)

    roms = tmp_path / "roms"
    roms.mkdir()
    plain, chosen = roms / "Plain.ngc", roms / "Chosen.ngc"
    for p in (plain, chosen):
        p.write_bytes(b"\xff" * 64)
    mine = QImage(8, 8, QImage.Format.Format_RGB32); mine.fill(0xFF00FF00)
    assert mine.save(str(shell.COVER_DIR / "Chosen.png"), "PNG")

    seen: list[str] = []
    worker = shell.ThumbWorker([plain, chosen], None)
    worker.ready.connect(lambda r, _i: seen.append(r))
    worker.run()

    assert seen == [str(chosen)]
    assert not shell._cover_path(plain).exists()


def _write_png(path) -> None:
    """A cover the worker will actually accept. A file that is not a readable image is
    re-rendered instead of reused -- which is right, and not what these tests measure."""
    from PyQt6.QtGui import QImage
    img = QImage(4, 4, QImage.Format.Format_RGB32)
    img.fill(0xFF3366CC)
    assert img.save(str(path), "PNG")


class _BootRecorder:
    """A session that records which ROM was booted and shows nothing.

    Blank on purpose: a capture with no picture is never cached (see
    `test_a_blank_capture_never_becomes_a_cover`), so the recorder cannot accidentally
    make the covers it is measuring."""

    booted: list = []

    def __init__(self, rom, bios_path=None, autosave=False, **kw):
        _BootRecorder.booted.append(pathlib.Path(rom))
        self.machine = _FlatMachine()

    def close(self) -> None:
        pass


def _boots_for(monkeypatch, roms, bios):
    """Run the cover worker over `roms` and return the ROMs it actually booted."""
    _BootRecorder.booted = []
    monkeypatch.setattr(shell, "NativeSession", _BootRecorder)
    worker = shell.ThumbWorker(list(roms), bios)
    monkeypatch.setattr(worker, "_bios", bios)       # pretend the image is loadable
    worker.run()
    return list(_BootRecorder.booted)


def test_a_bios_appearing_renders_only_the_missing_covers(app, tmp_path, monkeypatch):
    """Set the BIOS in Settings and come back: the covers that could not be rendered
    without one are rendered now, without a restart — and WITHOUT re-rendering the ones
    that already exist. Re-rendering a real library means booting every game in it."""
    monkeypatch.setattr(shell, "THUMB_DIR", tmp_path / "thumbnails")
    monkeypatch.setattr(shell, "COVER_DIR", tmp_path / "covers")
    monkeypatch.setattr(shell, "DEFAULT_BIOS", tmp_path / "no-such-bios.bin")
    # Also point the clean-room HLE fallback at nothing: otherwise `_resolve_bios`
    # returns hle_bios/bios_hle.bin and this "no BIOS at all" case never happens.
    monkeypatch.setattr(shell, "HLE_BIOS", tmp_path / "no-such-hle.bin")
    shell.THUMB_DIR.mkdir()
    roms = tmp_path / "roms"
    roms.mkdir()
    done, missing = roms / "Done.ngc", roms / "Missing.ngc"
    for rom in (done, missing):
        rom.write_bytes(b"\xff" * 64)
    _write_png(shell._cover_path(done))

    settings = cfg.make_settings()
    settings.setValue("paths/rom_folder", str(roms))
    asked: list[list] = []
    monkeypatch.setattr(shell.LibraryPage, "_start_worker",
                        lambda self, r: asked.append(list(r)))

    page = shell.LibraryPage(settings, shell.lib.Library(tmp_path / "library.json"))
    try:
        assert page._bios_hint.isVisible() or not page.isVisible(), "the why is on screen"

        bios = tmp_path / "bios.bin"
        bios.write_bytes(b"\x00" * 64)
        settings.setValue("paths/bios", str(bios))
        asked.clear()
        page.show()          # back from Settings
        assert asked, "a BIOS appearing must render what could not be rendered before"
        booted = _boots_for(monkeypatch, asked[-1], bios)
        assert booted == [missing], "only the game without a cover is booted"
    finally:
        page._stop_worker()
        page.deleteLater()


def test_a_new_rom_costs_one_boot_not_a_library(app, tmp_path, monkeypatch):
    """Drop a game in the folder and it gets a cover on its own — one boot, not all of
    them. This is the whole reason covers are cached per ROM path."""
    monkeypatch.setattr(shell, "THUMB_DIR", tmp_path / "thumbnails")
    monkeypatch.setattr(shell, "COVER_DIR", tmp_path / "covers")
    shell.THUMB_DIR.mkdir()
    roms = tmp_path / "roms"
    roms.mkdir()
    old = roms / "Old.ngc"
    old.write_bytes(b"\xff" * 64)
    _write_png(shell._cover_path(old))
    bios = tmp_path / "bios.bin"
    bios.write_bytes(b"\x00" * 64)

    settings = cfg.make_settings()
    settings.setValue("paths/rom_folder", str(roms))
    settings.setValue("paths/bios", str(bios))
    asked: list[list] = []
    monkeypatch.setattr(shell.LibraryPage, "_start_worker",
                        lambda self, r: asked.append(list(r)))
    page = shell.LibraryPage(settings, shell.lib.Library(tmp_path / "library.json"))
    try:
        fresh = roms / "New.ngc"
        fresh.write_bytes(b"\xff" * 64)
        page.reload()
        asked.clear()
        page._resume_missing_covers()
        assert asked, "the new game must be picked up"
        assert _boots_for(monkeypatch, asked[-1], bios) == [fresh]
    finally:
        page._stop_worker()
        page.deleteLater()


def test_regenerating_covers_drops_only_what_we_rendered(app, tmp_path, monkeypatch):
    """The button that DOES cost a full re-render. It asks first (booting a whole
    library is minutes), and a cover the user chose is not ours to delete."""
    from PyQt6.QtWidgets import QMessageBox

    monkeypatch.setattr(shell, "THUMB_DIR", tmp_path / "thumbnails")
    monkeypatch.setattr(shell, "COVER_DIR", tmp_path / "covers")
    shell.THUMB_DIR.mkdir(); shell.COVER_DIR.mkdir()
    roms = tmp_path / "roms"
    roms.mkdir()
    rom = roms / "Game.ngc"
    rom.write_bytes(b"\xff" * 64)
    auto = shell._cover_path(rom)
    _write_png(auto)
    chosen = shell.COVER_DIR / "Game.png"
    chosen.write_bytes(b"mine")

    settings = cfg.make_settings()
    settings.setValue("paths/rom_folder", str(roms))
    asked: list[list] = []
    monkeypatch.setattr(shell.LibraryPage, "_start_worker",
                        lambda self, r: asked.append(list(r)))
    page = shell.LibraryPage(settings, shell.lib.Library(tmp_path / "library.json"))
    try:
        monkeypatch.setattr(QMessageBox, "question",
                            staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
        page.regenerate_covers()
        assert auto.exists(), "answering No must change nothing"

        asked.clear()
        monkeypatch.setattr(QMessageBox, "question",
                            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
        page.regenerate_covers()
        assert not auto.exists(), "the rendered cover is dropped"
        assert chosen.exists(), "a cover the user chose is not ours to delete"
        assert asked and asked[-1] == [rom], "...and the library is rendered again"
    finally:
        page._stop_worker()
        page.deleteLater()


def test_link_tab_reads_the_cable_and_pokes_it(app):
    """The debugger's Link tab: it must land on its own refresh (the tab list and
    the refresh tuple are POSITIONAL -- a mismatch silently refreshes the wrong
    panel), read the channel out of a real core, and drive its three pokes:
    inject, impair, loopback."""
    import ngpc_debug as dbg_mod
    from core.native import NativeMachine

    class _FakePlay:
        """The slice of PlayPage the Link tab talks to."""

        def __init__(self, machine):
            self.machine = machine
            self.link_monitor = None
            self._net = None
            self.paused = False

        def set_link_monitor(self, mon): self.link_monitor = mon
        def link_mode(self): return "loopback" if self._net is not None else "none"
        def attach_net_link(self, net): self._net = net
        def detach_net_link(self): self._net = None

    machine = NativeMachine(b"\x00" * 0x10000)
    machine.serial_set_enabled(True)
    machine.serial_write_rx(b"\x41\x42")            # two bytes waiting, unread

    dbg = dbg_mod.DebugWindow(None, cfg.make_settings())
    try:
        dbg._play = _FakePlay(machine)
        idx = [dbg._tabs.tabText(i) for i in range(dbg._tabs.count())].index("Link")
        dbg._tabs.setCurrentIndex(idx)
        dbg.refresh()                               # dispatches through the tuple

        text = dbg._link_state.toPlainText()
        assert "cable" in text and "INTRX0" in text
        assert "queued for us       2" in text, "the core's counters are on screen"
        assert dbg._play.link_monitor is not None, "the tab tapped the cable"

        # inject: hex is parsed and queued for the console
        dbg._link_inject.setText("DE AD"); dbg._link_inject_mode.setCurrentText("Hex")
        dbg._link_send()
        assert dbg._link_mon.take_injected() == b"\xDE\xAD"
        dbg._link_inject.setText("zz"); dbg._link_send()
        assert "Not hex" in dbg._link_verdict.text(), "a typo says so, and sends nothing"

        # impair: the dialled-in numbers reach the monitor
        dbg._link_delay.setValue(3); dbg._link_drop.setValue(25)
        dbg._link_cut.setChecked(True)
        assert dbg._link_mon.impair.delay_frames == 3
        assert dbg._link_mon.impair.drop == pytest.approx(0.25)
        assert dbg._link_mon.impair.cut

        # loopback: attaching goes in as a network link, and off again
        dbg._link_loop.setCurrentIndex(1)
        assert type(dbg._play._net).__name__ == "LoopbackLink"
        dbg._link_loop.setCurrentIndex(0)
        assert dbg._play._net is None
    finally:
        dbg.close()


# ---- the BIOS ladder and the clean-room fallback -------------------------------
def _synthetic_rom() -> bytes:
    """A cartridge that boots and halts: enough to exercise the shell's wiring
    without needing a commercial ROM the repo does not ship."""
    rom = bytearray(b"\xFF" * 0x100000)
    rom[0:28] = b" LICENSED BY SNK CORPORATION"
    rom[0x1C:0x20] = (0x200040).to_bytes(4, "little")
    rom[0x23] = 0x10
    rom[0x40] = 0x05                      # halt
    return bytes(rom)


def test_the_bios_ladder_falls_back_to_the_clean_room_image(app, tmp_path, monkeypatch):
    """Configured path wins, then a real bios.bin, then the HLE image -- and the
    emulator must still have A BIOS at the bottom of the ladder, or nothing boots."""
    configured = tmp_path / "mine.bin"
    configured.write_bytes(b"\x00" * 0x10000)
    assert shell._resolve_bios(str(configured)) == configured

    assert shell._resolve_bios("") == shell.DEFAULT_BIOS or not shell.DEFAULT_BIOS.is_file()

    missing = tmp_path / "nope.bin"
    monkeypatch.setattr(shell, "DEFAULT_BIOS", missing)
    assert shell._resolve_bios("") == shell.HLE_BIOS
    assert shell._resolve_bios(str(missing)) == shell.HLE_BIOS, "a bad path must not win"


@pytest.mark.skipif(not shell.HLE_BIOS.is_file(), reason="the HLE image is not built")
def test_the_clean_room_image_never_takes_the_console_boot_path(app, tmp_path, monkeypatch):
    """The HLE image has no boot animation or setup UI -- that is the whole point of
    skipping them. Letting `real_bios` run against it would jump into code that is
    not there, so the shell has to notice which image it got."""
    monkeypatch.setattr(shell, "DEFAULT_BIOS", tmp_path / "absent.bin")
    rom = tmp_path / "synthetic.ngc"
    rom.write_bytes(_synthetic_rom())
    s = cfg.make_settings()
    s.setValue("paths/bios", "")
    s.setValue("general/real_bios", True)          # ...even when the user asked for it
    w = shell.Shell()
    try:
        w.play._frames_due = lambda: 2
        w.play.start(rom)
        assert w.play._bios_path() == shell.HLE_BIOS
        assert w.play._using_hle_bios() is True
        assert w.play._real_bios is False, "console boot needs a real BIOS"
    finally:
        w.play.stop()
        w.close()


@pytest.mark.skipif(not shell.HLE_BIOS.is_file(), reason="the HLE image is not built")
def test_a_savestate_round_trips_through_the_shell(app, tmp_path, monkeypatch):
    """Save, change the machine, load: the bytes must come back. The state is CPU +
    RAM, so this also covers the shell's own capture/apply pair, which nothing else
    exercised."""
    monkeypatch.setattr(shell, "STATE_DIR", tmp_path)
    rom = tmp_path / "synthetic.ngc"
    rom.write_bytes(_synthetic_rom())
    w = shell.Shell()
    try:
        w.play._frames_due = lambda: 2
        w.play.start(rom)
        for _ in range(4):
            w.play._tick()
        w.play.machine.write(0x005000, b"SAVEDSTATE")
        w.play.save_state(0)
        w.play.machine.write(0x005000, b"CLOBBERED!")
        assert w.play.machine.read(0x005000, 10) == b"CLOBBERED!"
        w.play.load_state(0)
        assert w.play.machine.read(0x005000, 10) == b"SAVEDSTATE"
    finally:
        w.play.stop()
        w.close()


def test_two_dumps_of_one_game_do_not_look_like_one_rom(app, tmp_path, monkeypatch):
    """The bug a user reported as "two thumbnails for one ROM": `_pretty` cuts the
    dump tags, so `Biomotor Unitron (USA)` and `Biomotor Unitron (USA, Europe)` --
    two real, different files -- both printed "Biomotor Unitron" under two covers.
    A title that is not unique now keeps the full file name."""
    monkeypatch.setattr(shell, "THUMB_DIR", tmp_path / "thumbnails")
    monkeypatch.setattr(shell, "COVER_DIR", tmp_path / "covers")
    shell.THUMB_DIR.mkdir()

    roms = tmp_path / "roms"
    roms.mkdir()
    twin_a = roms / "Biomotor Unitron (USA).ngc"
    twin_b = roms / "Biomotor Unitron (USA, Europe).ngc"
    alone = roms / "Faselei! (Europe).ngc"
    for p in (twin_a, twin_b, alone):
        p.write_bytes(b"\xff" * 64)

    titles = shell._titles([twin_a, twin_b, alone])
    assert titles[str(twin_a)] == "Biomotor Unitron (USA)"
    assert titles[str(twin_b)] == "Biomotor Unitron (USA, Europe)"
    assert titles[str(alone)] == "Faselei!", "a unique title still loses its tags"
    assert len(set(titles.values())) == 3, "no two cards may read the same"

    # ...and the cards actually carry them, in both views.
    page = shell.LibraryPage(cfg.make_settings(), shell.lib.Library(tmp_path / "library.json"))
    try:
        page._all_roms = sorted([twin_a, twin_b, alone])
        for view in (cfg.VIEW_GRID, cfg.VIEW_LIST):
            page._settings.setValue("library/view", view)
            page._arrange()
            shown = {lbl.text() for item in page._items.values()
                     for lbl in item.findChildren(QLabel)
                     if lbl.objectName() == "cardName"}
            assert shown == set(titles.values()), view
            assert page._items[str(twin_a)].toolTip() == str(twin_a), "hover says which file"
    finally:
        page._stop_worker()
        page.deleteLater()


def test_changing_the_bios_does_not_throw_the_covers_away(app, tmp_path, monkeypatch):
    """A cover is named after the ROM path alone, so switching BIOS leaves the pictures
    the previous one produced. That was briefly "fixed" by purging the cache on every
    BIOS change — and re-rendering a real library is minutes of booting every game, for
    a picture that differs by a shade. The cache stands; `Regenerate covers` is the
    button for the day you actually want them redone.

    The version prune stays: it is what keeps the folder from growing a copy per
    THUMB_VERSION bump, and it still only ever removes files the worker itself wrote."""
    monkeypatch.setattr(shell, "THUMB_DIR", tmp_path / "thumbnails")
    monkeypatch.setattr(shell, "COVER_DIR", tmp_path / "covers")
    shell.THUMB_DIR.mkdir()

    rom = tmp_path / "roms" / "Game.ngc"
    rom.parent.mkdir()
    rom.write_bytes(b"\xff" * 64)
    real, hle = tmp_path / "bios.bin", tmp_path / "bios_hle.bin"
    real.write_bytes(b"\x01" * 64)
    hle.write_bytes(b"\x02" * 64)

    cover = shell._cover_path(rom)
    cover.write_bytes(b"rendered once")
    stale = shell.THUMB_DIR / f"Game.{shell._path_tag(rom)}.v1.png"   # older version
    stale.write_bytes(b"old")
    hand_placed = shell.THUMB_DIR / "Game.png"                        # not ours
    hand_placed.write_bytes(b"x")

    shell.ThumbWorker([rom], hle)._prune()
    assert cover.exists()
    shell.ThumbWorker([rom], real)._prune()
    assert cover.exists(), "a different BIOS must not cost a whole library re-render"
    assert not stale.exists(), "...but an older render version is still cleaned up"
    assert hand_placed.exists(), "a file the worker did not write is not its to delete"


def _make_junction(link: pathlib.Path, target: pathlib.Path) -> bool:
    """A directory junction, the Windows way. Needs no privileges (a symlink does),
    which is exactly why sync clients and installers use them everywhere."""
    import subprocess
    if os.name != "nt":
        try:
            link.symlink_to(target, target_is_directory=True)
            return True
        except OSError:
            return False
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                       capture_output=True)
    return r.returncode == 0 and link.exists()


def test_the_scan_finds_roms_recursively_and_ignores_the_rest(tmp_path):
    root = tmp_path / "lib"
    (root / "sub" / "deeper").mkdir(parents=True)
    wanted = [root / "A.ngc", root / "sub" / "B.NGP", root / "sub" / "deeper" / "C.zip"]
    for p in wanted:
        p.write_bytes(b"\xff" * 16)
    for junk in ("notes.txt", "A.ngc.tmp", "cover.png"):
        (root / junk).write_bytes(b"x")

    assert shell.scan_roms(root) == sorted(wanted)


def test_one_rom_behind_a_junction_is_one_card(tmp_path):
    """THE bug: a user with a SINGLE ROM in his folder saw two cards. A junction --
    what every drive-sync client, and the legacy profile folders, are made of --
    makes one file reachable under two paths, and the old `rglob` scan walked
    straight through it. Worse, a junction pointing back up the tree made `rglob`
    recurse until Windows refused the path, and the `except OSError` around it threw
    the WHOLE scan away.

    `entry.is_symlink()` is False for a junction, so `os.walk(followlinks=False)`
    would not have helped either: the reparse tag is what says so.
    """
    root = tmp_path / "lib"
    (root / "inner").mkdir(parents=True)
    (root / "Game.ngc").write_bytes(b"\xff" * 16)
    if not _make_junction(root / "inner" / "link", root):
        pytest.skip("this platform would not create a directory junction")

    found = shell.scan_roms(root)
    assert found == [root / "Game.ngc"], "one file, one card -- by its real path"


def test_a_hardlinked_rom_is_not_a_second_game(tmp_path):
    """The identity check behind the junction fix, on its own: two directory entries
    for ONE file on disk are one game, whatever made them."""
    root = tmp_path / "lib"
    (root / "sub").mkdir(parents=True)
    real = root / "Game.ngc"
    real.write_bytes(b"\xff" * 16)
    try:
        os.link(real, root / "sub" / "Game.ngc")
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("no hardlink support here")

    assert shell.scan_roms(root) == [real], "the shallowest path is the one kept"


def test_the_scan_survives_a_folder_it_cannot_read(tmp_path, monkeypatch):
    """One unreadable folder must cost that folder, not the library."""
    root = tmp_path / "lib"
    (root / "locked").mkdir(parents=True)
    (root / "Game.ngc").write_bytes(b"\xff" * 16)

    real_scandir = os.scandir

    def picky(path):
        if str(path).endswith("locked"):
            raise PermissionError(str(path))
        return real_scandir(path)

    monkeypatch.setattr(shell.os, "scandir", picky)
    assert shell.scan_roms(root) == [root / "Game.ngc"]


def _fake_bios(kind: str) -> bytes:
    """A 64 KiB image carrying the byte pattern that identifies a BIOS: the
    `ld (0x6F91), #` its boot code runs, plus (colour only) a K2GE register access."""
    img = bytearray(b"\x00" * 0x10000)
    img[0x100:0x105] = b"\xF1\x91\x6F\x00" + (b"\x10" if kind == "colour" else b"\x00")
    if kind == "colour":
        img[0x200:0x203] = b"\xE2\x87\x00"
    return bytes(img)


def test_bios_kind_tells_the_two_consoles_apart(tmp_path):
    """Which machine an image is the BIOS of, read the way a cartridge reads it: the
    console-type byte the BIOS stamps at 0x6F91 (0x10 = NGPC, 0x00 = mono NGP), plus
    whether it ever addresses a K2GE colour register."""
    colour, mono, junk = (tmp_path / n for n in ("c.bin", "m.bin", "j.bin"))
    colour.write_bytes(_fake_bios("colour"))
    mono.write_bytes(_fake_bios("mono"))
    junk.write_bytes(b"\x00" * 0x10000)

    assert shell.bios_kind(colour) == shell.BIOS_COLOUR
    assert shell.bios_kind(mono) == shell.BIOS_MONO
    assert shell.bios_kind(junk) == shell.BIOS_UNKNOWN
    assert shell.bios_kind(tmp_path / "gone.bin") == shell.BIOS_UNKNOWN
    assert shell.bios_kind(None) == shell.BIOS_UNKNOWN


def test_the_bios_selector_picks_which_image_boots(app, tmp_path, monkeypatch):
    """Three slots, one selector: the user's colour dump, the user's mono dump, and
    the built-in HLE image. What the panel says is what boots."""
    monkeypatch.setattr(shell, "DEFAULT_BIOS", tmp_path / "no-bios-here.bin")
    monkeypatch.setattr(shell, "DEFAULT_BIOS_MONO", [tmp_path / "no-mono-here.bin"])
    colour, mono = tmp_path / "ngpc.bin", tmp_path / "ngp.bin"
    colour.write_bytes(_fake_bios("colour"))
    mono.write_bytes(_fake_bios("mono"))

    s = cfg.make_settings()
    s.setValue("paths/bios", str(colour))
    s.setValue("paths/bios_mono", str(mono))

    s.setValue("bios/active", cfg.BIOS_USE_COLOUR)
    assert shell.resolve_selected_bios(s) == (colour, cfg.BIOS_USE_COLOUR)
    s.setValue("bios/active", cfg.BIOS_USE_MONO)
    assert shell.resolve_selected_bios(s) == (mono, cfg.BIOS_USE_MONO)
    s.setValue("bios/active", cfg.BIOS_USE_HLE)
    assert shell.resolve_selected_bios(s) == (shell.HLE_BIOS, cfg.BIOS_USE_HLE)

    # An empty slot boots SOMETHING rather than nothing -- and reports the slot that
    # actually answered, so the panel can say the console asked for is not the one running.
    s.setValue("bios/active", cfg.BIOS_USE_MONO)
    s.setValue("paths/bios_mono", "")
    assert shell.resolve_selected_bios(s) == (colour, cfg.BIOS_USE_COLOUR)

    # ...and with no BIOS at all in reach, the ladder still ends on the HLE image.
    s.setValue("paths/bios", "")
    assert shell.resolve_selected_bios(s) == (shell.HLE_BIOS, cfg.BIOS_USE_HLE)


def test_a_colour_cartridge_on_the_mono_console_is_told_it_is_mono(app, tmp_path):
    """THE bug behind the request. 0x6F91 is how a cartridge knows which machine it is
    in. It used to be seeded from the CARTRIDGE HEADER, which for a colour game says
    0x10 -- so a colour cartridge in our "mono NGP" was still told NGPC and behaved
    exactly as it does on one. The console owns that byte, not the cartridge: the mono
    NGP's own BIOS writes 0x00 there (disassembled: `ld (0x6F91), 0x00`), whatever is
    in the slot."""
    from core.native_session import NativeSession

    rom = tmp_path / "colour.ngc"
    rom.write_bytes(_synthetic_rom())          # header 0x23 = 0x10: a COLOUR cartridge
    for k1ge, expect in ((False, 0x10), (True, 0x00)):
        s = NativeSession(rom, autosave=False, save_to_rom=False, k1ge_console=k1ge)
        try:
            assert s.machine.read(0x006F91, 1)[0] == expect, f"k1ge_console={k1ge}"
        finally:
            s.close()


def test_the_console_follows_the_bios_that_boots(app, tmp_path, monkeypatch):
    """What the user asked for in one line: a cartridge must detect a K1GE console when
    the selected BIOS is a K1GE one. It is the same question twice -- a game reads 0x6F91,
    and 0x6F91 is what the BIOS stamps -- so the machine is derived from the image that
    will actually boot, not from the slot it happens to sit in."""
    monkeypatch.setattr(shell, "DEFAULT_BIOS", tmp_path / "none.bin")
    monkeypatch.setattr(shell, "DEFAULT_BIOS_MONO", [tmp_path / "none-mono.bin"])
    colour, mono = tmp_path / "ngpc.bin", tmp_path / "ngp.bin"
    colour.write_bytes(_fake_bios("colour"))
    mono.write_bytes(_fake_bios("mono"))

    s = cfg.make_settings()
    s.setValue("paths/bios", str(colour))
    s.setValue("paths/bios_mono", str(mono))
    s.setValue("gfx/mono_mode", cfg.MONO_K2GE)      # the Console setting stays on NGPC

    s.setValue("bios/active", cfg.BIOS_USE_COLOUR)
    assert shell.console_is_mono(s) is False
    s.setValue("bios/active", cfg.BIOS_USE_MONO)
    assert shell.console_is_mono(s) is True, "selecting the K1GE BIOS selects the K1GE"

    # The IMAGE decides, not the slot: a mono dump filed under the colour slot still
    # boots a mono console, and a colour dump in the mono slot does not fake one.
    s.setValue("bios/active", cfg.BIOS_USE_COLOUR)
    s.setValue("paths/bios", str(mono))
    assert shell.console_is_mono(s) is True
    s.setValue("bios/active", cfg.BIOS_USE_MONO)
    s.setValue("paths/bios_mono", str(colour))
    assert shell.console_is_mono(s) is False

    # The HLE image is neither console's BIOS, so the explicit setting is what is left.
    s.setValue("bios/active", cfg.BIOS_USE_HLE)
    assert shell.console_is_mono(s) is False
    s.setValue("gfx/mono_mode", cfg.MONO_K1GE)
    assert shell.console_is_mono(s) is True, "the Console setting still forces it"


def test_the_mono_console_renders_in_greys_not_in_two_tones(app, tmp_path):
    """The K1GE render path. On that silicon the LUT value IS the shade -- there is no
    12-bit palette (K1GE Tech Ref §3-7) -- so the picture must not be resolved through
    colour RAM the machine does not have. Reading it there is what left the NGP boot
    logo sitting on a wallpaper of SNK tiles, in two tones."""
    from core.native_session import NativeSession

    rom = tmp_path / "colour.ngc"
    rom.write_bytes(_synthetic_rom())
    ramp = b"\xff\x0f\xdd\x0d\xbb\x0b\x99\x09\x66\x06\x44\x04\x22\x02\x00\x00"
    with NativeSession(rom, autosave=False, save_to_rom=False, k1ge_console=True) as mono:
        assert mono.machine.read(0x008380, 16) == ramp, "eight greys, stamped by the console"
        # ...and it is the console's own, so it is there with no BIOS supplied at all --
        # which is the case that used to render the whole screen in two tones.
        assert mono.machine.read(0x0083C0, 16) == ramp, "same ramp on every plane"


def _plugin_dir(tmp_path):
    """A folder of filter plugins, as a user would have it: one good, three broken."""
    d = tmp_path / "myfilters"
    d.mkdir()
    (d / "double.py").write_text(
        'import numpy as np\n'
        'NAME = "Double"\n'
        'SCALE = 2\n'
        'def apply(rgb):\n'
        '    return np.repeat(np.repeat(rgb, 2, axis=0), 2, axis=1)\n', encoding="utf-8")
    (d / "syntax.py").write_text("def broken(:\n", encoding="utf-8")
    (d / "shape.py").write_text(
        'NAME = "Bad shape"\nSCALE = 2\ndef apply(rgb):\n    return rgb\n', encoding="utf-8")
    (d / "helper.py").write_text("# not a filter at all\nVALUE = 1\n", encoding="utf-8")
    return d


def test_filter_plugins_are_loaded_from_a_folder_the_user_owns(tmp_path):
    """The good scalers (HQx, 2xSaI, xBRZ) are copyleft implementations an MIT emulator
    cannot redistribute, so it loads them instead of shipping them — from a folder the
    user points at, with no directory created on their disk for it."""
    import ngpc_filters

    ok, bad = ngpc_filters.discover(_plugin_dir(tmp_path))
    assert [p.name for p in ok] == ["Double"]
    assert ok[0].scale == 2 and ok[0].ident == "plugin:double"

    # Every rejected file is reported WITH ITS REASON: a plugin that silently fails to
    # appear comes back to us as "your filter list is broken".
    reasons = {b.source.name: b.reason for b in bad}
    assert set(reasons) == {"syntax.py", "shape.py", "helper.py"}
    assert "SyntaxError" in reasons["syntax.py"]
    assert "expected" in reasons["shape.py"]        # smoke-tested, not just declared
    assert "NAME" in reasons["helper.py"]

    assert ngpc_filters.discover("") == ([], []), "no folder set: the feature is simply off"
    assert ngpc_filters.discover(tmp_path / "nope") == ([], [])


def test_a_filter_that_breaks_costs_the_filter_not_the_frame(tmp_path):
    """A plugin verified at load can still fail on a real picture. That must never take
    the game down with it: the frame comes back unfiltered and the plugin is dropped."""
    import ngpc_filters
    import numpy as np

    d = _plugin_dir(tmp_path)
    reg = ngpc_filters.FilterRegistry()
    reg.sync(str(d))
    assert reg.get("plugin:double") is not None

    def explode(rgb):
        raise RuntimeError("boom")
    object.__setattr__(reg.plugins[0], "apply", explode)

    assert reg.run("plugin:double", np.zeros((152, 160, 3), np.uint8)) is None
    assert "boom" in reg.disabled_reason("plugin:double")
    assert reg.get("plugin:double") is None, "...and it is not tried again this session"


def test_a_plugin_never_returns_more_picture_than_was_asked_for(tmp_path):
    """The plugin's magnification counts toward the requested scale, and a factor that
    does not divide it settles for the largest multiple that fits — the window fit
    covers the rest, as it already does for any non-integer window."""
    import ngpc_filters
    import ngpc_video as vid

    reg = ngpc_filters.FilterRegistry()
    reg.sync(str(_plugin_dir(tmp_path)))
    plug = reg.plugins[0]
    scaler = lambda rgb: (reg.run(plug.ident, rgb), plug.scale)  # noqa: E731
    fb = [0] * (vid.SCREEN_W * vid.SCREEN_H)

    for k in (1, 2, 4, 6):
        assert (vid.render_array(fb, k, vid.FILTER_LCD_GRID, vid.COLOR_RAW, scaler).shape
                == vid.render_array(fb, k, vid.FILTER_NONE, vid.COLOR_RAW).shape), k
    at3 = vid.render_array(fb, 3, vid.FILTER_NONE, vid.COLOR_RAW, scaler).shape
    assert at3 == (vid.SCREEN_H * 2, vid.SCREEN_W * 2, 3), "x2 into x3 stays at x2"
