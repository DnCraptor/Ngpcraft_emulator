// platform/board.cpp — RP2350 board bring-up for the NGP firmware.
//
// The flash QE-fix + XIP-timing block below is lifted VERBATIM from
// pico-speccy (src/main.cpp), the platform reference. The only edits are two
// de-ZX substitutions: Config::max_flash_freq -> BOARD_MAX_FLASH_FREQ, and
// Debug::fault_log(...) -> removed. Provenance: DnCraptor/pico-speccy.
//
// Milestone 0 scope: flash timings valid at 378 MHz + a slim core1 render loop.
// PSRAM bring-up (butter/QMI + SPI) is intentionally NOT here yet — it belongs
// with Seam 1 (cart ROM in PSRAM); the emulator is not driven at this milestone.

#include <pico/stdlib.h>
#include <pico/multicore.h>
#include <pico/sem.h>
#include <pico/bootrom.h>
#include <hardware/vreg.h>
#include <hardware/clocks.h>
#include <hardware/sync.h>
#include <hardware/flash.h>
#include <hardware/xip_cache.h>
#include <hardware/regs/qmi.h>
#include <hardware/structs/qmi.h>
#include <hardware/structs/xip.h>
#include <hardware/regs/pads_qspi.h>
#include <hardware/structs/pads_qspi.h>
#include <hardware/regs/addressmap.h>

#include "board.h"
extern "C" {
#include "graphics.h"
}

// Nominal profile. 504 MHz overclock profile arrives with the Clock menu item.
#ifndef CPU_MHZ
#define CPU_MHZ 378
#endif
// Max flash SCK the timing model targets (pico-speccy ships 66). Was Config::max_flash_freq.
#define BOARD_MAX_FLASH_FREQ 66

// Flash JEDEC read buffer (filled by flash_info(), tested by flash_qe_fix()).
uint8_t rx[4] = { 0 };

// core1 scanout gate + a one-shot "graphics is up" signal for core0.
struct semaphore vga_start_semaphore;
struct semaphore graphics_ready_semaphore;

extern "C" void hdmi_poll_reinit(void);

// (sigbus/dummy_panic fault handlers omitted here — not registered at Milestone 0)

// QMI M0 (flash) timing for a given sys_clk: CLKDIV keeps SCK under
// BOARD_MAX_FLASH_FREQ, RXDELAY (half sys-clock units) places the read sample.
static void __not_in_flash_func(flash_timing_for)(int mhz, int* divisor_out, int* rxdelay_out) {
        const int max_flash_freq = BOARD_MAX_FLASH_FREQ * MHZ;
        const int clock_hz = mhz * MHZ;
        int divisor = (clock_hz + max_flash_freq - 1) / max_flash_freq;
        if (divisor == 1 && clock_hz > 100000000) {
            divisor = 2;
        }
        int rxdelay = divisor;
        if (clock_hz / divisor > 100000000) {
            rxdelay += 1;
        }
        *divisor_out = divisor;
        *rxdelay_out = rxdelay;
}

static inline void flash_timing_write(int divisor, int rxdelay) {
        qmi_hw->m[0].timing = 0x60007000 |
                            rxdelay << QMI_M0_TIMING_RXDELAY_LSB |
                            divisor << QMI_M0_TIMING_CLKDIV_LSB;
}

void __not_in_flash() flash_timings(int mhz) {
        int divisor, rxdelay;
        flash_timing_for(mhz, &divisor, &rxdelay);
        flash_timing_write(divisor, rxdelay);
}

