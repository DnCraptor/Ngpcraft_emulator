# == DO NOT EDIT THE FOLLOWING LINES for the Raspberry Pi Pico VS Code Extension to work ==
if(WIN32)
    set(USERHOME $ENV{USERPROFILE})
else()
    set(USERHOME $ENV{HOME})
endif()
set(sdkVersion 2.3.1)
set(toolchainVersion 15_2_Rel1)
set(picotoolVersion 2.3.1)
set(picoVscode ${USERHOME}/.pico-sdk/cmake/pico-vscode.cmake)
if (EXISTS ${picoVscode})
    include(${picoVscode})
endif()
# ====================================================================================
cmake_minimum_required(VERSION 3.13)

# ── NGP on RP2350 — firmware build skeleton (MURM2 + VGA_HDMI) ────────────────
# Platform layer vendored from DnCraptor/pico-speccy (see drivers/, src/boards/,
# external/PicoDVI). Emulation core = cpp/src, driven through the flat C ABI in
# cpp/include/ngpc_core.h.
#
# ⚠ FIRST DRAFT — not compile-verified in the authoring environment (no
# arm-none-eabi / pico-sdk there). Expect to iterate build errors via patches.
# Milestone 0 for this file: the vendored platform links and puts text on the
# display. The emulator is linked (ngpc_core) but NOT yet driven — that is the
# job of the three seam patches that follow (memory / video / input+audio).

set(PICO_PLATFORM rp2350)
set(PICO_BOARD_HEADER_DIRS ${CMAKE_SOURCE_DIR}/src/boards)
set(PICO_BOARD picospeccy_rp2350b CACHE STRING "Board type" FORCE)

set(CPU_MHZ 378)            # nominal profile; 504 MHz = overclock (menu, later)
set(BUTTER_PSRAM_GPIO 8)    # MURM2

# Output naming mirrors pico-speccy: <board>-<project>-<display>-<version>, so the
# .uf2 name states the target hardware. BOARD_TAG follows pico-speccy (m2p2=MURM2).
# TODO: turn BOARD_TAG/pins into a board matrix so every pico-speccy board builds.
set(BOARD_TAG   m2p2)       # MURM2 / RP2350B
set(PORT_VERSION 0.1.0)
set(BUILD_NAME "${BOARD_TAG}-ngp-VGA-HDMI-${PORT_VERSION}")

include(pico_sdk_import.cmake)

set(PROJECT_NAME ngp_rp2350)
project(${PROJECT_NAME} C CXX ASM)
set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

pico_sdk_init()

# One clean output dir for every artifact. NOTE: RUNTIME_OUTPUT_DIRECTORY steers
# .elf/.bin/.hex here, but pico_add_extra_outputs writes the .uf2 into the build
# tree regardless, so it is relocated with a POST_BUILD copy below.
set(OUTPUT_DIR "${CMAKE_SOURCE_DIR}/bin")
set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY "${OUTPUT_DIR}")
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY "${OUTPUT_DIR}")
set(CMAKE_LIBRARY_OUTPUT_DIRECTORY "${OUTPUT_DIR}")

# Stack layout / guards — verbatim from pico-speccy MURM2 (core0 4 KB fills the
# empty SCRATCH_Y; core1 pinned 2 KB; MSPLIM guards turn any overflow into a
# fault at the boundary instead of silent neighbour-stack corruption).
add_compile_definitions(PICO_STACK_SIZE=0x2000 PICO_CORE1_STACK_SIZE=0x800
                        PICO_USE_STACK_GUARDS=1
                        CPU_MHZ=${CPU_MHZ})

# ── vendored platform drivers ────────────────────────────────────────────────
# picomp3lib intentionally omitted (MP3 decoder — ZX/NeoGS side, unused by NGP).
add_subdirectory(drivers/audio)
add_subdirectory(drivers/nespad)
add_subdirectory(drivers/fatfs)
add_subdirectory(drivers/sdcard)
add_subdirectory(drivers/vga-nextgen)
add_subdirectory(drivers/st7789)
add_subdirectory(drivers/hdmi)
add_subdirectory(drivers/tv)
add_subdirectory(drivers/tv-software)
add_subdirectory(drivers/graphics)
add_subdirectory(drivers/psram)
add_subdirectory(drivers/ps2)

