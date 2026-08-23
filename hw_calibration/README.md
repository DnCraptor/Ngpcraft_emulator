# CPU-speed calibration ROM — for the "games run 2x too fast" bug  ✅ SOLVED

**Bug (SOLVED):** Cool Boarders Pocket / Densha de Go ran **2x too fast in every
emulator measured, this one included**, vs real hardware. They self-time their frame
rate: on silicon their per-frame work spills past one VBlank -> 30 fps; in emulators
it fits -> 60 fps. So emulators ran the games' work in **fewer cycles than silicon**.
This ROM measured that per instruction class (VBlank = reference clock) and pinned
the cause.

**Result (silicon, v2):** the numbers are **not uniform** — short/fetch-bound ops
~3.4x too fast, MUL/DIV ~2.5x, RASV correct. Signature of **unmodelled cart-flash
FETCH wait-states**. What silicon settled:
- ✅ **`cart_wait=3` (instruction fetch) confirmed** — BASE/SHIFT/ADD/MEM match.
- ⛔ **cart DATA reads are NOT wait-stated** — v2's `CRND == RRND` on silicon means a
  cart data read costs the same as RAM, so **`cart_data_wait=0`**. (An earlier
  `cart_data_wait=5`, curve-fit to Cool Boarders, was **refuted** by this ROM.)
- ✅ **MUL/DIV were under-costed** (silicon 444/265 vs 481/301) — **fixed** in the core.

With the CPU model now silicon-exact on every class, Cool Boarders STILL ran ~51 fps
(vs 30 on silicon) → the residual was **not the CPU**. **v3** then measured VRAM writes and
found the K2GE throttle is real (VWR 452 < MEM 471) — but *not* Cool Boarders' cause, since
that game writes VRAM in vblank. The residual is its per-frame **LDIR** block copy, which is
what **v6** measures (`ldir_cost`, shipped at 14 pending that ROM). See `../DEVLOG.md` and
memory `project_ngpc_emulator_fps_waitstates`.

