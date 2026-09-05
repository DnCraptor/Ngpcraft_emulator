// platform/main.cpp — NGP-on-RP2350, Milestone 0 (platform bring-up only).
//
// Goal of this file for now: prove the vendored pico-speccy platform builds and
// drives the display on real hardware, BEFORE any emulator glue exists. Once
// this shows text on screen, the three seam patches land on top:
//   * memory  — region dispatch replacing the core's flat 16 MB `mem`;
//   * video   — NGP renderer → 8-bit palette-indexed framebuffer + palette push;
//   * in/out  — gamepad → NGP 0xB0 input port, ngpc_get_audio → audio driver.
//
// ⚠ The full board bring-up (vreg + 378 MHz clock + QMI/flash fix-up + butter
// PSRAM init) still has to be ported from pico-speccy's own main.cpp; this
// placeholder leans on SDK defaults and is expected to be reconciled in the
// next patch. It is deliberately minimal so there is something flashable to
// confirm toolchain + board + display path.

#include <pico/stdlib.h>

extern "C" {
#include "graphics.h"
}

int main() {
    stdio_init_all();

    // TODO(boot): port clock/vreg/QMI/PSRAM init from pico-speccy main.cpp.
    graphics_init();
    graphics_set_mode(TEXTMODE_DEFAULT);
    clrScr(0);
    draw_text("NGP / RP2350 - platform boot OK", 0, 0, 7, 0);
    draw_text("emulator core linked, not yet driven", 0, 1, 7, 0);

    while (true) {
        tight_loop_contents();
    }
    return 0;
}