// Flash timing to hold ACROSS a sys_clk change (from_mhz -> to_mhz).
//
// set_sys_clock_khz()/try_set_sys_clock_khz() live in flash and fetch their own
// instructions through XIP while the PLL is being reprogrammed, so whatever
// M0 timing is in force at that moment has to read correctly at BOTH clocks.
// Neither steady-state value does: the boot2 default (CLKDIV 2, RXDELAY 2) is
// far out of spec at 378 MHz, and our flash_timings(378) (CLKDIV 6, RXDELAY 6
// at max_flash_freq 66) puts the sample point a full half-SCK late at the
// 150 MHz boot clock — RXDELAY is an ABSOLUTE delay in half sys-clock units
// (pad round trip + flash clock-to-Q), and "rxdelay = divisor" scales it with
// the divider instead: 7.9 ns at 378 MHz becomes 20 ns at 150 MHz, i.e. the
// sample lands on the SCK edge where the flash launches the next nibble.
// Whether that fetch returns garbage depends on the chip, its temperature and
// what the XIP cache happens to hold: UNDEFINSTR -> HardFault -> lockup, on
// one board and not another. SpeccyP hit exactly this (SWD-diagnosed inside
// set_sys_clock_pll, commit 8809b41) and fixed it with a fixed CLKDIV 4 /
// RXDELAY 2 for the transition.
//
// Ours is derived instead of hard-coded: take the steady-state numbers for the
// HIGHER of the two clocks (so SCK never exceeds max_flash_freq at either end)
// and DOUBLE the divider while keeping RXDELAY. Halving SCK moves the nominal
// sample point a whole SCK period away from the launching edge at the target
// clock and puts it mid-window at the slower one; RXDELAY stays the same
// absolute delay, which is what it compensates. Worked examples (max 66):
//   150->378: CLKDIV 12 RXDELAY 6 — sample 60 ns into a 80 ns SCK period at 150,
//             23.8 ns into 31.7 ns at 378; data valid ~[10, period+6] at both.
//   378->504: CLKDIV 16 RXDELAY 8; 378->252: CLKDIV 12 RXDELAY 6.
// SCK is slow (12-31 MHz) only for the few hundred us of the switch itself;
// callers restore flash_timings(actual_mhz) right after.
void __not_in_flash_func(flash_timings_transition)(int from_mhz, int to_mhz) {
        int divisor, rxdelay;
        flash_timing_for(from_mhz > to_mhz ? from_mhz : to_mhz, &divisor, &rxdelay);
        divisor *= 2;
        if (divisor > (int)(QMI_M0_TIMING_CLKDIV_BITS >> QMI_M0_TIMING_CLKDIV_LSB))
            divisor = QMI_M0_TIMING_CLKDIV_BITS >> QMI_M0_TIMING_CLKDIV_LSB;
        flash_timing_write(divisor, rxdelay);
}

static void __not_in_flash_func(flash_info)() {
    if (rx[0] == 0) {
        uint8_t tx[4] = {0x9f};
        flash_do_cmd(tx, rx, 4);
    }
}

// Flash QE bit fix for Puya flash on RP2350 boards (see fhoedemakers/flash_config).
// Puya ships with SR2.QE=0 and its 01h command writes SR1 only, so boot2's
// Winbond-style 2-byte 01h status write silently fails — quad XIP keeps sampling
// WP#/HOLD# and locks up under overclock. Must run BEFORE flash_timings()/
// set_sys_clock — the overclock is what triggers the lockup.
//
// The whole command sequence runs inside ONE exit-XIP window. Per the PY25Q128HA
// datasheet, Volatile SR Write Enable (50h) must be IMMEDIATELY followed by the
// Write Status Register command — no other flash commands in between. Chaining
// flash_do_cmd() calls violates that: each call's epilogue re-runs boot2, which
// itself talks to the flash (SR2 check + its own Winbond-style SR write attempt),
// clearing the volatile-WE latch — hw-confirmed as "FIX FAILED" on ZERO2.
// Preferred path is the volatile SR2 write (instant, zero wear, re-applied each
// boot); if the chip ignores it, fall back to a one-time NON-volatile write
// (06h + 31h + WIP poll — the fhoedemakers-proven sequence), then as a last
// resort the Winbond-style 2-byte 01h write (SR1+SR2) some Puya parts need.
// Codes (0 = nothing to report — callers test `if (flash_qe)`; >= 4 = failure,
// Hardware Info appends the flash_qe_diag dump for those):
//   0 = n/a (not Puya)          1 = QE already set        2 = set, volatile 50h+31h
//   3 = set, non-volatile 31h   4 = FIX FAILED (QE still 0 / WEL never latched)
//   5 = set, non-volatile 01h   6 = raw exit-XIP window self-test failed
uint8_t flash_qe = 0;

// Successes print as the raw code ("01h".."05h", legend above); failures as
// words so they stand out in Hardware Info and the boot log.
const char* flash_qe_text() {
    static const char* const kText[] = {
        "00h", "01h", "02h", "03h", "04h", "05h", "06h",
    };
    return flash_qe < count_of(kText) ? kText[flash_qe] : "00h";
}

