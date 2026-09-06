// platform/main.cpp — NGP-on-RP2350, Milestone 0 (platform bring-up only).
//
// Boots the vendored pico-speccy platform and shows a colour-bar test pattern,
// BEFORE any emulator glue. Once this shows on real hardware, the three seam
// patches land on top:
//   * memory  — region dispatch replacing the core's flat 16 MB `mem`;
//   * video   — NGP renderer -> the 8-bit framebuffer in platform/video_hooks.c;
//   * in/out  — gamepad -> NGP 0xB0 input port, ngpc_get_audio -> audio driver.

#include <pico/stdlib.h>
#include <pico/multicore.h>
#include <pico/sem.h>

#include "board.h"
extern "C" {
#include "graphics.h"
void video_show_test_pattern(void);          // platform/video_hooks.c
extern uint8_t linkVGA01;                     // defined in the vga driver
int testPins(uint32_t pin0, uint32_t pin1);   // platform/vga_detect.cpp
}

#ifndef PICO_DEFAULT_LED_PIN
#define PICO_DEFAULT_LED_PIN 25
#endif

// GP25 doubles as a boot progress indicator, so a black screen is still
// diagnosable. Blink pattern tells you how far boot got:
//   (nothing)          -> hung before/inside board_init, or before main
//   2 blinks, then dark -> board_init OK, but core1 graphics_init hung
//   2 + 3, then steady  -> all up; check the display for colour bars
static void led_init(void) {
    gpio_init(PICO_DEFAULT_LED_PIN);
    gpio_set_dir(PICO_DEFAULT_LED_PIN, GPIO_OUT);
}
static void led_blink(int n) {
    for (int i = 0; i < n; i++) {
        gpio_put(PICO_DEFAULT_LED_PIN, 1); sleep_ms(120);
        gpio_put(PICO_DEFAULT_LED_PIN, 0); sleep_ms(200);
    }
    sleep_ms(400);
}

int main() {
    led_init();
    led_blink(2);          // phase 1: main reached (before clock/flash bring-up)

    board_init();          // flash QE + vreg + clock @378 MHz (252 failsafe)
    led_blink(3);          // phase 2: board_init survived

    stdio_init_all();

    // Cable auto-detect: sample the VGA base pins so graphics_init (core1) routes
    // scanout to whichever connector is plugged (video_driver=0 enables this).
    linkVGA01 = testPins(VGA_BASE_PIN, VGA_BASE_PIN + 1);

    sem_init(&vga_start_semaphore, 0, 1);
    sem_init(&graphics_ready_semaphore, 0, 1);

    multicore_launch_core1(render_core);               // core1: graphics_init + scanout
    sem_acquire_blocking(&graphics_ready_semaphore);   // wait until graphics is up

    graphics_set_mode(GRAPHICSMODE_DEFAULT);
    video_show_test_pattern();                         // palette + framebuffer

    sem_release(&vga_start_semaphore);                 // let core1 enter its service loop

    while (true) {                                     // phase 3: steady 2 Hz heartbeat
        gpio_put(PICO_DEFAULT_LED_PIN, 1); sleep_ms(250);
        gpio_put(PICO_DEFAULT_LED_PIN, 0); sleep_ms(250);
    }
    return 0;
}
