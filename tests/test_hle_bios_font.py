"""The BIOS font has to be READABLE, and nothing used to check that.

⛔ WHAT SHIPPED. The font was rasterised out of Pillow's default face — about
eleven pixels tall — into an 8x8 cell, so the bottom row of every glyph fell
outside the tile. `E` lost its base bar and became an `F`; `L` lost its foot and
became a bare stroke identical to `I`. Games that ask the BIOS for its character
set (SYSFONTSET) then drew menus nobody could read: Yahtzee's title screen came
out as "1 PENAYRE / HIBGF 3SOQEBS".

Every existing test passed throughout. They checked the image's size, its
checksum, its syscall table and its vectors — everything about the font except
whether it spelt anything. A checksum pins whatever is there, including a
mistake.

So these are shape checks, and each one names the letter it is about. The
fixture at the bottom is the old broken `E`: a check that cannot fail on the bug
it was written for is not a check, so it is made to fail on it here.
"""

import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
FONT = REPO / "hle_bios" / "font_2bpp.bin"

# The glyph as it was shipped: seven rows, the base bar clipped away.
BROKEN_E = (
    "........",
    ".#####..",
    ".#......",
    ".#......",
    ".#......",
    ".####...",
    ".#......",
    ".#......",
)


def glyph(font: bytes, char: str) -> list[str]:
    """One character out of the 2bpp CHAR-RAM image, as rows of '#' and '.'."""
    code = ord(char)
    tile = font[code * 16:(code + 1) * 16]
    rows = []
    for r in range(8):
        # Low byte first: a CHAR-RAM row is a little-endian 16-bit word, so the
        # byte at the lower address holds the RIGHT half of the row. Decoding it
        # the other way round is how the generator's own mistake went unseen —
        # the test agreed with the bug.
        word = tile[r * 2] | (tile[r * 2 + 1] << 8)
        rows.append("".join("#" if (word >> (14 - 2 * p)) & 3 else "." for p in range(8)))
    return rows


def lowest_inked_row(rows) -> str:
    inked = [r for r in rows if "#" in r]
    return inked[-1] if inked else ""


def highest_inked_row(rows) -> str:
    inked = [r for r in rows if "#" in r]
    return inked[0] if inked else ""


@unittest.skipUnless(FONT.exists(), "hle_bios/font_2bpp.bin not present")
class HleBiosFontTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.font = FONT.read_bytes()
        assert len(cls.font) == 0x1000, len(cls.font)

    def test_the_check_catches_the_glyph_that_shipped(self):
        """The instrument first, or the rest of this file proves nothing."""
        self.assertLess(
            lowest_inked_row(BROKEN_E).count("#"), 4,
            "the broken E is no longer broken; this fixture has drifted",
        )

    def test_a_row_is_stored_low_byte_first(self):
        """⛔ THE FAULT THAT MADE EVERY GLYPH ILLEGIBLE.

        The two bytes of a row were written high-half first, so on screen each
        character appeared with its own left and right halves exchanged: shapes
        that were nearly letters and completely unreadable. Pinned against the
        retail BIOS's own tile 0x50 ('P'), whose first row reads `f0 3f` — the
        wide top bar's RIGHT half in the first byte.

        `L` is the clearest witness here: its top row is ink on the left only,
        so the first byte must be empty and the second must not.
        """
        top = self.font[ord("L") * 16:ord("L") * 16 + 2]
        self.assertEqual(
            top[0], 0x00,
            "the left half of L's stem is in the low byte: the row is reversed",
        )
        self.assertNotEqual(top[1], 0x00)

    def test_letters_that_stand_on_a_bar_have_one(self):
        """E, L, Z and U end in a horizontal stroke. Losing it was the bug."""
        for char in "ELZ":
            with self.subTest(char=char):
                rows = glyph(self.font, char)
                self.assertGreaterEqual(
                    lowest_inked_row(rows).count("#"), 4,
                    f"{char} has no bottom stroke:\n" + "\n".join(rows),
                )

    def test_letters_that_hang_from_a_bar_have_one(self):
        """T and Z start with one, and a clipped TOP would be the same fault."""
        for char in "TZ":
            with self.subTest(char=char):
                rows = glyph(self.font, char)
                self.assertGreaterEqual(
                    highest_inked_row(rows).count("#"), 4,
                    f"{char} has no top stroke:\n" + "\n".join(rows),
                )

    def test_E_is_not_an_F(self):
        """The exact confusion a clipped base bar produces."""
        self.assertNotEqual(glyph(self.font, "E"), glyph(self.font, "F"))

    def test_every_capital_is_its_own_shape(self):
        """L and I both came out as a bare vertical stroke."""
        seen = {}
        for char in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            key = tuple(glyph(self.font, char))
            self.assertNotIn(
                key, seen,
                f"{char} is drawn exactly like {seen.get(key)}",
            )
            seen[key] = char

    def test_every_digit_is_its_own_shape(self):
        seen = {}
        for char in "0123456789":
            key = tuple(glyph(self.font, char))
            self.assertNotIn(key, seen, f"{char} is drawn exactly like {seen.get(key)}")
            seen[key] = char

    def test_printable_characters_are_not_blank(self):
        """A space is blank; nothing else may be."""
        for code in range(0x21, 0x7F):
            with self.subTest(char=chr(code)):
                self.assertTrue(
                    any("#" in row for row in glyph(self.font, chr(code))),
                    f"{chr(code)!r} (0x{code:02X}) is blank",
                )
        self.assertFalse(any("#" in row for row in glyph(self.font, " ")))

    def test_glyphs_keep_a_gap_between_lines_and_columns(self):
        """Text is drawn on an 8x8 grid with no spacing of its own: the gap has
        to live inside the cell, or every line and word runs together."""
        for code in range(0x21, 0x7F):
            char = chr(code)
            rows = glyph(self.font, char)
            with self.subTest(char=char):
                self.assertEqual(
                    rows[7], "." * 8, f"{char!r} touches the line below",
                )
                self.assertTrue(
                    all(row[-1] == "." for row in rows),
                    f"{char!r} touches the character to its right",
                )

    def test_the_font_is_reproducible_from_the_written_glyphs(self):
        """The bytes on disk must be what the table says — the rasteriser that
        used to make them changed with the Pillow version installed."""
        sys.path.insert(0, str(REPO / "hle_bios"))
        try:
            import gen_crt0
        finally:
            sys.path.pop(0)
        self.assertEqual(gen_crt0.rasterise_font_2bpp(), self.font)


if __name__ == "__main__":
    unittest.main()