// One CS-framed command inside an open exit-XIP window (mirror of the QMI half
// of the SDK's flash_do_cmd). Caller guarantees: XIP exited, IRQs off, and no
// flash-resident code touched until the window is closed.
// NOTE: all three must be __no_inline_not_in_flash_func, NOT __not_in_flash_func:
// the latter permits inlining, and MinSizeRel inlined the whole window body into
// flash-resident main() — executing from (dead) XIP inside the window, hardfault
// at the first XIP-cache miss (hw-traced on ZERO2 with the debug probe).
static void __no_inline_not_in_flash_func(qe_cmd_raw)(const uint8_t *tx, uint8_t *rxb, size_t n) {
    hw_set_bits(&qmi_hw->direct_csr, QMI_DIRECT_CSR_ASSERT_CS0N_BITS);   // CS low
    hw_set_bits(&qmi_hw->direct_csr, QMI_DIRECT_CSR_EN_BITS);
    size_t txr = n, rxr = n;
    while (txr || rxr) {
        uint32_t flags = qmi_hw->direct_csr;
        if (txr && !(flags & QMI_DIRECT_CSR_TXFULL_BITS)) {
            qmi_hw->direct_tx = *tx++;
            --txr;
        }
        if (rxr && !(flags & QMI_DIRECT_CSR_RXEMPTY_BITS)) {
            uint8_t b = (uint8_t)qmi_hw->direct_rx;
            if (rxb) *rxb++ = b;
            --rxr;
        }
    }
    hw_clear_bits(&qmi_hw->direct_csr, QMI_DIRECT_CSR_EN_BITS);
    hw_clear_bits(&qmi_hw->direct_csr, QMI_DIRECT_CSR_ASSERT_CS0N_BITS); // CS high
}

static uint8_t __no_inline_not_in_flash_func(qe_read_reg)(uint8_t cmd) {
    uint8_t tx[2] = { cmd, 0 }, rxb[2];
    qe_cmd_raw(tx, rxb, 2);
    return rxb[1];
}

// Diagnostics captured inside the window, logged afterwards:
// [0]=in-window JEDEC MF (self-test vs rx[1]) [1]=SR1 [2]=SR2 initial
// [3]=SR2 after volatile try [4]=SR1 after 06h (WEL check) [5]=SR2 final
uint8_t flash_qe_diag[6];

static void __no_inline_not_in_flash_func(flash_qe_fix)() {
    if (rx[1] != 0x85)                       // Puya only; Winbond is factory-set,
        return;                              // other vendors have different SR layouts
    // boot2 copy to re-enter fast XIP afterwards (on RP2350 crt0 parks boot2 in BOOTRAM)
    static uint32_t boot2_copy[64];
    const volatile uint32_t *b2 = (const volatile uint32_t *)BOOTRAM_BASE;
    for (int i = 0; i < 64; ++i)
        boot2_copy[i] = b2[i];
    __compiler_memory_barrier();

    rom_connect_internal_flash_fn connect_flash =
        (rom_connect_internal_flash_fn)rom_func_lookup_inline(ROM_FUNC_CONNECT_INTERNAL_FLASH);
    rom_flash_exit_xip_fn exit_xip =
        (rom_flash_exit_xip_fn)rom_func_lookup_inline(ROM_FUNC_FLASH_EXIT_XIP);
    rom_flash_flush_cache_fn flush_cache =
        (rom_flash_flush_cache_fn)rom_func_lookup_inline(ROM_FUNC_FLASH_FLUSH_CACHE);

    // ROM's exit_xip resets the QMI CS1 window to the clean 03h config — save and
    // restore it like the SDK does (harmless this early: psram_init runs later,
    // but keeps this function safe to call at any point).
    uint32_t m1_timing = qmi_hw->m[1].timing;
    uint32_t m1_rcmd   = qmi_hw->m[1].rcmd;
    uint32_t m1_rfmt   = qmi_hw->m[1].rfmt;
    uint32_t pads_save[count_of(pads_qspi_hw->io)];
    for (size_t i = 0; i < count_of(pads_qspi_hw->io); ++i)
        pads_save[i] = pads_qspi_hw->io[i];

    const uint32_t ints = save_and_disable_interrupts();
    connect_flash();
    exit_xip();
    // Pull SD2 (WP#) and SD3 (HOLD#) high during the window: with QE=0 the chip
    // interprets them as control pins, and in serial direct mode the QMI leaves
    // them undriven. A floating/low WP# + SRP0 hardware-protects the status
    // registers — every SR write is then silently ignored (pads are io[3]/io[4]:
    // SCLK, SD0, SD1, SD2, SD3, SS).
    hw_write_masked(&pads_qspi_hw->io[3], PADS_QSPI_GPIO_QSPI_SD2_PUE_BITS,
                    PADS_QSPI_GPIO_QSPI_SD2_PUE_BITS | PADS_QSPI_GPIO_QSPI_SD2_PDE_BITS);
    hw_write_masked(&pads_qspi_hw->io[4], PADS_QSPI_GPIO_QSPI_SD3_PUE_BITS,
                    PADS_QSPI_GPIO_QSPI_SD3_PUE_BITS | PADS_QSPI_GPIO_QSPI_SD3_PDE_BITS);
    // ---- window open: only qe_* helpers below (all RAM-resident) ----
    // Self-test: re-read JEDEC ID through our raw path; must match flash_info()'s.
    uint8_t jtx[4] = { 0x9f, 0, 0, 0 }, jrx[4];
    qe_cmd_raw(jtx, jrx, 4);
    flash_qe_diag[0] = jrx[1];
    flash_qe_diag[1] = qe_read_reg(0x05);
    uint8_t sr2 = qe_read_reg(0x35);
    flash_qe_diag[2] = sr2;
    if (jrx[1] != rx[1] || jrx[2] != rx[2] || jrx[3] != rx[3]) {
        flash_qe = 6;                                     // raw window broken — don't write anything
    } else if (sr2 & 0x02) {
        flash_qe = 1;                                     // QE already set
    } else {
        uint8_t wr31[2] = { 0x31, (uint8_t)(sr2 | 0x02) };
        const uint8_t c50 = 0x50, c06 = 0x06;
        qe_cmd_raw(&c50, NULL, 1);                        // Volatile SR Write Enable
        qe_cmd_raw(wr31, NULL, 2);                        // Write SR2 (volatile copy)
        for (volatile int i = 0; i < 2000; ++i);          // settle (volatile write is ~instant)
        flash_qe_diag[3] = qe_read_reg(0x35);
        if (flash_qe_diag[3] & 0x02) {
            flash_qe = 2;
        } else {
            // Volatile write ignored — one-time non-volatile write instead.
            qe_cmd_raw(&c06, NULL, 1);                    // Write Enable (WEL)
            flash_qe_diag[4] = qe_read_reg(0x05);
            if (flash_qe_diag[4] & 0x02) {                // WEL latched?
                qe_cmd_raw(wr31, NULL, 2);
                for (int i = 0; i < 20000; ++i)           // WIP poll, tW max ~12 ms
                    if (!(qe_read_reg(0x05) & 0x01))
                        break;
                flash_qe = (qe_read_reg(0x35) & 0x02) ? 3 : 4;
                if (flash_qe == 4) {
                    // Last resort: Winbond-style 2-byte 01h write (SR1+SR2) —
                    // some Puya parts route SR2 only through this form.
                    uint8_t wr01[3] = { 0x01, qe_read_reg(0x05), (uint8_t)(sr2 | 0x02) };
                    wr01[1] &= (uint8_t)~0x03;            // don't write back WIP/WEL
                    qe_cmd_raw(&c06, NULL, 1);
                    qe_cmd_raw(wr01, NULL, 3);
                    for (int i = 0; i < 20000; ++i)
                        if (!(qe_read_reg(0x05) & 0x01))
                            break;
                    flash_qe = (qe_read_reg(0x35) & 0x02) ? 5 : 4;
                }
            } else {
                flash_qe = 4;
            }
        }
    }
    flash_qe_diag[5] = qe_read_reg(0x35);
    // ---- close window: flush cache, re-enter fast XIP via boot2 ----
    flush_cache();
    ((void (*)(void))((intptr_t)boot2_copy + 1))();
    qmi_hw->m[1].timing = m1_timing;
    qmi_hw->m[1].rcmd   = m1_rcmd;
    qmi_hw->m[1].rfmt   = m1_rfmt;
    for (size_t i = 0; i < count_of(pads_qspi_hw->io); ++i)
        pads_qspi_hw->io[i] = pads_save[i];
    restore_interrupts(ints);
}

