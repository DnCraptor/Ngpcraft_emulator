"""ROM loading from bare files and from .zip / .7z archives (core/rom_loader).

The point of the module is that the rest of the emulator never has to know how a
ROM arrived. These tests pin the two things that matter: the bytes come back
intact, and the RIGHT entry is chosen when an archive holds more than one file.
7z tests skip cleanly when no backend (py7zr or a 7z CLI) is present.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from core import rom_loader


# A tiny but distinctive "ROM": bigger than a bundled read-me so the largest-wins
# tiebreak is actually exercised.
ROM = b"NGPCROM\x00" + bytes(range(256)) * 4
README = b"see https://example.invalid\n"


def _write_zip(path: Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)


# -- classification --------------------------------------------------------
def test_is_helpers():
    assert rom_loader.is_rom("Sonic.ngc")
    assert rom_loader.is_rom("Sonic.NGP")
    assert rom_loader.is_archive("Sonic.zip")
    assert rom_loader.is_archive("Sonic.7z")
    assert not rom_loader.is_archive("Sonic.ngc")
    assert rom_loader.is_loadable("Sonic.ngc") and rom_loader.is_loadable("Sonic.zip")
    assert not rom_loader.is_loadable("Sonic.txt")


# -- bare file -------------------------------------------------------------
def test_bare_rom_round_trips(tmp_path):
    p = tmp_path / "game.ngc"
    p.write_bytes(ROM)
    loaded = rom_loader.load(p)
    assert loaded.data == ROM
    assert loaded.from_archive is False
    assert loaded.name == "game.ngc"


# -- zip -------------------------------------------------------------------
def test_zip_single_entry(tmp_path):
    p = tmp_path / "game.zip"
    _write_zip(p, {"game.ngc": ROM})
    loaded = rom_loader.load(p)
    assert loaded.data == ROM
    assert loaded.from_archive is True
    assert loaded.name == "game.ngc"


def test_zip_picks_rom_over_readme(tmp_path):
    p = tmp_path / "game.zip"
    _write_zip(p, {"readme.txt": README, "game.ngc": ROM})
    loaded = rom_loader.load(p)
    assert loaded.data == ROM
    assert loaded.name == "game.ngc"


def test_zip_largest_rom_wins(tmp_path):
    p = tmp_path / "game.zip"
    small = b"\x00" * 32
    _write_zip(p, {"proto.ngp": small, "final.ngc": ROM})
    loaded = rom_loader.load(p)
    assert loaded.data == ROM


def test_zip_no_rom_ext_falls_back_to_only_file(tmp_path):
    p = tmp_path / "game.zip"
    _write_zip(p, {"mystery.bin": ROM})
    loaded = rom_loader.load(p)
    assert loaded.data == ROM


def test_empty_zip_raises(tmp_path):
    p = tmp_path / "empty.zip"
    _write_zip(p, {})
    with pytest.raises(rom_loader.RomArchiveError):
        rom_loader.load(p)


def test_corrupt_zip_raises(tmp_path):
    p = tmp_path / "bad.zip"
    p.write_bytes(b"PK\x03\x04 not really a zip")
    with pytest.raises(rom_loader.RomArchiveError):
        rom_loader.load(p)


# -- 7z (needs a backend) --------------------------------------------------
def _have_7z_backend() -> bool:
    try:
        import py7zr  # noqa: F401
        return True
    except ImportError:
        return rom_loader._seven_zip_cli() is not None


sevenzip = pytest.mark.skipif(
    not _have_7z_backend(), reason="no 7z backend (py7zr or 7z CLI) installed")


@sevenzip
def test_7z_picks_rom_over_readme(tmp_path):
    import py7zr

    p = tmp_path / "game.7z"
    with py7zr.SevenZipFile(p, "w") as z:
        z.writestr(README, "readme.txt")
        z.writestr(ROM, "game.ngc")
    loaded = rom_loader.load(p)
    assert loaded.data == ROM
    assert loaded.from_archive is True
    assert loaded.name == "game.ngc"


@sevenzip
def test_7z_nested_path(tmp_path):
    import py7zr

    p = tmp_path / "game.7z"
    with py7zr.SevenZipFile(p, "w") as z:
        z.writestr(ROM, "roms/final.ngc")
    loaded = rom_loader.load(p)
    assert loaded.data == ROM
    assert loaded.name == "final.ngc"
