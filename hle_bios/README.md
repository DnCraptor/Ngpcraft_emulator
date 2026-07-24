# Clean-room HLE BIOS image

A tiny, **clean-room** Neo Geo Pocket BIOS image that lets the emulator run
games **out of the box, with no `bios.bin`**. It contains no SNK code or data:
only the hardware/SDK *contract* (vector tables, system-call return values, an
open-source font) reproduced from public documentation and the differential
behaviour of a real BIOS used purely as an oracle.

When no real `bios.bin` is present the emulator loads `bios_hle.bin` instead.
If a real BIOS *is* present it is used (maximum fidelity); the two are selected
at load time, so the legacy path is byte-for-byte unchanged.

## What it does (and doesn't)

- **Runs the games.** 71/73 of the commercial corpus render, matching the real
  BIOS on every playable game (byte-identical output on the large majority).
- **No UI / boot animation.** The NEO·GEO POCKET intro and the language/clock
  setup wizard are intentionally omitted — that is the whole point of the HLE.
  Date/time/colour are meant to become emulator settings.
- **No link cable in single BIOS mode** beyond letting COM-polling games proceed
  as "cable idle". Real 2-player still uses a real `bios.bin`.

## Layout (64 KiB, linked at 0xFF0000)

| region | contents |
|---|---|
| `0xFF0000` `f_code` | boot glue, swi trap, ISR trampolines, syscall stubs, font |
| `0xFFFE00` `SYSCALL_TBL` | 32 pointers, indexed by `RW3` (the SDK `SYSTEM_CALL` path) |
| `0xFFFF00` `INT_VECTOR` | 60 CPU hardware vectors (reset / swi / IRQ) |

Boot preloads the font into CHAR RAM (`0xA000`) and seeds the user vector table
anchor at `0x6FB8` that the core scans for. Each hardware IRQ trampolines to the
game's own handler in the user table, preserving every register via a BIOS
scratch word (no unsupported `EX (XSP)` / indirect `CALL`).

## Implemented system calls

Both entry paths are handled: the SDK `SYSTEM_CALL` wrapper (`calr`) *and* a
direct `swi 1` both dispatch through `SYSCALL_TBL`. Each stub returns exactly
what games poll for:

- **RTCGET** (`0x02`) — copies the 7 packed-BCD clock bytes into `XHL3`.
- **INTLVSET** (`0x04`) — sets the interrupt source's priority in the `INTxx`
  I/O registers: `nibble = 0x08 | min(level,5)`, source→register from a table
  (even source = low nibble, odd = high). The `0x08` enable bit was measured off
  the retail BIOS on five games; the Python reference model omits it.
- **COMGETDATA** (`0x14`) — returns `RA3=1` ("no data") so serial drain loops exit.
- **COMSENDSTATUS / COMRECIVESTATUS** (`0x17`/`0x18`) — `RWA3=0` (no error, no data).
- everything else — no-op (correct for CLOCKGEARSET / GEMODESET, harmless otherwise).

Verified against a real BIOS on the 73-ROM corpus: **73/73 deterministic**, and
**72/73 render ≥85% pixel-identical** (most are byte-identical). The one
exception is a homebrew that expects the BIOS first-boot setup wizard — the UI
this HLE intentionally omits.

## Build

Needs the official Toshiba chain (`THOME=C:\t900`) and Pillow (font). Then:

```sh
bash build.sh        # gen_crt0.py -> asm900 -> tulink -> tuconv -> pack -> bios_hle.bin
```

`gen_crt0.py` generates `src/crt0.asm` (vectors, stubs, and the pre-expanded
2bpp font). `pack_bios.py` turns the linker's S-record into the flat 64 KiB
image. `tests/test_hle_bios_image.py` guards the structure and a deterministic
boot without needing the toolchain.
