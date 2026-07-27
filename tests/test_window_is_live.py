"""The WINDOW is not latched per line — and the scroll offsets still are.

⛔ THE BUG THIS CONDEMNS. "Graphical glitches in these two games, right below the energy
bar, with flickering lines" -- a junk row sitting just above the bottom HUD. Samurai
Shodown! 2 hides that seam the way the hardware lets it: from its H-blank handler it
writes WSI.H = 0, which empties the window so the whole line comes out as the
out-of-window colour, and puts 0xA0 back one line later. A deliberate one-line blank.

This core read the window out of the START-OF-LINE raster snapshot, like the scroll
registers, so the blank landed on the line AFTER the one the game asked for -- harmlessly
inside the black HUD -- and the junk row it was meant to cover stayed on screen. MEASURED
over 12 000 frames of a real match: 4 053 of 8 802 blank requests (46%) left content on
the line the game blanked. After the fix: 0 of 9 046.

⚖️ WHY THE WINDOW IS DIFFERENT, from the manufacturer. Every display register this
renderer reads carries an explicit "reflected in the next line being drawn" caution in
the K2GE Tech Ref -- 0x8012 (§ 4-11), 0x8020/21 (§ 4-3-4), 0x8030 (§ 4-4-7), 0x8032..35
(§ 4-4-8), 0x8118 (§ 4-6). The window registers (§ 4-5, 0x8002..0x8005) have a caution of
their own, and it is about WBA + WSI overflowing 256 -- not about latching. The one block
whose caution does not mention the next line is the one that gates the display area
against the raster as it draws.

⚠️ BOTH HALVES ARE TESTED HERE, and that is the point: a test that only pinned the window
would be satisfied by "make everything live", which would break every scroll split in
every game. The scroll test is the control group.
"""

from __future__ import annotations

import unittest

from core import native

RASV = 0x8009            # the raster position register the hardware exposes
WINDOW = 0x8002          # WBA.H, WBA.V, WSI.H, WSI.V
WSI_H = 0x8004
S1SO_V = 0x8033
CHAR_RAM, SCR1_MAP, SCR1_PAL = 0xA000, 0x9000, 0x8280
OOW_PALETTE = 0x83F0
W, H = 160, 152


def _rom() -> bytes:
    rom = bytearray(b"\xFF" * 0x100000)
    rom[0:28] = b" LICENSED BY SNK CORPORATION"
    rom[0x1C:0x20] = (0x200040).to_bytes(4, "little")
    rom[0x23] = 0x10
    # `nop ; jr -2` -- a SPIN, deliberately not a `halt`. A halted CPU makes the core
    # fast-forward a WHOLE FRAME per run() call (core.cpp: 200 scanlines in one go), so
    # a host poke could never land inside a frame and every measurement below would be
    # taken from a frame drawn after the poke was undone. The scene still never changes:
    # the loop touches nothing.
    rom[0x40:0x43] = b"\x00\x68\xFD"
    return bytes(rom)


