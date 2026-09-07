// platform/video_hooks.c — the app-side symbols the vendored pico-speccy
// vga/hdmi drivers call back into. In pico-speccy these live in src/ (Video.cpp,
// Config.cpp, OSDMain.cpp); we did not import that ZX code, so we provide our own.
//
// This is the seed of the VIDEO seam. For Milestone 0 it owns one 8-bit
// palette-indexed framebuffer and shows a colour-bar test pattern, proving the
// scanout path end to end. Seam 2 (NGP renderer -> this framebuffer) replaces
// video_show_test_pattern() with the real per-frame blit + palette push.

#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include "graphics.h"

// Default VGA/HDMI mode 0 is screen_width=320, v_active=480; the drivers
// line-double (getLineBuffer(line>>1)), so the framebuffer is 320x240. A few
// spare lines absorb v_offset so getLineBuffer() never indexes past the buffer.
#define FB_W       320
#define FB_H_VIS   240
#define FB_H_ALLOC 256

static uint8_t  fb[FB_H_ALLOC][FB_W];

// 0 = auto-detect by cable (main() sets linkVGA01 via testPins), 1 = VGA, 2 = HDMI.
// Force a specific output by setting 1 (VGA) or 2 (HDMI).
uint8_t video_driver = 0;

// -- driver callbacks ---------------------------------------------------------

uint8_t *getLineBuffer(int line) {
    if (line < 0) line = 0;
    if (line >= FB_H_ALLOC) line = FB_H_ALLOC - 1;
    return fb[line];
}

int  get_video_mode(void)        { return 0; }     // index into the driver's mode table
int  get_framebuffer_width(void) { return FB_W; }
int  get_framebuffer_height(void){ return FB_H_VIS; }

void ESPectrum_vsync(void) { /* Seam 2 will render the next NGP frame here. */ }

// vga.c sizes its scratch buffers from this. Probe the heap for the largest
// block still available (mirrors what pico-speccy's OSDMain.cpp reports).
size_t getLargestAllocatable(void) {
    for (size_t s = 512u * 1024u; s >= 4096u; s -= 4096u) {
        void *p = malloc(s);
        if (p) { free(p); return s; }
    }
    return 0;
}

// -- Milestone 0 test pattern (removed once Seam 2 drives the framebuffer) -----

void video_show_test_pattern(void) {
    // 8 vertical colour bars via palette indices 0..7.
    static const uint32_t bar_rgb[8] = {
        0x000000, 0x0000FF, 0xFF0000, 0xFF00FF,
        0x00FF00, 0x00FFFF, 0xFFFF00, 0xFFFFFF,
    };
    for (uint8_t i = 0; i < 8; i++) graphics_set_palette(i, bar_rgb[i]);

    for (int y = 0; y < FB_H_ALLOC; y++)
        for (int x = 0; x < FB_W; x++)
            fb[y][x] = (uint8_t)((x / (FB_W / 8)) & 7);

    graphics_set_buffer(&fb[0][0], FB_W, FB_H_VIS);
}


// ── on-screen trap readout (Seam 1 diagnostics) ──────────────────────────────
// A tiny 8x8 hex font so a trap can name itself on the HDMI/VGA screen without a
// serial cable: one flash, read the numbers, port the opcode. Drawn straight into
// the same 8-bit framebuffer the scanout already shows (no mode change).
static const uint8_t hexfont[16][8] = {
    {0x3C,0x66,0x6E,0x76,0x66,0x66,0x3C,0}, {0x18,0x38,0x18,0x18,0x18,0x18,0x7E,0},
    {0x3C,0x66,0x06,0x0C,0x30,0x60,0x7E,0}, {0x3C,0x66,0x06,0x1C,0x06,0x66,0x3C,0},
    {0x0C,0x1C,0x3C,0x6C,0x7E,0x0C,0x0C,0}, {0x7E,0x60,0x7C,0x06,0x06,0x66,0x3C,0},
    {0x1C,0x30,0x60,0x7C,0x66,0x66,0x3C,0}, {0x7E,0x06,0x0C,0x18,0x30,0x30,0x30,0},
    {0x3C,0x66,0x66,0x3C,0x66,0x66,0x3C,0}, {0x3C,0x66,0x66,0x3E,0x06,0x0C,0x38,0},
    {0x18,0x3C,0x66,0x66,0x7E,0x66,0x66,0}, {0x7C,0x66,0x66,0x7C,0x66,0x66,0x7C,0},
    {0x3C,0x66,0x60,0x60,0x60,0x66,0x3C,0}, {0x78,0x6C,0x66,0x66,0x66,0x6C,0x78,0},
    {0x7E,0x60,0x60,0x7C,0x60,0x60,0x7E,0}, {0x7E,0x60,0x60,0x7C,0x60,0x60,0x60,0},
};

