# -*- coding: utf-8 -*-
"""The scroll-plane viewer: map decoding, the colour paths, and the per-line camera.

Pure numpy against a fake address space -- no Qt, no core. The colour rules are the
renderer's (`cpp/src/render.cpp`); where a test asserts one, it names the rule, so a
future change to the renderer breaks the test that claims to mirror it instead of
leaving the viewer quietly drawing a different picture from the emulator.
"""

import numpy as np

from core import tilemap_view as tv


class _Mem:
    def __init__(self):
        self.b = bytearray(0x10000)

    def read(self, addr, n=1):
        return bytes(self.b[addr:addr + n])

    def put(self, addr, data):
        self.b[addr:addr + len(data)] = data

    def entry(self, plane, tx, ty, tile, *, palette=0, h_flip=False, v_flip=False):
        attrib = ((tile >> 8) & 1) | (palette << 1) | (v_flip << 6) | (h_flip << 7)
        at = tv.MAP_BASE[plane] + (ty * 32 + tx) * 2
        self.b[at] = tile & 0xFF
        self.b[at + 1] = attrib

    def tile(self, index, rows):
        """rows: 8 iterables of 8 values 0..3."""
        out = bytearray(16)
        for r, row in enumerate(rows):
            odd = (row[0] << 6) | (row[1] << 4) | (row[2] << 2) | row[3]
            even = (row[4] << 6) | (row[5] << 4) | (row[6] << 2) | row[7]
            out[r * 2] = even
            out[r * 2 + 1] = odd
        self.put(tv.CHAR_RAM + index * 16, bytes(out))

    def k2ge_palette(self, plane, code, colors):
        """colors: 4 12-bit words for pixel values 0..3."""
        base = tv.PALETTE_BASE[plane] + code * 8
        for i, c in enumerate(colors):
            self.b[base + i * 2] = c & 0xFF
            self.b[base + i * 2 + 1] = (c >> 8) & 0xFF


def _solid_tile(value):
    return [[value] * 8 for _ in range(8)]


# ---------------------------------------------------------------- decoding
def test_pixel_values_follow_the_byte_order_the_hardware_uses():
    """odd byte holds dots 0-3, even byte dots 4-7 (render.cpp tile_row). Get this
    backwards and every tile comes out mirrored in 4-pixel halves -- which looks
    like corruption, not like a decode bug."""
    m = _Mem()
    m.tile(0, [[0, 1, 2, 3, 3, 2, 1, 0]] + [[0] * 8] * 7)
    px = tv.tile_pixel_values(m.read(tv.CHAR_RAM, 16))
    assert list(px[0, 0]) == [0, 1, 2, 3, 3, 2, 1, 0]


def test_a_plane_renders_its_entries_at_the_right_place():
    m = _Mem()
    m.tile(1, _solid_tile(1))
    m.k2ge_palette(tv.SCR1, 0, (0x0000, 0x000F, 0, 0))     # value 1 -> pure red
    m.entry(tv.SCR1, tx=3, ty=2, tile=1)
    view = tv.read_plane(m.read, tv.SCR1)
    assert view.rgb.shape == (256, 256, 3)
    assert tuple(view.rgb[2 * 8 + 4, 3 * 8 + 4]) == (255, 0, 0)
    assert tuple(view.rgb[0, 0]) != (255, 0, 0), "only the placed entry is drawn"


def test_the_ninth_tile_bit_comes_from_the_attribute():
    """Character numbers are 9 bits: attrib bit 0 is the top one. A viewer that
    reads 8 shows tile 5 where tile 261 lives -- plausible garbage."""
    m = _Mem()
    m.entry(tv.SCR1, 0, 0, tile=261)
    view = tv.read_plane(m.read, tv.SCR1)
    assert int(view.tiles[0, 0]) == 261


def test_flips_are_applied_and_reported():
    m = _Mem()
    m.tile(1, [[1, 0, 0, 0, 0, 0, 0, 0]] + [[0] * 8] * 7)
    m.k2ge_palette(tv.SCR1, 0, (0x0000, 0x000F, 0, 0))
    m.entry(tv.SCR1, 0, 0, tile=1)                       # normal
    m.entry(tv.SCR1, 1, 0, tile=1, h_flip=True)
    m.entry(tv.SCR1, 2, 0, tile=1, v_flip=True)
    view = tv.read_plane(m.read, tv.SCR1)
    assert tuple(view.rgb[0, 0]) == (255, 0, 0)          # top-left
    assert tuple(view.rgb[0, 8 + 7]) == (255, 0, 0)      # mirrored to the right edge
    assert tuple(view.rgb[7, 16]) == (255, 0, 0)         # mirrored to the bottom
    assert tv.entry_at(view, 1, 0).h_flip
    assert tv.entry_at(view, 2, 0).v_flip


