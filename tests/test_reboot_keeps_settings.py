"""A power cycle is not a factory reset -- the console keeps its own settings.

⛔ THE BUG THIS ENDS, reported by a player: "in local two-player, one window respects
the language I chose and the other shows the game in Japanese."

`NativeSession.__init__` lays the saved coin-cell page (0x6C00-0x6FFF: the language,
the date, the BIOS's own settings) back over the hand-off reset, precisely so a
dual-language SNK cartridge reads the language the console is set to. `reboot()` ran
the same hand-off reset and did NOT put the page back.

It stayed invisible while the only way to reboot was the reset button. Then the link
button started power-cycling player 1 -- so a game that probes the cable during its own
boot can find its peer -- and two-player play began showing one console configured and
the other not: player 2 is a FRESH session, player 1 a REBOOTED one.

MEASURED before the fix: **757 of the 1024 bytes** of that page differed between a
fresh console and a rebooted one. After: zero.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core import native

REPO = Path(__file__).resolve().parent.parent
BIOS = REPO / "bios.bin"
ROM = REPO / "tests" / "roms" / "link_probe.ngc"

SETTINGS_PAGE = (0x6C00, 0x400)          # the console's own page, per NativeSession

pytestmark = pytest.mark.skipif(
    not (BIOS.exists() and ROM.exists()),
    reason="needs the retail bios.bin (gitignored) and the probe ROM")


@pytest.fixture
def configured_cell(tmp_path, monkeypatch):
    """A console somebody has already set up: a coin cell with content in it.

    The value does not matter -- what matters is that it is NOT what a blank power-on
    leaves, so "the page was restored" and "the page happened to look right" cannot be
    confused.
    """
    import core.native_session as ns

    cell = tmp_path / "system.ram"
    cell.write_bytes(bytes([0x5A]) * (0x7000 - native.RAM_START))
    monkeypatch.setattr(ns, "SYSTEM_RAM_PATH", cell)
    return cell


def _session(**kw):
    from core.native_session import NativeSession

    return NativeSession(ROM, bios_path=BIOS, autosave=False, save_to_rom=False,
                         sidecar=False, **kw)


def _page(s) -> bytes:
    return s.machine.read(*SETTINGS_PAGE)


def test_a_power_cycle_keeps_the_consoles_own_settings(configured_cell):
    """A rebooted console must hold what a freshly started one holds. Compared against
    a FRESH session rather than against a constant, so the test says "these two consoles
    are the same machine" -- which is the property two-player play actually needs."""
    fresh, rebooted = _session(), _session()
    try:
        rebooted.reboot()
        for _ in range(60):              # let both settle past the hand-off
            fresh.run_frames(1)
            rebooted.run_frames(1)
        a, b = _page(fresh), _page(rebooted)
        differing = sum(1 for x, y in zip(a, b) if x != y)
        assert a == b, (
            f"a power cycle forgot the console's settings: {differing} of {len(a)} "
            "bytes of the BIOS page differ from a fresh console's")
    finally:
        fresh.close(); rebooted.close()


def test_a_power_cycle_keeps_the_cartridge_save(configured_cell):
    """The control group for the test above: `reboot` already went to some trouble to
    carry the flash across, so a change to it must not quietly undo that."""
    s = _session()
    try:
        before = s.machine.read(0x200000, 0x100)
        s.reboot()
        assert s.machine.read(0x200000, 0x100) == before
    finally:
        s.close()