@unittest.skipUnless(native.available(), "native core not built (cmake --build cpp/build)")
class WindowIsLiveTests(unittest.TestCase):
    TARGET = 100          # a line well inside the picture

    def setUp(self) -> None:
        self.m = native.NativeMachine(_rom())
        self.m.reset(bios_handoff=True)
        self.m.write(WINDOW, bytes([0x00, 0x00, 0xA0, 0x98]))     # the whole screen
        self.m.write(0x8012, bytes([0x02]))                       # OOWC = index 2
        self.m.write(OOW_PALETTE + 2 * 2, bytes([0x0F, 0x0F]))    # a colour nothing else uses
        # A CHECKERBOARD of 8x8 blocks: every row carries two colours, so a line that
        # comes out uniform really was blanked, and a one-tile vertical scroll visibly
        # swaps the pattern.
        self.m.write(CHAR_RAM, bytes([0x55, 0x55] * 8))           # tile 0: pixel value 1
        self.m.write(CHAR_RAM + 16, bytes([0xAA, 0xAA] * 8))      # tile 1: pixel value 2
        self.m.write(SCR1_PAL, bytes([0x00, 0x00, 0xF0, 0x00, 0x00, 0x0F, 0x00, 0x0E]))
        for ty in range(32):
            for tx in range(32):
                self.m.write(SCR1_MAP + (ty * 32 + tx) * 2, bytes([(tx + ty) & 1, 0]))
        self.m.run_frames(2)
        self.baseline = self._rows()
        self.oow = 0x0F0F

    def tearDown(self) -> None:
        self.m.close()

    # --- driving the beam to a chosen line, the way the hardware exposes it -------
    def _line(self) -> int:
        return self.m.read(RASV, 1)[0]

    def _run_to_line(self, line: int) -> None:
        for _ in range(4000):                    # bounded: never spin on a stuck raster
            if self._line() == line:
                return
            self.m.run(32, record=False)
        self.fail(f"the raster never reached line {line}")

    def _rows(self) -> list[list[int]]:
        fb = self.m.framebuffer()
        return [fb[y * W:(y + 1) * W] for y in range(H)]

    def _changed(self, rows: list[list[int]]) -> list[int]:
        return [y for y in range(151) if rows[y] != self.baseline[y]]

    # --- the harness has to be able to see a difference at all -------------------
    def test_the_scene_makes_a_blanked_line_visible(self) -> None:
        self.assertGreater(len(set(self.baseline[self.TARGET])), 1,
                           "every row must carry two colours, or 'uniform' proves nothing")
        self.assertNotIn(self.oow, set(self.baseline[self.TARGET]),
                         "the out-of-window colour must not occur in the scene")

    def test_the_window_takes_effect_on_the_line_it_is_written_on(self) -> None:
        line = self.TARGET
        self._run_to_line(line)
        self.m.write(WSI_H, bytes([0x00]))        # the window is empty from here
        self._run_to_line(line + 1)
        self.m.write(WSI_H, bytes([0xA0]))        # ... and back, one line later
        self._run_to_line(151)
        rows = self._rows()

        self.assertEqual(rows[line], [self.oow] * W,
                         f"line {line} is the one the write happened on: it must be blank")
        self.assertEqual(self._changed(rows), [line],
                         "exactly one line, and it is that one")

    def test_the_scroll_offset_is_STILL_latched_to_the_next_line(self) -> None:
        # THE CONTROL GROUP. 0x8032..35 carries the Tech Ref's "displayed from the next
        # line" caution; the window does not. Making everything live would satisfy the
        # test above and break every scroll split in every game -- this is what stops it.
        line = self.TARGET
        self._run_to_line(line)
        self.m.write(S1SO_V, bytes([0x08]))       # shift the plane by a whole tile row
        self._run_to_line(151)
        rows = self._rows()

        changed = self._changed(rows)
        self.assertNotIn(line, changed,
                         f"line {line} was already being drawn: the write must not reach it")
        self.assertIn(line + 1, changed, "the new offset must show from the NEXT line")

    def test_a_static_window_still_clips_the_way_it_always_did(self) -> None:
        # Nothing changes mid-frame -> live and snapshot are the same value, so the
        # ordinary sub-window case must be untouched by all of the above.
        self.m.write(WINDOW, bytes([20, 15, 90, 60]))
        self.m.run_frames(2)
        rows = self._rows()
        self.assertEqual(rows[10], [self.oow] * W, "above the window: all out-of-window")
        self.assertEqual(rows[40][0], self.oow, "left of the window: out-of-window")
        self.assertNotEqual(rows[40][60], self.oow, "inside the window: the scene shows")
        self.assertEqual(rows[80], [self.oow] * W, "below the window: all out-of-window")


if __name__ == "__main__":
    unittest.main()
