"""Booting the BIOS with no cartridge must say WHICH CONSOLE it is booting.

⛔ THE BUG THIS CONDEMNS. "If i select a monochrome bios and then boot bios i have a
black screen" -- user report 2026-07-28, who also asked the fair question "je suis pas
sur que notre emulateur puisse booter le bios k1ge?". It can, and it always could: with
a mono NGP dump the BIOS runs perfectly (measured -- same PC, same 900 frames, its own
code all the way). `Player.start_bios` simply built its machine by hand and never passed
the console type, which the cartridge path has always passed through NativeSession
(`k1ge_console=self._mono_console()`).

A mono BIOS on K2GE silicon writes no colour palettes -- it has no reason to know they
exist -- so every pixel resolves through empty palette RAM. MEASURED on the real dump,
no cartridge, 900 frames: ONE distinct colour, 0x0000, on every frame. Set the flag and
the same dump comes up in seven greys on its own SELECT ONE / POCKET MENU screens.

⚖️ NOT A SAVE CONFLICT -- and that was worth measuring rather than assuming, because it
was the reported suspicion. Both consoles share one saves/system.ram, and they accept
each other's cell in BOTH directions: a cell written by the colour NGPC boots the mono
NGP straight to its Pocket Menu, and a cell the mono BIOS wrote through its own setup
boots the NGPC configured. Stamping the machine-type pair (0x6F91/0x6F92) either way
does not invalidate it either. Only a wiped settings page brings the setup screen back.
"""

from __future__ import annotations

import os
import pathlib

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

import ngpc_settings as cfg  # noqa: E402
import ngpc_shell as shell  # noqa: E402

# The mono dump is not in the repo and never will be. Look where the app itself looks,
# plus beside the checkout -- and skip, loudly, rather than pretend to have tested this.
_MONO_CANDIDATES = list(shell.DEFAULT_BIOS_MONO) + [
    pathlib.Path(__file__).resolve().parents[2] / n
    for n in ("ngp_bios.ngp", "ngp_bios.bin", "bios_ngp.bin", "ngpbios.bin")
]
_MONO = next((p for p in _MONO_CANDIDATES
              if p.is_file() and shell.bios_kind(p) == shell.BIOS_MONO), None)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _clean_settings():
    # Safe: the root conftest points QSettings at a temp .ini before collection.
    s = cfg.make_settings()
    s.clear()
    yield
    s.clear()


def _select_mono(settings) -> None:
    settings.setValue("bios/active", cfg.BIOS_USE_MONO)
    settings.setValue("paths/bios_mono", str(_MONO))


class _Spy(shell.native.NativeMachine):
    """Records the console type the shell hands the machine. Nothing else changes."""
    told: list = []

    def set_k1ge_console(self, on: bool) -> None:      # type: ignore[override]
        _Spy.told.append(bool(on))
        super().set_k1ge_console(on)


@pytest.mark.skipif(_MONO is None, reason="needs a real monochrome NGP BIOS dump")
@pytest.mark.parametrize("mono", [True, False])
def test_start_bios_tells_the_machine_which_console_it_is(app, monkeypatch, mono):
    # BOTH directions on purpose. A test that only pinned the mono case would be
    # satisfied by hard-wiring k1ge on, which would break every colour BIOS boot.
    w = shell.Shell()
    try:
        _select_mono(w.play._settings)
        monkeypatch.setattr(shell.native, "NativeMachine", _Spy)
        monkeypatch.setattr(type(w.play), "_mono_console", lambda self: mono)
        _Spy.told = []
        w.play.start_bios()
        assert _Spy.told == [mono], (
            "start_bios built its machine without saying which console it is; the "
            f"cartridge path passes _mono_console() and this one must too (got {_Spy.told})"
        )
    finally:
        w.play.stop()
        w.close()


@pytest.mark.skipif(_MONO is None, reason="needs a real monochrome NGP BIOS dump")
def test_the_mono_bios_actually_draws_its_screen(app):
    """The symptom itself: a picture, not a black rectangle."""
    w = shell.Shell()
    try:
        _select_mono(w.play._settings)
        assert w.play._mono_console() is True, "the mono dump must identify as an NGP"
        w.play._frames_due = lambda: 6           # bypass the wall-clock pacer
        w.play.start_bios()
        for _ in range(120):
            w.play._tick()
        shades = set(w.play.machine.framebuffer())
        assert len(shades) > 3, (
            f"the monochrome BIOS drew {len(shades)} colour(s) -- {sorted(shades)!r}. "
            "One means the whole screen is a single flat value: the console type never "
            "reached the machine and the greys resolve through palettes nobody filled."
        )
    finally:
        w.play.stop()
        w.close()
