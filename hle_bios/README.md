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
  Date/time/colour are emulator settings instead: the console's **language** and the
  **clock** (including a *Set by hand* mode, which is this image's replacement for the
  BIOS date screen) live in Settings ▸ Console (BIOS). With a REAL BIOS those same
  settings step aside: that image has a setup screen, and what the player sets there is
  written to the coin cell and wins.
- **Saves work**, and **the link cable works** — both are real drivers here, not
  stubs (see below). Two consoles on this image exchange bytes at the same rate as
  on the retail BIOS, and the six games that save at boot leave a byte-identical
  cartridge.
- **What is not reproduced** is the retail BIOS's *internal* state layout. A game
  that polls BIOS work RAM directly instead of calling the API sees ours, not SNK's.

### Games that fingerprint the BIOS (`core/bios_fingerprint.py`)

**Metal Slug 2nd Mission** checks that the console booted from the BIOS: it sweeps
CHAR RAM (`0xA000..0xC000`) for 64 bytes of the retail boot-time contents, of which
it carries its own copy at `0x28DCC4`. Miss, and it wipes the magic `"MET2"` at
`0x6A88`; a routine then zeroes the key configuration at `0x46DC/DD` every other
frame, so `and A,<mask>` is always `and A,0`. **The game runs, it looks perfect,
and shoot and jump never fire again** — an anti-piracy punishment built to be
mistaken for an emulator bug.

Those 64 bytes are SNK glyphs, and the check is literally a demand for SNK's own
expression, so this image ships none of it: **the bytes are taken out of the
player's cartridge** at hand-off and put in the player's CHAR RAM. Our code holds
a title, an offset and an address — facts, not data. It is gated on *behaviour*
(nothing is written if the fingerprint is already there), so under a real
`bios.bin` it is a no-op by construction.

