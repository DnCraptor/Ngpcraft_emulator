"""A collection archive holds SEVERAL games, and each one is a game in its own right.

⛔ WHAT THIS ENDS. `Pack.zip` used to resolve to whichever member was LARGEST -- so a
forty-game collection booted one arbitrary cartridge, silently, and all forty would
have shared one `saves/Pack.flash`. Nothing said so; it just was not the game you
asked for.

The fix is a VIRTUAL PATH, `Pack.zip/Game A.ngc`. It is not a path on disk, and that
is the point: every per-game thing here is derived from the ROM's `Path`, so a virtual
path hands each game inside the archive its own save, savestates, watches, cover and
library entry for free.

⚡ AND AN ARCHIVE WITH ONE GAME KEEPS ITS OLD PATH. Expanding `Sonic.zip` to
`Sonic.zip/Sonic.ngc` would rename the save, the states and the cover of every
single-game archive in every existing library. The new shape is used only where the
old one cannot work -- which is the one test below nobody would think to write.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from core import rom_loader as rl

A_BYTES = b"GAME-A" * 200
B_BYTES = b"GAME-B" * 500


@pytest.fixture
def pack(tmp_path) -> Path:
    """A collection: two ROMs of different sizes, plus junk that is not a game."""
    z = tmp_path / "Pack.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("Game A.ngc", A_BYTES)
        zf.writestr("Game B.ngp", B_BYTES)
        zf.writestr("readme.txt", b"not a game")
        zf.writestr("cover.png", b"\x89PNG not a game either")
    return z


@pytest.fixture
def single(tmp_path) -> Path:
    z = tmp_path / "Solo.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("Solo Game.ngc", b"\x00" * 64)
        zf.writestr("readme.txt", b"still not a game")
    return z


# --------------------------------------------------------------------------- #
# the loader
# --------------------------------------------------------------------------- #
def test_the_games_in_an_archive_can_be_listed(pack):
    assert rl.list_roms(pack) == ["Game A.ngc", "Game B.ngp"]


def test_only_real_roms_count_as_games(pack, single):
    """A read-me and a cover live in these archives too. If they counted, a
    single-game archive would look like a collection and get expanded."""
    assert rl.list_roms(single) == ["Solo Game.ngc"]
    assert "readme.txt" not in rl.list_roms(pack)


def test_a_named_member_loads_ITS_bytes_not_the_biggest_one(pack):
    """The whole point: `Game A` must give game A. Sized so the old rule (take the
    largest) would hand back B -- a test that passed either way would prove nothing."""
    assert rl.load(pack / "Game A.ngc").data == A_BYTES
    assert rl.load(pack / "Game B.ngp").data == B_BYTES
    assert rl.load(pack).data == B_BYTES, "the plain-archive path must be unchanged"


def test_a_member_that_is_not_there_says_so(pack):
    """Rather than falling back to some other game, which is how you end up playing
    the wrong cartridge and blaming the emulator."""
    with pytest.raises(rl.RomArchiveError) as e:
        rl.load(pack / "Nope.ngc")
    assert "Nope.ngc" in str(e.value)


def test_a_member_is_read_only_like_the_archive_it_came_from(pack):
    """`from_archive` is what sends the save to a sidecar instead of into the .zip."""
    assert rl.load(pack / "Game A.ngc").from_archive is True


@pytest.mark.parametrize("given,archive,member", [
    ("Pack.zip/Game A.ngc", "Pack.zip", "Game A.ngc"),
    ("Pack.zip", "Pack.zip", None),
    ("Game.ngc", "Game.ngc", None),
    ("Pack.7z/sub/Game B.ngp", "Pack.7z", "sub/Game B.ngp"),
])
def test_a_virtual_path_splits_where_the_archive_ends(given, archive, member):
    got_archive, got_member = rl.split_member(Path("C:/roms") / given)
    assert got_archive == Path("C:/roms") / archive
    assert got_member == member


def test_an_unreadable_archive_lists_nothing_rather_than_raising(tmp_path):
    """Listing runs during a library scan. One bad archive is one card missing, never
    a scan that stops -- that failure mode has already cost this project a half-empty
    library once."""
    bad = tmp_path / "Broken.zip"
    bad.write_bytes(b"this is not a zip")
    assert rl.list_roms(bad) == []


# --------------------------------------------------------------------------- #
# the library
# --------------------------------------------------------------------------- #
def test_a_collection_becomes_one_card_per_game(pack, single, tmp_path):
    shell = pytest.importorskip("ngpc_shell")
    found = shell.scan_roms(tmp_path)
    assert pack / "Game A.ngc" in found
    assert pack / "Game B.ngp" in found
    assert pack not in found, "the archive itself must not also be offered"


def test_an_archive_with_ONE_game_keeps_its_own_path(single, tmp_path):
    """⛔ The migration guard. Expanding this one would rename the save, the states
    and the cover of every single-game archive in every library that exists today."""
    shell = pytest.importorskip("ngpc_shell")
    found = shell.scan_roms(tmp_path)
    assert single in found
    assert single / "Solo Game.ngc" not in found


def test_each_game_in_a_collection_gets_its_own_save_and_cover(pack):
    """The identity that matters. Two games in one archive must not share a flash
    save -- which is exactly what `Pack.zip` for both of them would have done."""
    from core import native_session as ns

    a, b = pack / "Game A.ngc", pack / "Game B.ngp"
    assert ns.default_save_path(a) != ns.default_save_path(b)
    assert ns.default_save_path(a).name == "Game A.flash"

    shell = pytest.importorskip("ngpc_shell")
    assert shell._cover_path(a) != shell._cover_path(b)
    assert str(a) != str(b)          # ...and so are their library entries


def test_a_game_in_a_collection_sorts_by_its_archive_date(pack):
    """`stat()` on a virtual path raises, so without a fallback every game inside an
    archive sorts as the oldest thing in the library."""
    import ngpc_library as lib

    assert lib._stat(pack / "Game A.ngc", "st_mtime") == lib._stat(pack, "st_mtime")
    assert lib._stat(pack / "Game A.ngc", "st_mtime") > 0