def test_transparency_is_per_pixel_not_per_tile():
    """There is NO 'tile 0 is blank' rule -- character 0 is 16 ordinary bytes and
    transparency is pixel value 0 (render.cpp, pass 242)."""
    m = _Mem()
    m.tile(0, [[0, 1, 0, 1, 0, 1, 0, 1]] * 8)
    m.k2ge_palette(tv.SCR1, 0, (0x0FFF, 0x000F, 0, 0))
    view = tv.read_plane(m.read, tv.SCR1)
    assert view.transparent[0, 0] and not view.transparent[0, 1]


def test_entry_info_gives_the_addresses_you_would_go_and_poke():
    m = _Mem()
    m.entry(tv.SCR2, tx=5, ty=4, tile=0x101, palette=9, h_flip=True)
    view = tv.read_plane(m.read, tv.SCR2)
    info = tv.entry_at(view, 5, 4)
    assert info.addr == 0x009800 + (4 * 32 + 5) * 2
    assert info.tile == 0x101
    assert info.tile_addr == 0x00A000 + 0x101 * 16
    assert info.palette == 9 and info.h_flip and not info.v_flip


# ---------------------------------------------------------------- colour paths
def test_k2ge_palette_index_is_code_times_eight_plus_value_times_two():
    """The K2GE rule from render.cpp `resolve`. Palette code 3, pixel value 2."""
    m = _Mem()
    m.tile(1, _solid_tile(2))
    m.k2ge_palette(tv.SCR1, 3, (0, 0, 0x0F00, 0))        # value 2 -> pure blue
    m.entry(tv.SCR1, 0, 0, tile=1, palette=3)
    view = tv.read_plane(m.read, tv.SCR1)
    assert not view.compat
    assert tuple(view.rgb[0, 0]) == (0, 0, 255)


def test_compat_mode_uses_the_single_pc_bit_and_the_level_lut():
    """In K1GE compat only ONE palette bit exists (P.C, attrib bit 5), the LUT turns
    the pixel value into a 3-bit LEVEL, and the level indexes a 12-bit palette at
    p_c*8 + level. Reading four palette bits here would send every tile to a
    palette the hardware never selects."""
    m = _Mem()
    m.b[tv.MODE_REGISTER] = 0x80                          # K1GE compat
    m.tile(1, _solid_tile(1))
    lut = bytearray(8)
    lut[1 * 4 + 1] = 5                                    # p_c=1, value 1 -> level 5
    m.put(tv.K1GE_LUT[tv.SCR1], bytes(lut))
    pal = bytearray(32)
    idx = 1 * 8 + 5                                       # p_c*8 + level
    pal[idx * 2] = 0xF0                                   # green
    pal[idx * 2 + 1] = 0x00
    m.put(tv.K1GE_PAL[tv.SCR1], bytes(pal))
    m.entry(tv.SCR1, 0, 0, tile=1, palette=0b0010_0000 >> 1)   # sets attrib bit 5
    view = tv.read_plane(m.read, tv.SCR1)
    assert view.compat
    assert tuple(view.rgb[0, 0]) == (0, 255, 0)


def test_a_mono_console_goes_straight_to_the_grey_ramp():
    """A real K1GE stops at the level: no colour RAM is read, so no cartridge write
    can flatten the ramp. The mode bit cannot answer this -- 0x87E2 is a register
    that console does not have -- so the CONSOLE flag decides."""
    m = _Mem()
    m.tile(1, _solid_tile(1))
    lut = bytearray(8); lut[1] = 7                        # p_c=0, value 1 -> level 7
    m.put(tv.K1GE_LUT[tv.SCR1], bytes(lut))
    m.entry(tv.SCR1, 0, 0, tile=1)
    assert m.b[tv.MODE_REGISTER] == 0, "the mode register is untouched on purpose"
    view = tv.read_plane(m.read, tv.SCR1, k1ge_console=True)
    assert view.compat
    assert tuple(view.rgb[0, 0]) == (0, 0, 0)             # grey ramp level 7 = black


# ---------------------------------------------------------------- the camera
def _log(rows):
    """A raster log: 152 rows of the 0x8000 register block."""
    out = []
    for line in range(tv.SCREEN_H):
        blk = bytearray(0x40)
        soh, sov = rows(line)
        blk[0x32], blk[0x33] = soh & 0xFF, sov & 0xFF
        out.append(bytes(blk))
    return tuple(out)


def test_camera_reads_the_registers_each_line_was_drawn_with():
    spans = tv.camera_spans(_log(lambda ln: (ln, 0)), tv.SCR1)
    assert len(spans) == tv.SCREEN_H
    assert spans[0].x == 0 and spans[10].x == 10
    assert spans[10].y == 10, "y is line + the vertical scroll, wrapped"


def test_camera_wraps_because_the_plane_is_cyclical():
    spans = tv.camera_spans(_log(lambda ln: (0, 200)), tv.SCR1)
    assert spans[60].y == (60 + 200) & 0xFF == 4


