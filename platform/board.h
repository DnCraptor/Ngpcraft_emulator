// platform/board.h — RP2350 board bring-up entry points for the NGP firmware.
#pragma once

#include <stdint.h>
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

// Butter/QSPI PSRAM, brought up inside board_init(). PSRAM_DATA is the XIP-mapped
// base (0x11000000 on butter boards). butter_psram_size() returns usable bytes,
// 0 when no chip is present or PSRAM is disabled. Cart ROM is staged here in Seam 1.
extern uint8_t *PSRAM_DATA;
uint32_t butter_psram_size(void);
uint32_t butter_psram_probed(void);

#ifdef __cplusplus
}
#endif
