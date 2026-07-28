"""Analyse ROM must read the CARTRIDGE, not the container it arrived in.

⛔ THE BUG THIS CONDEMNS. "In the library tab if i analyze a compressed rom the info
are all wrong as it treat the compressed rom as plain rom" -- report 2026-07-28. The
library lists .zip/.7z and offers "Analyze ROM…" on them, and every other consumer in
the emulator reads through the one choke point built for this (core/rom_loader): the
player, the thumbnail worker, the CLI. The analyser was the last place still calling
`read_bytes()` on whatever the user picked, so on an archive it parsed the ZIP's own
local file header as a cartridge header.

⚠️ AND IT DID NOT LOOK BROKEN, which is why this test asserts on the VALUES and not
merely on "no exception". Measured on a zipped SNK Gals' Fighters before the fix:

    file size    901 KiB          (the COMPRESSED size)
    title        "ls' Fighters"   (a slice of the stored FILENAME)
    game id      204B             (two bytes of "SNK")
    entry point  4E530000         (a 32-bit value the 24-bit vector cannot hold)

A plausible-looking report, every field wrong.

🎯 THE GATE IS AN EQUALITY, not a list of expected strings: the same ROM loose and
packed must produce the same facts. That cannot be satisfied by a wrong-but-stable
parse, and it needs no golden values to drift.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from core import romcheck

# The facts that describe the cartridge itself. `archive`/`ROM inside` are
# deliberately excluded: those SHOULD differ -- they name the container.
CARTRIDGE_FACTS = ("file size", "title", "game id", "version", "mode",
                   "entry point", "copyright")


def _rom_image() -> bytes:
    """A minimal cartridge the header parser accepts, padded so it is not a stub.

    Deliberately BIGGER than its own compressed size, and full of a repeating
    pattern, so a report that measured the archive instead would disagree loudly.
    """
    rom = bytearray(b"\x00" * 0x8000)
    rom[0:28] = b" LICENSED BY SNK CORPORATION"
    rom[0x1C:0x20] = (0x200040).to_bytes(4, "little")   # entry point
    rom[0x20:0x22] = b"\x93\x00"                        # game id
    rom[0x22] = 0x30                                    # version
    rom[0x23] = 0x10                                    # colour
    rom[0x24:0x30] = b"ARCHIVETEST\x00"                 # title
    rom[0x40:0x44] = b"\x00\x68\xFE\x00"                # nop ; jr $
    return bytes(rom)


def _facts(path: Path) -> dict:
    # run=False: this is about WHICH BYTES get read, and the static half is where
    # every header fact is produced. It also keeps the test instant and core-free.
    report = romcheck.analyse(path, run=False)
    return {k: v for k, v in report.facts.items() if k in CARTRIDGE_FACTS}


@pytest.fixture
def loose(tmp_path) -> Path:
    p = tmp_path / "game.ngc"
    p.write_bytes(_rom_image())
    return p


def test_a_zipped_rom_reports_exactly_what_the_loose_one_does(loose, tmp_path):
    packed = tmp_path / "game.zip"
    with zipfile.ZipFile(packed, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(loose, loose.name)

    assert packed.stat().st_size < loose.stat().st_size, (
        "the archive must be smaller than the ROM, or 'it read the container' and "
        "'it read the ROM' would be indistinguishable here"
    )
    assert _facts(packed) == _facts(loose)


def test_the_report_still_names_the_archive_it_was_given(loose, tmp_path):
    # Reading THROUGH the archive must not hide it: the reader has to be able to
    # tell that the 32 KiB reported is the unpacked size, not the file on disk.
    packed = tmp_path / "game.zip"
    with zipfile.ZipFile(packed, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(loose, loose.name)

    facts = romcheck.analyse(packed, run=False).facts
    assert "game.zip" in facts["archive"]
    assert facts["ROM inside"] == "game.ngc"
    # ...and a loose ROM gains no such noise.
    assert "archive" not in romcheck.analyse(loose, run=False).facts


def test_a_broken_archive_is_reported_not_parsed_as_a_cartridge(tmp_path):
    # LONGER than a 0x40 header, and carrying the copyright string the BIOS wants:
    # a short scrap would be rejected as "too small" by either code path and this
    # test would pass without discriminating. Fed to the old path, these bytes parse
    # into a complete, clean-looking set of header facts.
    p = tmp_path / "game.zip"
    p.write_bytes(b" LICENSED BY SNK CORPORATION" + bytes(0x100))
    report = romcheck.analyse(p, run=False)
    assert report.errors, "a zip that cannot be opened must be an ERROR"
    assert "title" not in report.facts, (
        "the container was parsed as a cartridge: it produced header facts"
    )
