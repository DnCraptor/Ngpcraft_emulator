"""Keep the test suite out of the user's console.

⛔ THE DAMAGE THIS PREVENTS, and it is not hypothetical -- it happened while writing
the tests next to this file. `saves/system.ram` is the COIN CELL: the language, the
colour theme and the clock the player set on the BIOS's own setup screen. Any test that
boots the real BIOS and then stops it goes through `commit_system_ram`, which writes
that file for real. A test only ticks a handful of frames, so what it commits is a
half-initialised BIOS page -- and the next hand-off boot restores that page as its
baseline. MEASURED: with the file a test had just written, `test_link_play` came up with
an entirely black framebuffer; move the file aside and the same tests pass.

So the suite writes a throwaway coin cell instead. Both names are patched because they
are two references to the same path: `core.native_session` owns it, and `ngpc_shell`
imported it under its own name at module load, so patching one leaves the other pointing
at the player's file.

Autouse and suite-wide on purpose. This is not a property of the tests that happen to
boot a BIOS today -- it is a property of booting one at all, and the next test to do it
should not have to know.
"""

from __future__ import annotations

import sys

import pytest

# Qt-free on purpose: this file is imported for EVERY test, including the ones that run
# with no PyQt6 installed (the UI tests skip themselves with `importorskip`). Importing
# the shell here would turn a missing PyQt6 from "some tests skip" into "collection
# fails", on every platform -- and the Linux CI runner is the one where Qt's shared
# libraries are least likely to all be present. `core.native_session` has no Qt in it.
import core.native_session as ns


@pytest.fixture(autouse=True)
def _sandbox_the_coin_cell(tmp_path, monkeypatch):
    cell = tmp_path / "system.ram"
    clock = tmp_path / "system.rtc"
    monkeypatch.setattr(ns, "SYSTEM_RAM_PATH", cell, raising=False)
    monkeypatch.setattr(ns, "SYSTEM_RTC_PATH", clock, raising=False)
    # The shell took its OWN references at import time (`SYSTEM_RAM_PATH as
    # _SYSTEM_RAM`), and `start_bios` / `stop` use THOSE -- patching only the module
    # above would leave the console-boot path writing the player's file. Looked up in
    # `sys.modules` rather than imported: a test that uses the shell has already
    # imported it (module level, during collection), and one that has not needs nothing
    # from it.
    shell = sys.modules.get("ngpc_shell")
    if shell is not None:
        monkeypatch.setattr(shell, "_SYSTEM_RAM", cell, raising=False)
        monkeypatch.setattr(shell, "_SYSTEM_RTC", clock, raising=False)
    yield
