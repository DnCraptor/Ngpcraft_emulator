// platform/board.h — RP2350 board bring-up entry points for the NGP firmware.
#pragma once

#include <pico/sem.h>

#ifdef __cplusplus
extern "C" {
#endif

// core1 scanout gate (released by core0 once the splash/frame is ready) and a
// one-shot signal that graphics_init() has completed on core1.
extern struct semaphore vga_start_semaphore;
extern struct semaphore graphics_ready_semaphore;

// core0, before launching core1: flash QE fix + regulator + clock (CPU_MHZ, or
// a 252 MHz failsafe) + flash XIP timings for the resulting clock.
void board_init(void);

// core1 entry: graphics_init() then the display service loop. Pass to
// multicore_launch_core1().
void render_core(void);

#ifdef __cplusplus
}
#endif