Adding a game is one row in that table. Whether any other game needs one was
measured, not assumed: 30 of the 82-ROM corpus embed a run of the BIOS's boot CHAR
RAM, but planting it changes work RAM or the screen in **exactly one** of them —
Metal Slug 2nd Mission (18 RAM bytes, screen identical: the silent punishment).
The rest merely ship the same SDK font. Bounded claim: 300 frames, no input, one
matched block per game, so a check that fires later or on different data would not
show up.

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
- **INTLVSET** (`0x04`) — sets the interrupt source's priority: `nibble = 0x08 |
  min(level,5)`, source→register from a table (even source = low nibble, odd = high).
  The `0x08` enable bit was measured off the retail BIOS on five games; the Python
  reference model omits it. **And the register is not the source of truth — its
  SHADOW in work RAM is** (`0x6C24`, `0x6C25`, `0x6C27`, `0x6C28`, `0x6C2A`,
  `0x6C2B`): the BIOS updates its RAM copy and writes the whole byte out, never
  reading the register back. Measured, ten sources × two levels: with `INTE45` at
  `0xDC` from the hand-off, `INTLVSET(source=1, level=3)` leaves it at **`0xB0`**, not
  `0xBC`. Reading the register back — which is what we used to do — preserves a nibble
  the silicon would have lost, and leaves the shadow permanently zero. Games read that
  shadow: `0x6C27` (Delta Warp, Ganbare Neo Poke-kun, Neo Turf Masters), `0x6C28`
  (Sonic Pocket Adventure, Faselei!, KOF Battle de Paradise).
- **the whole COM group** (`0x10`–`0x1A`) — **the link cable**, a real ring driver:
  `COMINIT` arms both serial vectors in `INTES0` and lowers `IFF` (the hand-off
  leaves it at 7, which masks them), `COMCREATEDATA`/`COMSENDSTART` queue and start
  a transmission, the two serial ISRs move bytes between `SC0BUF` and the rings at
  `0x6C80` (TX) / `0x6CC0` (RX), `COMGETDATA` pops one, `COM*STATUS` reports the
  counts, `COMONRTS`/`COMOFFRTS` drive the handshake line. The **RX count at
  `0x6D01` is ABI**: SDK code reads it directly to know how many bytes to drain.
  ⚠️ The two serial vectors are **cross-wired** versus their SDK names — `0x18`
  ("INTTX0") *receives*, `0x19` ("INTRX0") *transmits*, which is what the retail
  BIOS's own handlers do. Wire them by name and each console receives its own byte.
  ⚠️ The ring counts are read-modify-written **from both sides** — a syscall
  increments what an ISR decrements — so the ring primitives run with interrupts
  masked (`push sr` / `ei 7` / `pop sr`, restoring the caller's mask rather than
  assuming one). Measured over 2000 frames of two-console traffic: 5138 bytes each
  way, 3 in flight, exactly the retail BIOS's figure.
- **FLASHWRITE / FLASHALLERS / FLASHERS / FLASHPROTECT** (`0x06`–`0x09`) — **the
  save**, a real driver rather than a stub: the AMD/Fujitsu command cycles at the
  cart window (`AA`/`55` unlock, `A0` program, `80`+`30` erase block, `80`+`10`
  erase chip, `9A`+`9A` protect), with the block NUMBER translated through the
  card's own table, chosen by that card's type byte. **Both slots**: card 0 at
  `0x200000` and card 1 at `0x800000`. That second window is the development slot on
  a 2 MiB cartridge — but a **4 MiB cartridge is two dies and the second one lives
  exactly there**. Measured: with Metal Slug 2nd Mission, Densha de Go! 2 or SvC Match
  of the Millennium in the slot, the retail BIOS writes `0x6C59 = 3`. This driver used
  to refuse card 1 outright, which failed a save on precisely the carts that have two
  chips to save on. The test is now whether the slot holds a cartridge — which is what
  the card-type byte says — not what number it is.
- **SHUTDOWN** (`0x00`) — every interrupt masked, then `halt`: that *is* the console
  switched off as far as the core is concerned.
- **SYSFONTSET** (`0x05`, 17 games) — copies the 256 system characters to CHAR RAM at
  `0xA000`, honouring the palette codes in `RA3` (low nibble glyph, high nibble
  background). The stored font is 2bpp with glyph = 3 / background = 0, so every
  2-bit field is `11` or `00` and the source byte *is* the glyph mask — the recolour
  is `(src AND fg*0x55) OR (NOT src AND bg*0x55)`, no lookup table.
  Games index the font BY ASCII CODE: the tile number written into the tilemap is
  the character itself, so tile `0x50` has to be a `P`. Getting the font wrong is
  therefore not cosmetic — it is a menu nobody can read. See **The font** below.
- **ALARMSET / ALARMDOWNSET** (`0x09`/`0x0B`) — the RTC alarm: `QC3` day, `RB3` hour,
  `RC3` minute, `0xFF` meaning "any". The chip has no wildcard except day 0 = every
  day, so the SDK's `0xFF` is normalised here as the retail BIOS does; arming also
  unmasks INT0, or the alarm would fire into a masked pin.
- **FLASHPROTECT** (`0x0D` — **not 9, 9 is ALARMSET**) — `RB3` first block, `RC3` card
  type, `RD3` count, per SYSTEM.INC's own ABI.
- **GEMODESET** (`0x0E`, 19 games) — K1GE/K2GE display mode. The mode register is
  write-protected; `0xAA`→`0x87F0` opens it and `0x55` closes it, which is why a plain
  store does nothing and this has to be a real routine.
- **CLOCKGEARSET** (`0x01`) — the most-called vector of all (49 games) and a
  **deliberate** no-op: this core runs at a fixed 6.144 MHz and models no clock gear,
  so there is nothing to set. Answering it any other way would invent a machine we do
  not emulate.
- everything else — no-op.

### What the console tells the cartridge (and where that comes from)

Three things are the CONSOLE's, not the game's, and on hardware the BIOS setup wizard
plus the coin cell decide them. We skip that wizard, so they are handed over at the
hand-off — the same mechanism as `XSP` or the card type, and they work identically
under the real BIOS and under this image:

| | byte | who sets it |
|---|---|---|
| **Language** | `0x6F87` — 0 Japanese, 1 English | Settings ▸ *Cartridge language* |
| **Clock** | RTC registers `0x90`–`0x97` | Settings ▸ *Clock* (hardware / host / paused) |
| **Which console a mono cart is in** | `0x6F91` — `0x10` NGPC, header value on NGP | Settings ▸ *Monochrome mode* |

⚠️ **The language one was silently wrong for everyone.** `0x6F87` is read by **24 games
of the corpus**, and nobody was writing it — so it read 0, and 0 is Japanese. Every
bilingual cartridge ran in Japanese by default, never by choice. Four of the six
`(En,Ja)` titles change what they draw when it is flipped (Baseball Stars in both
regions, Puyo Pop, Neo Geo Cup '98); the other two agree for the first 420 frames.

Measured for the other two: the RTC advances with emulated time and matches the real
BIOS second for second, host-clock mode lands on the host's date exactly, and on the
four monochrome cartridges of the corpus this image renders **98.7–100 % pixel-identical
to the real BIOS**. Flipping to NGP changes the picture on Samurai Shodown (63.8 %) —
the one colour-aware mono game — and on nothing else, which is what it should do.

### What the legacy BIOS still has that this does not

Measured, not assumed — a read log over the syscall table and over BIOS work RAM,
across 90 ROMs, discriminating the *reader's PC* so BIOS code reading its own state is
not mistaken for a game reading it:

- **every syscall the corpus actually calls is answered** (census: 18 distinct vectors
  used at boot; the five never called are SHUTDOWN, FLASHALLERS, ALARMSET,
  ALARMDOWNSET, FLASHPROTECT — implemented anyway).
- **171 bytes of BIOS work RAM still differ** (174 before the INTLVSET shadows landed).
  Tracked down rather than counted:
  - `0x6C24`–`0x6C2B` — the INTLVSET shadows. **Closed this pass**; the only ROM still
    differing is `game.ngp`, which under a real BIOS is sitting in the setup wizard.
  - `0x6C27`/`0x6C28` were the bytes cartridge code reads most — and the write log
    says the **games themselves** write them (Delta Warp `0x0B`/`0xBB`, Sonic `0x05`),
    identically under both BIOSes. They are the same shadow bytes, maintained by hand
    by games that drive `INTxx` directly instead of calling INTLVSET.
  - `0x6C16`–`0x6C22`, `0x6DA2` — counters the retail BIOS updates every frame
    (`0xFF218F`, `0xFF21C4`, `0xFF2B29`…). **No cartridge code reads them** in the
    whole corpus, so they are left alone rather than cargo-culted.
  - `0x6FE4`–`0x6FED` — user-vector slots the retail BIOS fills with its own serial
    handlers. We own those vectors directly instead; by design, and the link works.
  - `0x6C40`–`0x6C4B` — scratch, each side its own.
  - a `0x6D0A`–`0x6D25` block one COM-heavy game polls: the retail BIOS's internal
    serial state, not part of any documented API. Not reproduced.
- Pixel parity against the real BIOS: **89/90 at ≥85%**, the one exception being the
  homebrew that expects the setup wizard.

> ⛔ **Those four used to fall through to the default `ret`, and that is worse than
> it sounds.** A bare `ret` leaves `RA3` holding the CARD NUMBER the caller passed
> (`0`) — which reads back as `SYS_SUCCESS`. So every save under the HLE image
> reported success and wrote **nothing**: the player loses a file and is never told.
> Measured both ways, before and after (`tests/test_hle_bios_flash.py`).
>
> The same pass fixed the `swi 1` dispatcher, which did its table lookup in `XHL`
> and parked its return address in `XDE` — **both of which are arguments**
> (`FLASHWRITE` takes the source in `XHL3` and the offset in `XDE3`, `RTCGET` its
> buffer in `XHL3`). It now uses `XIX`, as the SDK wrapper does.

Gates for the save path: the six commercial games that initialise their save area at
boot leave a **byte-identical cartridge image** under the HLE image and under the
real BIOS — three of them really do erase+program, Tsunagete Pon! 2 writing 1848
bytes — and the boot sweep is unchanged (**88/90** rendering, real BIOS 89/90).

Verified against a real BIOS on the 73-ROM corpus: **73/73 deterministic**, and
**72/73 render ≥85% pixel-identical** (most are byte-identical). The one
exception is a homebrew that expects the BIOS first-boot setup wizard — the UI
this HLE intentionally omits.

## Build

Needs the official Toshiba chain (`THOME=C:\t900`). Then:

```sh
bash build.sh        # gen_crt0.py -> asm900 -> tulink -> tuconv -> pack -> bios_hle.bin
```

`gen_crt0.py` generates `src/crt0.asm` (vectors, stubs, and the pre-expanded
2bpp font, read from the tracked `font_2bpp.bin`). `python gen_crt0.py
--regen-font` rewrites that `.bin` from `font_glyphs.py` and so changes the
shipped image. `pack_bios.py` turns the linker's S-record into the flat 64 KiB
image. `tests/test_hle_bios_image.py` guards the structure and a deterministic
boot without needing the toolchain; `tests/test_hle_bios_font.py` guards the
font itself — see below.

## The font

`font_glyphs.py` holds the 96 printable characters **written out pixel by
pixel**, 5 wide by 7 tall inside the 8x8 cell, one column of left bearing and
the eighth row left clear so lines never touch. `gen_crt0.py` packs them into
`font_2bpp.bin`.

Two things about it are worth knowing, because both were once wrong and both
produced menus that were almost letters and completely unreadable.

**A CHAR-RAM row is a little-endian 16-bit word.** The byte at the LOWER address
carries the RIGHT half of the row. Writing the halves the other way round shows
every glyph with its own left and right sides exchanged. The retail BIOS's own
`P` (tile `0x50`) is the reference: `f0 3f | 0c 30 | 0c 30 | f0 3f | 00 30 ...`.

**It is a table, not a rasteriser.** It used to be rendered with
`ImageFont.load_default()` — a face about eleven pixels tall — into an 8x8 box,
so every glyph lost its bottom stroke: `E` came out as an `F` and `L` as a bare
vertical bar. That also made the SHIPPED IMAGE depend on which Pillow version
the build machine had installed, which quietly defeats the checksum the Libretro
build pins it with. There is no font dependency any more.

⚠️ The image's own tests passed through both faults. They checked its size, its
checksum, its syscall table and its vectors — everything except whether the font
spelt anything. `tests/test_hle_bios_font.py` is the missing check: glyph shapes,
the byte order, every capital distinct, and a fixture of the broken glyph so the
instrument proves it can fire.
