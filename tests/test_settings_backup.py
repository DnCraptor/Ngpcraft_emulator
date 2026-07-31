# -*- coding: utf-8 -*-
"""Settings survive the store being wiped.

Settings live in the Windows registry by default: no undo, no history, and
nothing beside it saying what used to be there. One stray `clear()` -- a script
run outside this suite's redirect, a botched uninstall, a registry cleaner --
takes the BIOS path, the ROM folder, every binding and every preference, silently.

These tests hold the two rules that make the backup trustworthy rather than a
second way to lose the data.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSettings  # noqa: E402

import ngpc_settings as cfg  # noqa: E402


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A real QSettings backed by a temp INI, with its backups alongside."""
    ini = tmp_path / "settings.ini"
    monkeypatch.setenv(cfg.SETTINGS_FILE_ENV, str(ini))
    s = QSettings(str(ini), QSettings.Format.IniFormat)
    yield s
    s.sync()


def _fill(s):
    s.setValue("paths/bios", "C:/bios.bin")
    s.setValue("paths/rom_folder", "D:/roms")
    s.setValue("input/A", 88)
    s.setValue("win/geometry", "ignored")
    s.sync()


def test_a_wiped_store_is_put_back_from_the_backup(store):
    _fill(store)
    assert cfg.backup_settings(store)

    store.clear(); store.sync()                     # the accident
    assert cfg.bios_path(store) == ""

    recovered = cfg.protect_settings(store)
    assert recovered == 3
    assert cfg.bios_path(store) == "C:/bios.bin"
    assert cfg.rom_folder(store) == "D:/roms"


def test_an_empty_store_is_never_saved_over_a_good_backup(store):
    """Backing up the damage is the classic way a backup destroys what it was
    meant to protect: launch once after a wipe and the copy is gone too."""
    _fill(store)
    cfg.backup_settings(store)

    store.clear(); store.sync()
    assert not cfg.backup_settings(store), "an empty store is not worth saving"

    assert cfg.restore_settings(store) == 3, "the good copy is still there"


def test_window_geometry_alone_does_not_count_as_settings(store):
    """It is rewritten on every launch, including the first one after a wipe. If it
    counted, the store would never look empty and nothing would ever be restored."""
    store.setValue("win/geometry", "abc")
    store.setValue("win/rail_collapsed", False)
    store.sync()
    assert cfg._real_keys(store) == []
    assert not cfg.backup_settings(store)


def test_the_previous_generation_survives_one_bad_cycle(store, tmp_path):
    _fill(store)
    cfg.backup_settings(store)

    store.setValue("paths/bios", "E:/other.bin")
    store.sync()
    cfg.backup_settings(store)                      # rotates the first one out

    assert cfg.backup_path(previous=True).is_file()
    prev = QSettings(str(cfg.backup_path(previous=True)), QSettings.Format.IniFormat)
    assert prev.value("paths/bios") == "C:/bios.bin"


def test_restoring_falls_back_to_the_previous_generation(store):
    _fill(store)
    cfg.backup_settings(store)
    store.setValue("paths/bios", "E:/other.bin"); store.sync()
    cfg.backup_settings(store)

    cfg.backup_path().unlink()                      # newest copy lost too
    store.clear(); store.sync()
    assert cfg.protect_settings(store) == 3
    assert cfg.bios_path(store) == "C:/bios.bin"


def test_restoring_takes_the_richest_generation_not_the_newest(store):
    """Launch once after a wipe and the NEWEST copy is of the damage -- the few
    keys a fresh start writes -- while the one before it still holds everything.
    Restoring "the latest" hands back the emptier of the two."""
    _fill(store)
    cfg.backup_settings(store)                      # 3 real keys

    store.clear()
    store.setValue("paths/bios", "F:/fresh.bin")    # what a fresh start leaves
    store.sync()
    cfg.backup_settings(store)                      # newest copy: 1 real key

    store.clear(); store.sync()
    assert cfg.protect_settings(store) == 3
    assert cfg.rom_folder(store) == "D:/roms", "the full generation won"


def test_a_first_run_restores_nothing_and_says_nothing(store):
    """No settings and no backup is not a disaster, it is a new install."""
    assert cfg.protect_settings(store) == 0
    assert not cfg.backup_path().exists()


def test_a_healthy_store_is_never_overwritten_by_a_backup(store):
    """`protect_settings` restores only an EMPTY store: it recovers what was lost,
    it never merges over settings someone is using."""
    _fill(store)
    cfg.backup_settings(store)
    store.setValue("paths/bios", "E:/current.bin"); store.sync()

    assert cfg.protect_settings(store) == 0
    assert cfg.bios_path(store) == "E:/current.bin"


def test_protect_is_safe_when_the_backup_cannot_be_written(store, monkeypatch):
    """A read-only folder must not stop the emulator from starting."""
    _fill(store)

    def boom(*a, **k):
        raise OSError("read-only")

    monkeypatch.setattr(cfg.Path, "replace", boom)
    monkeypatch.setattr(cfg.Path, "exists", lambda self: True)
    assert cfg.backup_settings(store) is False