**Since (2026-08-06):** that block cost turned out to be **two** numbers, not one — the byte
form and the word form are different instructions and the loop is billed per iteration. The
word form is now `ldirw_cost`, shipped at **18**, measured on a homebrew raster oracle to a
one-cycle tolerance. See [v7](#v7--the-word-block-copy-ldirw-and-why-v6-cannot-answer-it).

## What the emulator actually ships today

| Knob | Field default (`Machine`) | Shipped by the shell | Status |
|---|---|---|---|
| `cart_wait` (fetch) | `0` — free fetch, the pre-feature behaviour | **3** (`cfg.CART_FETCH_WAIT`) | ✅ silicon (v1) |
| `cart_data_wait` | `0` | **0** (`cfg.CART_DATA_WAIT`) | ✅ silicon (v2): 0 is the answer, not "unset" — **re-confirmed 2026-08-06**, see below |
| `ldir_cost` — **byte** LDIR/LDDR | `7` (datasheet) | **14** (`cfg.CART_LDIR_COST`) | ⚠️ strongly evidenced, ROM measurement still open (v6) |
| `ldirw_cost` — **word** LDIRW/LDDRW | `0` = follow `ldir_cost` | **18** (`cfg.CART_LDIRW_COST`) | ⚠️ measured on a homebrew raster oracle (2026-08-06), no calib ROM yet — **v7 would settle it** |
| `vram_wait` | `0` | *not set* | ⚠️ effect confirmed (v3: VWR 452 < MEM 471), cost/byte not pinned — no guess shipped |

⚠️ **The two defaults differ on purpose, and it catches people.** The field default of `0` is
backward compatibility, not a hardware claim; the shell applies the silicon set on every ROM
load (`cart_wait_states()` → True). **Anything that builds a `Machine` itself — a bench script,
a test, the MCP server — runs free-fetch until it calls the setters**, and will measure a
machine ~2.9× too fast. Worse, any optimisation whose gain is *fewer instruction bytes* then
measures as exactly zero, because fetch is the one cost not being billed. Copy the three calls
from `core/romcheck.py`. Full write-up: README → "Timing — wait states".

## Files
- `a_cpu_calib_v6.ngc` — **current ROM.** LDRR/LDVR = ONE big 2000-byte LDIR per batch
  (RAM→RAM and RAM→VRAM), measuring the block-transfer cost/byte. (v4/v5 were broken: the
  compiler hoisted a small looped ldir / an inline-asm count crashed — validated in the
  emulator this time, LDRR=430 = exactly 2000×7.) Prime suspect for Cool Boarders' residual.
- `a_cpu_calib_v4.ngc`, `a_cpu_calib_v5.ngc` — earlier LDIR attempts (BROKEN, ignore).
- `a_cpu_calib_v3.ngc` — added VWR (VRAM write). Silicon: **VWR 452 < MEM 471** → VRAM writes
  ARE throttled in active display — real, but Cool Boarders writes VRAM in vblank, so not its cause.
- `a_cpu_calib_v2.ngc` — added CSEQ/CRND/RRND. Silicon settled `cart_data_wait=0` (CRND==RRND)
  and the MUL/DIV under-costing.
- `a_cpu_calib_v1.ngc` — the original (instruction classes only).
- `cpu_calib_v{1,2,3,4}.c` — sources.
- Built with the **official** Toshiba cc900 toolchain, so they owe nothing to our
  assembler. Rebuild in the C template (`02_CODE_PATTERNS/minimal_template/`): drop
  the `.c` in as `main.c`, `export THOME=$(pwd)/install/ngpcbins/T900`,
  `export PATH=$THOME/BIN:$PATH`, `cp makefile_win makefile`, `make` -> `main.ngp`
  (rename to `.ngc`; ~11 KB, no 2 MB padding needed — the flashcart takes it raw).

## What it shows
Each line = how many **200-op batches finish in 60 video frames (~1 s)**. Bigger
number = the CPU did more work per second = cheaper per op. Clock gear is forced
to 0 (full 6.144 MHz), exactly like the games.

```
BASE : bare loop + a register move (the loop overhead floor)
SHIFT: word shift  v = w << 5      (Cool Boarders' hot instruction)
ADD  : reg-reg add v = v + w
MUL  : multiply    v = v * w
DIV  : divide      v = w / v
MEM  : RAM byte write
RASV : max scanline seen -> 197 = 198 lines/frame ; 198 = 199 lines/frame
```

## Baseline — THIS EMULATOR (measured 2026-07-16)
```
BASE : 02313
SHIFT: 01786
ADD  : 02022
MUL  : 01128
DIV  : 00693
MEM  : 01598
RASV : 198        (i.e. our frame = 199 lines, 0..198)
```

## Silicon result (measured on real hardware, v2, 2026-07-16)
```
BASE : 00682   SHIFT: 00538   ADD  : 00578   MUL  : 00444   DIV  : 00265
MEM  : 00471   CSEQ : 00270   CRND : 00252   RRND : 00252   RASV : 198
```
- Fetch-bound (BASE/SHIFT/ADD/MEM) ~3.4x too fast in the emulator → **`cart_wait=3`** matches.
- **`CRND == RRND` (252 == 252)** → a cart data read == a RAM read → **`cart_data_wait=0`**
  (the earlier `=5` was refuted here).
- MUL/DIV smaller than a pure fetch-wait predicts (2.5x) → they were under-costed →
  **fixed** (emulator now reads MUL 446 / DIV 265).

## v2 — the three data-read tests (isolate `cart_data_wait`)
CSEQ/CRND/RRND each read ONE byte per rep with identical index arithmetic; only the
source differs. RRND reads RAM (never wait-stated); CRND reads cart flash with the
SAME stride, so **RRND − CRND is the pure cart data-read penalty**. (Every test's loop
CODE is fetched from cart, so fetch=3 slows them all equally — that shared cost cancels
in RRND − CRND.) CSEQ reads cart sequentially (stride 1); CSEQ vs CRND hints at flash
page-mode, but carries a small `inc`-vs-`add` arithmetic offset, so only a LARGE
CSEQ > CRND gap means sequential reads are genuinely cheaper (a 3rd parameter).

Emulator v2 numbers (2026-07-16):
```
              cart_wait=0     with fix (fetch=3,data=5)
CSEQ :          00923              00262
CRND :          00871              00246
RRND :          00871              00255      <- RRND==CRND with no fix; CRND<RRND with it
```
So the model charges cart data reads (CRND drops below RRND once the fix is on).

**Silicon verdict:** `RRND − CRND` came back **0** (252 == 252) → cart data reads are NOT
wait-stated, `cart_data_wait=0`, and the residual slowdown is elsewhere. (This is the
"re-open the analysis" branch — which pointed at VRAM writes, hence v3.)

## v3 — the VRAM-write test (VWR), the last open piece
With the CPU model now silicon-exact, Cool Boarders still runs ~51 fps in the emulator
vs 30 on silicon. The remaining suspect is the per-frame **char-RAM ldir**: the K2GE may
throttle CPU VRAM access during the active drawing period (`ngpcspec.txt`, "adjustment
circuitry"). **VWR** writes a byte to VRAM (0xBE00) in the same harness as **MEM** (a RAM
write at 0x4200); the batch loop spans active + vblank lines, so VWR is the average
VRAM-write cost across a frame.

Emulator v3 numbers: with no VRAM wait, `VWR == MEM` (497 ≈ 496). With a 4-cycle VRAM
write, `VWR = 466 < MEM = 496`. **On silicon:**
- `VWR << MEM` → VRAM writes ARE throttled → confirms the hypothesis; the gap gives the
  cost (feeds `vram_wait`, which brings Cool Boarders to 30 fps and leaves Fatal Fury at 60).
- `VWR == MEM` → VRAM writes are NOT throttled → refuted; re-open (do NOT ship a guess —
  a `cart_data_wait=5` guess already got refuted this way).

## v6 — the LDIR (block-copy) test, the current open question
With a silicon-exact CPU and the VRAM throttle understood, Cool Boarders still runs ~51 fps
(vs 30). It does a big per-frame **LDIR** into a RAM frame buffer (~thousands of bytes) the
calib never timed. Our LDIR costs 7 cycles/byte (datasheet) — and the datasheet MUL/DIV
figures already proved to be **floors**. Setting LDIR = 14 makes Cool Boarders hit 30 fps AND
leaves Fatal Fury at 60 — one instruction-cost fix explaining both — but that must be measured,
not guessed. **LDRR** = one 2000-byte LDIR RAM→RAM per batch (pure block cost); **LDVR** =
2000-byte LDIR RAM→VRAM (block + any VRAM throttle). Emulator (LDIR=7): **LDRR == LDVR == 430**
(2000×7 = 14000 cycles dominates the batch — verified in the emulator, unlike v4/v5).

## v7 — the WORD block copy (`LDIRW`), and why v6 cannot answer it

**The gap v6 leaves.** `ldir_cost` is charged **per iteration of the loop**, and a `LDIRW`
iteration moves **two** bytes where a `LDIR` iteration moves one. They were on the same
number, so a word copy was billed at half price per byte. Cool Boarders — the game that
pinned 14 — uses the **byte** form, and LDRR/LDVR in v4/v5/v6 are byte copies too. **Nothing
we have ever measured constrains the word form**, and one field could not have held both
answers anyway.

**What settled it in the meantime — a homebrew ROM, not a calib ROM.** Thor's *BOMBERMAN*
(2004) draws its title screen with a HiColor raster trick that ships **two** implementations
and picks between them at boot by timing a delay loop against RAS.V:

- `hc_showEmu` — polls `RAS.V & 7` between blocks, so it **self-synchronises** and comes out
  right whatever the cycle costs are. This is the reference picture.
- `hc_showHW` — **open loop**: 19 blocks of 224 `ldirw` words, no polling at all, each of
  which must cost exactly one 8-scanline slice (8 × 515 = **4120 cycles**) or the image
  shears. This is the path a real console takes.

This core is accurate enough that the ROM's own detector answers "real hardware"
(`in_emu = 0` at RAM `0x476E`) and takes the open-loop path — so the open-loop path has to
be right. Measured with the event log armed on `0x8280` (one write per block pair, target
pitch **8240** cycles):

| `ldirw_cost` | pitch | vs target | pixels matching the reference |
|---|---|---|---|
| 14 (= `ldir_cost`, the old behaviour) | 6536 | **0.793×** | 30 % |
| 17 | 7880 | 0.956× | 83 % |
| **18** | 8328 | 1.011× | **100 %** |
| 19 | 8418 | 1.022× | 4 % |

**The window is one cycle wide.** That is what makes this a better instrument than any
frame-rate average: Cool Boarders tells you whether a number is roughly right, this tells
you whether it is exactly right.

⛔ **The other explanation was tested and refuted, again by v2.** "The copy's source is slow
cart flash, so charge `cart_data_wait`" also closes the gap — `cart_data_wait = 2` gives the
same 100 % picture. Re-running `a_cpu_calib_v2.ngc` with it: **CRND 252, RRND 255**. Silicon
says those two are equal. So the same ROM that killed `cart_data_wait = 5` in 2026-07 kills
`= 2` as well, and the width split is the explanation left standing. 🔑 **A fix that produces
the right picture is not thereby the right fix** — ask the oracle that already exists.

**What v7 should measure.** v6's harness with a fourth row: **`LDWR`** = one 2000-**byte**
block moved by `LDIRW` (so 1000 iterations), RAM→RAM, alongside the existing `LDRR`. Then
`LDRR` pins the byte form and `LDRR / LDWR` pins the word form, in one flash. Emulator-side
reading scale, so a silicon number can be read off directly:

| what comes back | means |
|---|---|
| `LDRR ≈ 217` | byte form = 14 (what we ship) |
| `LDRR ≈ 169` | byte form = 18 |
| `LDRR ≈ 430` | byte form = 7, the datasheet floor is the truth |

⚠️ v6 **boots in the emulator and crashes on hardware** — unexplained. Build v7 from the v3
source (which flashed fine) rather than from v6, or the same divergence will eat it.

## v8 — the ACCESS COST during active display (bus contention), the top open question

**Where it came from, and it is not our own suspicion.** `Emulator_vs_Hardware_20260807`
(xenon project), measured on device, same-session pairs. Their `SetSpriteEx` writes a sprite
position to a work-RAM shadow and can either keep it in a register or *read it back* as the
source for the OAM store — one extra **work-RAM read** per sprite, 40–60 sprites/frame:

| | with the readback | without | verdict |
|---|---|---|---|
| device, clean pair 2026-08-07 | 117 | 107 | **+9 %** |
| this core, calibrated | 90 | 90 | **0 %** — invisible |

And a third case flips the SIGN: metasprite chaining (fewer OAM writes, more bookkeeping)
measures **−1.8 VBl against** the chain here and **+5 VBl in favour** of it on device. A model
that reverses an optimisation's sign is worse than one that is merely imprecise — it makes the
developer choose backwards.

**What the mechanism is.** Their own source comment names it: *"every access during active
display is penalised by the display controller"*, and *"the emulator reported 1.5 % faster
because it counts instructions and not bus accesses"*. This is **bus contention**, not a cost
of a region.

⚖️ **AND THIS CORE ALREADY HAS THE RIGHT SHAPE — it is simply too narrow** (`execute.cpp`):

```cpp
if (m.vram_wait && a >= 0x8000 && a <= 0xBFFF && !m.in_vblank())
    m.access_wait += m.vram_wait;
```

Per access, and only outside vblank — exactly right, and backed by v3 (`VWR < MEM`). Three
things keep it from covering the case: `vram_wait` **defaults to 0**, it applies to **writes
only**, and it is limited to **0x8000–0xBFFF**. Their readback is a work-RAM READ, so it is
outside all three — turning `vram_wait` on would not touch it. (They also cannot help pin
`vram_wait` itself: their game does all its VRAM writes in vblank.)

🔑 **And v2 left a hole nobody noticed.** v2 proved cart-data reads and RAM reads are **EQUAL
TO EACH OTHER**; it never said what they equal. `cart_data_wait = 0` reads "equal" as
"therefore free", which v2 does not license. Their +9 % is the first evidence that the shared
value is not 0.

**What v8 must measure — and it is NOT "RAM vs cart" (v2 answered that) nor `vram_wait`:**

> **N accesses versus N+1, SAME region, run once during ACTIVE DISPLAY and once during
> VBLANK.**

Four pairs, same harness as v2/v3, each a tight loop of a fixed instruction sequence differing
by exactly one data access:

| row | what it isolates |
|---|---|
| `RD_A` / `RD_V` | one extra **work-RAM READ**, active display vs vblank |
| `WR_A` / `WR_V` | one extra **work-RAM WRITE**, same split |
| `CD_A` / `CD_V` | one extra **cart-data READ**, same split (closes v2's hole) |
| `OR_A` / `OR_V` | one extra **OAM/VRAM READ** (v3 did writes only) |

Reading the result:
- **`X_A == X_V` on every row** → there is no display-phase penalty; the +9 % is something
  else and Case B re-opens. Do NOT ship a number.
- **`X_A < X_V`** → the difference IS the per-access contention cost, per region and per
  direction. That one set of numbers pins `cart_data_wait`, extends the `vram_wait` guard to
  reads, and gives work RAM the term it has never had — which is what all three of their open
  cases are waiting on.

⚠️ Do not calibrate against their ABSOLUTE cycle figures. Their `README.md` states "86 922
cycles per frame at a fixed 30 fps" and, two paragraphs later, that a frame's compute "sits
just above two VBlanks" — 86 922 is **0.85** VBlank here, so the two cannot share a unit.
Their **ratios** (VBl/30F, HW-vs-emu factors) carry no unit and are sound; the absolute
figures are not, until the unit is settled with them.

## How to use it (real hardware)
1. Flash `a_cpu_calib_v6.ngc` to your flashcart, boot it.
2. Wait a few seconds for the numbers to settle, note them all (+ RASV).
3. **The open numbers are `LDRR` and `LDVR`** (the rest re-confirm v1/v2/v3).

### Reading the result (LDIR cost/byte = `7 × 430 / LDRR`)
- **LDRR ≈ 215 (about half of 430)** → LDIR is ~2x under-costed (real ≈ 14/byte). This is
  Cool Boarders' bug — bump the LDIR cycle count and it drops to 30 fps, Fatal Fury untouched.
- **LDRR ≈ 430** → LDIR is correctly costed at 7 → the residual is something else (re-open).
- **LDRR ≈ 860** → LDIR is *over*-costed (real ≈ 3.4) → the residual is elsewhere and LDIR
  needs lowering (a different fix).
- **LDVR < LDRR** → writing the block to VRAM costs extra (the active-display throttle, v3).
- **RASV**: 197 → our 199-line frame is one line too long (~0.5%); 198 → we match.

> The ratio you read is the whole answer: it turns "the game feels too fast" into
> an integer we can act on, and it becomes a non-regression test the emulator must
> reproduce. This is the project's rule: **we don't tune by feel, we measure.**

---

## v8 — LE COÛT D'UNE INTERRUPTION (2026-08-23) — ⏳ attend la mesure silicium

`a_irq_calib_v8.ngp` · md5 **`334e5cb56e26fe78194d913cee4029a3`** · source `cpu_calib_v8.c`

**Pourquoi.** Toutes les ROM v1–v7 mesurent du code qui tourne **sans interruptions**. Or
le défaut ouvert est un split rasteur : Cool Boarders coupe `INTT0` (niveau à 0 dans
`0x0073`), fait son travail, le ré-autorise — et la ligne où son split tombe dépend du
débit **dans cette fenêtre, interruptions comprises**. Notre modèle silicium facture
l'entrée en interruption **36 cycles** (18 états × 2) là où l'ancien timing n'en compte que
**18**. Ce nombre n'a **jamais** été mesuré sur silicium.

**Ce que la ROM fait.** Le même lot de travail, trois fois : `WORK0` avec `INTT0` interdit,
`WORK1` avec une interruption **par ligne** (`TREG0`=1, timer 0 en mode broche externe =
le H-blank, exactement le montage de Cool Boarders), `WORK4` une ligne sur quatre.
`LINE` et `RASV` sont des contrôles de longueur de trame.

⚠️ **Aucun gestionnaire n'est installé, et c'est voulu** : deviner le vecteur utilisateur
du BIOS ajouterait une inconnue. On mesure le coût **total d'une interruption prise**,
stub du BIOS compris — ce que le jeu paie aussi.

### Ce que nos deux modèles prédisent

| | WORK0 | WORK1 | WORK4 | coût des IRQ |
|---|---|---|---|---|
| ancien timing (défaut actuel) | **263** | 240 | 258 | −23, soit **8,7 %** du débit |
| modèle silicium (`--timing silicon`) | **214** | 181 | 208 | −33, soit **15,4 %** |

⇒ le modèle facture les interruptions **presque deux fois plus cher en proportion**. Les
deux jeux de chiffres sont assez éloignés pour que le silicium tranche sans ambiguïté.

### Protocole

1. Flasher, lancer, **noter les cinq nombres** (`WORK0`, `WORK1`, `WORK4`, `LINE`, `RASV`)
   et **le md5 ci-dessus** — sans le md5 la mesure n'est rattachable à rien.
2. `RASV` doit valoir **198**. Sinon la console n'est pas dans l'état attendu : rejeter le tir.
3. Laisser tourner quelques secondes : les nombres se réaffichent en boucle et doivent être
   **stables**. Un chiffre qui saute d'un tour à l'autre invalide le tir.

### Comment on lira le résultat

- `WORK0` est le **contrôle** : il ne fait intervenir aucune interruption et doit tomber
  près de la valeur du modèle déjà validé par v1/v2.
- `(WORK0 − WORK1) / (WORK0 − WORK4)` doit valoir **~4** sur silicium. Si non, le coût
  n'est pas linéaire en nombre d'interruptions et notre modèle est faux **dans sa forme**,
  pas seulement dans sa valeur — ce serait le résultat le plus important des trois.
- Le rapport `(WORK0 − WORK1)` silicium / émulateur donne **directement** le facteur
  d'erreur sur le coût d'une interruption, et donc quoi mettre dans `kIrqDeliveryCycles`.

⛔ **Ne pas ajuster la constante sur Cool Boarders.** C'est ce que cette ROM existe pour
éviter : la v2 avait déjà attrapé un `cart_data_wait=5` réglé à l'oreille sur ce même jeu.
