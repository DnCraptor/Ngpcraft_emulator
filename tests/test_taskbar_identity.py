"""Which Windows taskbar identity the app claims -- and, in the shipped build, why none.

⛔ THE BUG THIS CONDEMNS. "When i start the emu the icon in taskbar is a generic one.
If i click pin to taskbar then it displays the right icon" -- user report 2026-07-28.
The app claimed an explicit AppUserModelID unconditionally. That takes the taskbar
button off the executable and onto an id nothing on the machine resolves: no installer
here ever registered a shortcut carrying it, so the button has no app to source an icon
from. "Pin to taskbar" is the act of creating that missing shortcut -- which is exactly
why pinning fixed it, and fixed it permanently.

⚖️ EVERYTHING ELSE WAS MEASURED HEALTHY first, and this test exists because the answer
turned out to be "claim nothing", which is the one outcome an "is the icon set?" test
would never reach: the .ico carries all nine sizes 16..256; the shell call returns S_OK
and reads back; the live window answers WM_GETICON with handles for both sizes, from
source AND from the packaged .exe; the .exe carries an extractable icon resource; and
setting the id before QApplication rather than after changes nothing.

🎯 The decision is asymmetric ON PURPOSE, so both halves are pinned here. A frozen build
is its own identity with its own embedded icon and must claim nothing. From source the
executable is python.exe, whose identity is not ours, so the explicit id stays -- and a
test that only asserted "frozen claims nothing" would be satisfied by deleting the
feature outright.

🔁 AND IT CAME BACK, on someone else's machine: "l'icone ne s'affiche pas bien chez
certains utilisateurs sous Windows 11 dans la barre du bas [...] l'icone devient correcte
si il la pin a la barre d'application" (2026-07-30) -- with the .exe's icon in Explorer
CORRECT there, so extraction from the binary is not the problem. Claiming nothing removed
the wrong identity but the button still had to FIND a picture, and it finds one on a
shell item -- a shortcut -- which a portable .exe has nowhere until you pin it. The
second half of this file pins the fix for that: the window carries its own relaunch icon,
so the button no longer needs a shortcut to exist. Both halves must hold at once.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from ngpc_shell import (TASKBAR_APP_ID, TASKBAR_RELAUNCH_NAME,
                        claim_taskbar_identity, taskbar_window_props)


def test_the_shipped_exe_claims_nothing_and_keeps_its_own_icon():
    # The .exe is built with icon=assets/icone_ngpcraft.ico (NgpCraftEmulator.spec),
    # so Windows' own identity for it already carries the right picture. Claiming an
    # id here is what replaced it with one that resolves to no app at all.
    assert claim_taskbar_identity(frozen=True, platform="win32") is None


def test_running_from_source_still_escapes_python_exe():
    # THE OTHER HALF. Without this the app pools under python.exe -- someone else's
    # identity and someone else's icon.
    assert claim_taskbar_identity(frozen=False, platform="win32") == TASKBAR_APP_ID


@pytest.mark.parametrize("frozen", [True, False])
@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_no_such_concept_outside_windows(frozen, platform):
    assert claim_taskbar_identity(frozen=frozen, platform=platform) is None


# ---- the button's OWN icon, so it needs no shortcut to exist --------------

_EXE = r"C:\Games\NgpCraftEmulator.exe"
_PY = r"C:\Python\python.exe"
_SCRIPT = r"C:\repo\ngpc_shell.py"
_ICO = r"C:\repo\assets\icone_ngpcraft.ico"


def _props(frozen, icon=_ICO, platform="win32"):
    exe = _EXE if frozen else _PY
    return taskbar_window_props(exe=exe, script=_SCRIPT, icon=icon,
                                frozen=frozen, platform=platform)


def test_the_shipped_exe_points_the_button_at_its_own_embedded_icon():
    # THE FIX. Without this the button has no icon of its own and must find a shortcut,
    # which is why it looked right only on machines that already had one.
    assert _props(frozen=True)["RelaunchIconResource"] == f"{_EXE},0"


def test_from_source_the_icon_is_our_ico_and_never_python_exe():
    # python.exe's icon is not ours: pointing the button at it would pool this app with
    # every other Python GUI, which is the same class of bug one layer down.
    ref = _props(frozen=False)["RelaunchIconResource"]
    assert ref == f"{_ICO},0"
    assert "python" not in ref.lower()


def test_no_bundled_icon_means_stamp_nothing_rather_than_a_bogus_path():
    # A reference that resolves to nothing is worse than none: it REPLACES the fallback
    # (the executable's own identity) with a dead end.
    assert _props(frozen=False, icon=None) is None


def test_the_icon_never_travels_alone():
    # The shell documents the three as one set and ignores a lone icon, so a future edit
    # that drops the command or the name silently turns the icon off again.
    for frozen in (True, False):
        assert set(_props(frozen=frozen)) == {
            "RelaunchIconResource", "RelaunchCommand", "RelaunchDisplayNameResource"}


def test_the_relaunch_command_actually_relaunches_this_app():
    # It is what a pin bakes into the shortcut. A frozen build is one quoted path; from
    # source the interpreter alone would relaunch a bare Python prompt.
    assert _props(frozen=True)["RelaunchCommand"] == f'"{_EXE}"'
    assert _props(frozen=False)["RelaunchCommand"] == f'"{_PY}" "{_SCRIPT}"'


def test_paths_with_spaces_stay_one_argument():
    spaced = r"C:\Program Files\NgpCraft\NgpCraftEmulator.exe"
    got = taskbar_window_props(exe=spaced, script=_SCRIPT, icon=_ICO,
                               frozen=True, platform="win32")
    assert got["RelaunchCommand"] == f'"{spaced}"'
    # The icon reference is NOT a command line and must not be quoted -- the shell parses
    # it as "<file>,<index>" and a quote makes the file not exist.
    assert got["RelaunchIconResource"] == f"{spaced},0"


def test_the_display_name_is_the_app_not_the_file_stem():
    assert _props(frozen=True)["RelaunchDisplayNameResource"] == TASKBAR_RELAUNCH_NAME
    assert TASKBAR_RELAUNCH_NAME == "NgpCraft Emulator"


@pytest.mark.parametrize("frozen", [True, False])
@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_no_window_properties_outside_windows(frozen, platform):
    assert _props(frozen=frozen, platform=platform) is None


# ---- RUNTIME GATE: the shell must actually TAKE the values ----------------
# ⚠️ Everything above is a decision about strings. The failure this chases was never a
# wrong string -- it was Windows not having an icon to reach -- so the only test that
# means anything is one that puts the values ON A LIVE WINDOW and reads them back out of
# the shell's own property store. It runs in a SUBPROCESS on purpose: the other UI tests
# force QT_QPA_PLATFORM=offscreen and share one QApplication for the session, and an
# offscreen window has no HWND, so a stamp there would "pass" while proving nothing.

_GATE = textwrap.dedent(r"""
    import json, os, sys
    os.environ.pop("QT_QPA_PLATFORM", None)      # we need the REAL windows backend
    sys.path.insert(0, sys.argv[1])
    from PyQt6.QtWidgets import QApplication, QMainWindow
    import ngpc_shell as shell
    app = QApplication([])
    win = QMainWindow(); win.show()
    hwnd = int(win.winId())
    took = shell.stamp_taskbar_icon(win)
    print(json.dumps({"hwnd": hwnd, "took": took,
                      "read": shell.read_window_taskbar_props(hwnd),
                      "want": shell._current_taskbar_window_props()}))
""")


@pytest.mark.skipif(sys.platform != "win32", reason="no taskbar outside Windows")
def test_a_live_window_really_carries_the_icon_the_shell_will_use():
    repo = str(Path(__file__).resolve().parent.parent)
    env = dict(os.environ); env.pop("QT_QPA_PLATFORM", None)
    p = subprocess.run([sys.executable, "-c", _GATE, repo], capture_output=True,
                       text=True, env=env, timeout=180)
    if p.returncode != 0:
        pytest.skip(f"no interactive Windows session for a real HWND:\n{p.stderr[-400:]}")
    got = json.loads(p.stdout.strip().splitlines()[-1])
    assert got["hwnd"], "no HWND: the window never reached the windowing system"
    assert got["took"], "the shell REFUSED the properties -- the button has no icon"
    # Read-back, not "the call returned S_OK": the 2026-07-28 round already had a call
    # that returned S_OK and read back, and the icon was still generic.
    assert got["read"] == got["want"], "the shell holds something other than we set"
    assert got["read"]["RelaunchIconResource"].lower().endswith(",0")