static void put_glyph(int px, int py, int scale, int g, uint8_t col) {
    for (int r = 0; r < 8; r++)
        for (int c = 0; c < 8; c++)
            if (hexfont[g][r] & (0x80 >> c))
                for (int sy = 0; sy < scale; sy++)
                    for (int sx = 0; sx < scale; sx++) {
                        int x = px + c*scale + sx, y = py + r*scale + sy;
                        if ((unsigned)x < FB_W && (unsigned)y < FB_H_ALLOC) fb[y][x] = col;
                    }
}
static void put_hex(int x, int y, int scale, uint32_t v, int ndig, uint8_t col) {
    for (int i = ndig - 1; i >= 0; i--) { put_glyph(x, y, scale, (v >> (4*i)) & 0xF, col); x += (8*scale)+scale; }
}

// Rows (white on blue): stop_status(2), stop_pc(6), stop_opcode(2), frame_count(4).
void video_show_trap(uint32_t status, uint32_t pc, uint32_t op, uint32_t frames) {
    graphics_set_palette(0, 0x000000);
    graphics_set_palette(1, 0x000080);   // blue backdrop
    graphics_set_palette(7, 0xFFFFFF);   // white text
    for (int y = 0; y < FB_H_ALLOC; y++) for (int x = 0; x < FB_W; x++) fb[y][x] = 1;
    put_hex(16,  20, 3, status, 2, 7);
    put_hex(16,  70, 3, pc,     6, 7);
    put_hex(16, 120, 3, op,     2, 7);
    put_hex(16, 170, 3, frames, 4, 7);
    graphics_set_buffer(&fb[0][0], FB_W, FB_H_VIS);
}


// ── Seam 2: present the NGP framebuffer ──────────────────────────────────────
// ngpc_get_framebuffer() gives 160x152 pixels, 12-bit 0BGR (R=bits0-3, G=4-7,
// B=8-11). We map each to an RGB332 index (256-entry palette set once) and blit
// centred, 1:1, into the 320x240 scanout buffer. Lossy (12->8 bit) but no core
// change; a palette-index path for full fidelity can come later.
void video_init_ngp(void) {
    // RGB232 in indices 0..127 only. The HDMI driver reserves 240..255 (control),
    // 184..237 (data island, when audio is on) and uses bit 0x40 for dither, so a
    // full 256-entry palette lands in slots whose conv_color is never rebuilt --
    // that is the "colour table" corruption. 0..127 is below all of that.
    for (int i = 0; i < 128; i++) {
        uint8_t r2 = (i >> 5) & 3, g3 = (i >> 2) & 7, b2 = i & 3;
        uint8_t r8 = (uint8_t)(r2 * 255 / 3), g8 = (uint8_t)(g3 * 255 / 7), b8 = (uint8_t)(b2 * 255 / 3);
        graphics_set_palette((uint8_t)i, ((uint32_t)r8 << 16) | ((uint32_t)g8 << 8) | b8);
    }
    for (int y = 0; y < FB_H_ALLOC; y++) for (int x = 0; x < FB_W; x++) fb[y][x] = 0;  // black border
    graphics_set_buffer(&fb[0][0], FB_W, FB_H_VIS);
}

void video_present_ngp(const uint16_t *src) {
    const int OX = (FB_W - 160) / 2;        // 80
    const int OY = (FB_H_VIS - 152) / 2;    // 44
    for (int y = 0; y < 152; y++) {
        uint8_t *dst = &fb[OY + y][OX];
        const uint16_t *s = &src[y * 160];
        for (int x = 0; x < 160; x++) {
            uint16_t p = s[x];
            uint8_t r2 = (uint8_t)((p & 0x0F) >> 2);          // top 2 of R4
            uint8_t g3 = (uint8_t)(((p >> 4) & 0x0F) >> 1);   // top 3 of G4
            uint8_t b2 = (uint8_t)(((p >> 8) & 0x0F) >> 2);   // top 2 of B4
            dst[x] = (uint8_t)((r2 << 5) | (g3 << 2) | b2);   // 0..127
        }
    }
}
