// platform/emu.cpp — Seam 1 bring-up: load a cartridge from SD into PSRAM and
// run it BLIND (no video/input/audio yet). This is the first time the whole
// emulator core actually executes on hardware, so it doubles as the test of the
// memory dispatcher (patch 0013).
//
// GP25 tells you what happened:
//   slow ~1 Hz tick   -> the core is advancing frames normally (good)
//   fast flicker      -> ngpc_run_frames returns immediately every call (a trap:
//                        un-ported opcode / HALT) -- running, but stuck
//   frozen            -> a crash / bad memory access inside a frame
//   0.5 s heartbeat   -> no cartridge found on SD (drop a .ngp/.ngc in the root)

#include <pico/stdlib.h>
#include <string.h>
#include <stdio.h>

#include "board.h"                 // PSRAM_DATA, butter_psram_size()
extern "C" {
#include "ff.h"                    // FatFs
#include "ngpc_core.h"
}

extern "C" const unsigned char bios_hle_data[65536];   // generated from hle_bios/bios_hle.bin

#ifndef PICO_DEFAULT_LED_PIN
#define PICO_DEFAULT_LED_PIN 25
#endif

static bool ext_is_ngp(const char *name) {
    const char *dot = strrchr(name, '.');
    if (!dot) return false;
    char e[6] = {0};
    for (int i = 0; i < 5 && dot[i]; i++) {
        char c = dot[i];
        if (c >= 'A' && c <= 'Z') c += 32;          // lower-case
        e[i] = c;
    }
    return !strcmp(e, ".ngp") || !strcmp(e, ".ngc") || !strcmp(e, ".ngpc");
}

// Reads the first .ngp/.ngc/.ngpc in the SD root directly into `dst` (PSRAM is
// memory-mapped, so f_read can land there). Returns bytes read, 0 on failure.
static uint32_t load_first_rom(uint8_t *dst, uint32_t cap) {
    static FATFS fs;
    if (f_mount(&fs, "SD:", 1) != FR_OK) return 0;

    static DIR dir;
    static FILINFO fno;
    if (f_opendir(&dir, "SD:/") != FR_OK) return 0;

    char path[300];
    path[0] = 0;
    while (f_readdir(&dir, &fno) == FR_OK && fno.fname[0]) {
        if (fno.fattrib & AM_DIR) continue;
        if (ext_is_ngp(fno.fname)) {
            snprintf(path, sizeof path, "SD:/%s", fno.fname);
            break;
        }
    }
    f_closedir(&dir);
    if (!path[0]) return 0;

    static FIL f;
    if (f_open(&f, path, FA_READ) != FR_OK) return 0;
    uint32_t total = 0;
    while (total < cap) {
        UINT br = 0;
        if (f_read(&f, dst + total, 0x10000u, &br) != FR_OK) break;
        if (br == 0) break;
        total += br;
    }
    f_close(&f);
    return total;
}

extern "C" void video_show_trap(uint32_t status, uint32_t pc, uint32_t op, uint32_t frames);
extern "C" void video_init_ngp(void);
extern "C" void video_present_ngp(const uint16_t *src);

extern "C" void emu_run(void) {
    // Two cartridge regions in PSRAM: [0,cap) live window, [cap,2*cap) pristine.
    // cap = half the PSRAM, capped at a 4 MiB cart (a 4 MiB cart needs 8 MiB PSRAM).
    uint32_t psz = butter_psram_size();
    uint32_t cap = psz / 2;
    if (cap > 0x400000u) cap = 0x400000u;
    uint8_t *work     = PSRAM_DATA;
    uint8_t *pristine = PSRAM_DATA + cap;

    ngpc_t *emu = ngpc_create();
    ngpc_set_cart_ram(emu, work, pristine, cap);
    ngpc_load_bios(emu, bios_hle_data, 65536);   // HANDOFF needs the 0xFFFF00 vector table

    // Read the ROM straight into the pristine buffer; ngpc_load_rom then only
    // records length + flash geometry (its copy is skipped when data == pristine).
    uint32_t rom_len = (psz && cap) ? load_first_rom(pristine, cap) : 0;

    if (rom_len == 0 || ngpc_load_rom(emu, pristine, rom_len) != 0) {
        while (true) {                              // no cart: distinct 0.5 s heartbeat
            gpio_put(PICO_DEFAULT_LED_PIN, 1); sleep_ms(500);
            gpio_put(PICO_DEFAULT_LED_PIN, 0); sleep_ms(500);
        }
    }

    ngpc_reset(emu, NGPC_RESET_HANDOFF);

    video_init_ngp();                               // palette + clear

    uint32_t fc = 0;
    while (true) {
        ngpc_summary_t s;
        ngpc_run_frames(emu, 1, 200000u, &s);       // one frame, 200k-instr backstop
        if (s.stop_status != 41 /* NGPC_COUNT_REACHED */ && s.stop_status != 0 /* NGPC_OK */) {
            // Trap (un-ported opcode / HALT): stop spinning, show it on screen.
            video_show_trap(s.stop_status, s.stop_pc, s.stop_opcode, s.frame_count);
            while (true) {                          // slow blink == trapped (see screen)
                gpio_put(PICO_DEFAULT_LED_PIN, 1); sleep_ms(700);
                gpio_put(PICO_DEFAULT_LED_PIN, 0); sleep_ms(700);
            }
        }
        video_present_ngp(ngpc_framebuffer_ptr(emu));   // direct view, no 47 KB copy
        if ((++fc % 30u) == 0) gpio_xor_mask(1u << PICO_DEFAULT_LED_PIN);
    }
}
