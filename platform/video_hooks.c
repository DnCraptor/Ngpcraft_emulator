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

// 1 = VGA, 2 = HDMI, 0 = auto-detect by cable (needs testPins, port pending).
// Set to 1 for VGA. TODO: port testPins() to enable cable auto-detect (video_driver=0).
uint8_t video_driver = 2;

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