// ── entry points ─────────────────────────────────────────────────────────────

// Runs on core0 before core1 launch: flash QE fix, regulator, clock -> CPU_MHZ
// (falls back to 252 MHz if the PLL will not lock), flash timings for the result.
void board_init(void) {
    flash_info();
    flash_qe_fix();

    vreg_disable_voltage_limit();
    vreg_set_voltage(VREG_VOLTAGE_1_60);
    sleep_ms(100);                          // regulator settles before the overclock

    const int boot_mhz = clock_get_hz(clk_sys) / MHZ;
    int applied = CPU_MHZ;
    flash_timings_transition(boot_mhz, applied);   // valid at both clocks during the switch
    if (!set_sys_clock_khz(CPU_MHZ * KHZ, 0)) {
        applied = 252;                      // failsafe if the PLL does not lock
        flash_timings_transition(boot_mhz, applied);
        set_sys_clock_khz(applied * KHZ, 1);
    }
    flash_timings(applied);                 // steady-state timing for the active clock
}

// core1 entry. graphics_init() arms the VGA/HDMI PIO+DMA scanout; we signal core0
// it may draw, then wait for the start gate and service the display in a loop.
void __scratch_x("render") render_core(void) {
    multicore_lockout_victim_init();
    graphics_init();
    graphics_set_bgcolor(0x000000);
    sem_release(&graphics_ready_semaphore);   // core0 may set mode / draw now
    sem_acquire_blocking(&vga_start_semaphore);
    while (true) {
#ifdef VGA_HDMI
        hdmi_poll_reinit();
#endif
        tight_loop_contents();
    }
}