# ── emulation core (flat C ABI, no OS deps: only std::vector/deque/algorithm) ─
add_library(ngpc_core STATIC
    cpp/src/core.cpp
    cpp/src/memory.cpp
    cpp/src/execute.cpp
    cpp/src/mem_family.cpp
    cpp/src/reg_family.cpp
    cpp/src/z80.cpp
    cpp/src/apu.cpp
    cpp/src/render.cpp)
target_include_directories(ngpc_core PUBLIC cpp/include PRIVATE cpp/src)
target_compile_options(ngpc_core PRIVATE -fno-exceptions -fno-rtti -O2)
# ⚠ SEAM 1 NOT YET DONE: Machine() allocates a flat 16 MB `mem` vector, which
# cannot fit RP2350 SRAM — ngpc_create() will fail until read8/write8 are moved
# to region dispatch (RAM/VRAM/IO/BIOS in SRAM, cart ROM in PSRAM). That is why
# Milestone 0 links the core but does not call it.

# ── firmware target ──────────────────────────────────────────────────────────
add_executable(${PROJECT_NAME}
    platform/main.cpp
    platform/board.cpp
    platform/video_hooks.c)

# MURM2 pin map + VGA_HDMI display path — from pico-speccy IF(MURM2) block.
target_compile_definitions(${PROJECT_NAME} PRIVATE
    MURM2=1
    KBD_CLOCK_PIN=2 KBD_DATA_PIN=3
    BEEPER_PIN=9 PWM_PIN0=10 PWM_PIN1=11
    SDCARD_PIN_SPI0_CS=5 SDCARD_PIN_SPI0_SCK=6 SDCARD_PIN_SPI0_MOSI=7 SDCARD_PIN_SPI0_MISO=4
    PSRAM_SPINLOCK=255 PSRAM_ASYNC=255
    PSRAM_PIN_CS=255 PSRAM_PIN_SCK=255 PSRAM_PIN_MOSI=255 PSRAM_PIN_MISO=255
    NES_GPIO_CLK=20 NES_GPIO_LAT=21 NES_GPIO_DATA=26
    VGA_BASE_PIN=12 HDMI_BASE_PIN=12
    PICO_DEFAULT_LED_PIN=25
    KBDPS2
    VGA_HDMI)
if (BUTTER_PSRAM_GPIO)
    target_compile_definitions(${PROJECT_NAME} PRIVATE BUTTER_PSRAM_GPIO=${BUTTER_PSRAM_GPIO})
endif()

target_link_libraries(${PROJECT_NAME} PRIVATE
    pico_stdlib pico_multicore pico_bootrom
    hardware_pio hardware_dma hardware_interp
    hardware_vreg hardware_clocks hardware_sync hardware_flash
    hardware_pwm hardware_i2c hardware_adc
    graphics vga-nextgen hdmi
    sdcard fatfs psram ps2 nespad audio
    ngpc_core)

# Custom memory map: enlarges the stack region (PICO_STACK_SIZE=0x2000 overflows
# the SDK default SCRATCH_Y) and reserves room for the cart ROM window (Seam 1).
pico_set_linker_script(${PROJECT_NAME} "${CMAKE_SOURCE_DIR}/rp2350-memmap.ld")

set_target_properties(${PROJECT_NAME} PROPERTIES OUTPUT_NAME "${BUILD_NAME}")
message(STATUS "NGP build name: ${BUILD_NAME}")

pico_add_extra_outputs(${PROJECT_NAME})

# pico_add_extra_outputs emits the .uf2 into CMAKE_CURRENT_BINARY_DIR, not OUTPUT_DIR;
# put the flashable image alongside the other artifacts in OUTPUT_DIR.
add_custom_command(TARGET ${PROJECT_NAME} POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy
        "${CMAKE_CURRENT_BINARY_DIR}/${BUILD_NAME}.uf2"
        "${OUTPUT_DIR}/${BUILD_NAME}.uf2"
    VERBATIM)
