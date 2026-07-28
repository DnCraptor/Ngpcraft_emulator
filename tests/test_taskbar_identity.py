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
"""

from __future__ import annotations

import pytest

from ngpc_shell import TASKBAR_APP_ID, claim_taskbar_identity


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