def test_without_a_raster_log_every_line_reports_the_same_scroll():
    """The single-snapshot answer -- which is the mistake this view exists to
    expose, so it must be reachable but never silent."""
    spans = tv.camera_spans(None, tv.SCR1, fallback=(7, 0))
    assert {s.x for s in spans} == {7}


def test_the_span_mask_covers_one_screen_width_per_line():
    spans = tv.camera_spans(_log(lambda ln: (0, 0)), tv.SCR1)
    mask = tv.span_mask(spans)
    assert mask[0].sum() == tv.SCREEN_W
    assert mask.sum() == tv.SCREEN_W * tv.SCREEN_H
    assert not mask[200].any(), "lines past the screen are not part of the view"


def test_the_span_mask_wraps_around_the_right_edge():
    spans = tv.camera_spans(_log(lambda ln: (200, 0)), tv.SCR1)
    mask = tv.span_mask(spans)
    assert mask[0, 200] and mask[0, 255] and mask[0, 0] and mask[0, 103]
    assert not mask[0, 104]


def test_line_scroll_spread_names_the_parallax():
    flat = tv.camera_spans(_log(lambda ln: (40, 0)), tv.SCR1)
    assert tv.line_scroll_spread(flat) == 0, "one scroll value all frame"
    wobbly = tv.camera_spans(_log(lambda ln: (40 + (ln % 9), 0)), tv.SCR1)
    assert tv.line_scroll_spread(wobbly) == 8


def test_line_scroll_spread_measures_around_the_circle():
    """A scroll wobbling between 254 and 2 has moved four pixels. Measuring it as a
    numeric range reports 252 -- a defect invented out of arithmetic."""
    spans = tv.camera_spans(_log(lambda ln: ((254 + (ln % 5)) & 0xFF, 0)), tv.SCR1)
    assert tv.line_scroll_spread(spans) == 4


def test_the_two_planes_read_different_registers():
    """SCR1 scrolls on 0x8032/33 and SCR2 on 0x8034/35. Crossing them is invisible
    on a game that scrolls both together and wrong on every game that does not."""
    rows = []
    for _ in range(tv.SCREEN_H):
        blk = bytearray(0x40)
        blk[0x32], blk[0x34] = 10, 90
        rows.append(bytes(blk))
    assert tv.camera_spans(tuple(rows), tv.SCR1)[0].x == 10
    assert tv.camera_spans(tuple(rows), tv.SCR2)[0].x == 90


def test_a_short_or_empty_vram_read_does_not_explode():
    class _Tiny:
        def read(self, addr, n=1):
            return b"\x00" * min(n, 8)
    view = tv.read_plane(_Tiny().read, tv.SCR1)
    assert view.rgb.shape == (256, 256, 3)


# ---------------------------------------------------------------- composition
def _bright_plane():
    m = _Mem()
    m.tile(1, _solid_tile(1))
    m.k2ge_palette(tv.SCR1, 0, (0x0000, 0x0FFF, 0, 0))     # value 1 -> white
    for ty in range(32):
        for tx in range(32):
            m.entry(tv.SCR1, tx, ty, tile=1)
    return tv.read_plane(m.read, tv.SCR1)


def test_compose_dims_outside_the_screen_and_leaves_inside_alone():
    """The region you came to judge keeps its TRUE colours; the rest is dimmed. A
    tint over the visible part would make the tool lie about the thing it is for."""
    view = _bright_plane()
    spans = tv.camera_spans(_log(lambda ln: (0, 0)), tv.SCR1)
    img = tv.compose(view, spans)
    assert tuple(img[10, 80]) == (255, 255, 255), "inside the screen: untouched"
    assert img[200, 80].max() < 255, "outside: dimmed"
    assert img[200, 80].max() > 0, "dimmed, not blacked out -- it is still readable"


def test_compose_marks_the_screen_edges():
    view = _bright_plane()
    spans = tv.camera_spans(_log(lambda ln: (30, 0)), tv.SCR1)
    img = tv.compose(view, spans)
    assert tuple(img[0, 30]) == tv.EDGE_COLOUR
    assert tuple(img[0, 30 + tv.SCREEN_W - 1]) == tv.EDGE_COLOUR


def test_compose_without_spans_shows_the_plane_untouched():
    view = _bright_plane()
    assert np.array_equal(tv.compose(view), view.rgb)


def test_compose_can_mark_transparent_pixels():
    m = _Mem()
    m.tile(0, _solid_tile(0))
    view = tv.read_plane(m.read, tv.SCR1)
    plain = tv.compose(view)
    marked = tv.compose(view, mark_transparent=True)
    assert tuple(marked[0, 0]) == tv.TRANSPARENT_COLOUR
    assert not np.array_equal(plain, marked)
