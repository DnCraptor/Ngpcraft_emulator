#!/usr/bin/env python3
"""Generate crt0.asm for the clean-room HLE BIOS image (M0).

Nothing here is copied from the SNK BIOS: only the *structure* the hardware
and the emulator core require (vector-table layout, the swi dispatch shape,
the user-vector chaining) is reproduced. All of it is public contract from
the SDK docs / the TLCS-900 architecture.

Image map (64 KiB, linked at 0xFF0000):
  0xFF0000  f_code       boot + dispatcher + ISRs + stubs
  0xFFFE00  SYSCALL_TBL  32 pointers, swi 1 vector index -> stub
  0xFFFF00  INT_VECTOR   60 hardware vectors (reset/swi/IRQ)
"""

# --- user vector table (RAM, 0x6FB8) : which hardware IRQ index maps to
#     which 4-byte slot. Source: ngpcspec.txt "User Program Interrupt Vectors"
#     (the "MICRO DMA START VECTOR" column IS the hardware index).
UVT = 0x6FB8
USER_SLOT = {
    3: UVT + 0 * 4,    # SWI 3
    4: UVT + 1 * 4,    # SWI 4
    5: UVT + 2 * 4,    # SWI 5
    6: UVT + 3 * 4,    # SWI 6
    10: UVT + 4 * 4,   # RTC alarm      (0x6FC8)
    11: UVT + 5 * 4,   # VBlank         (0x6FCC)
    12: UVT + 6 * 4,   # Z80            (0x6FD0)
    16: UVT + 7 * 4,   # Timer 0        (0x6FD4)
    17: UVT + 8 * 4,   # Timer 1
    18: UVT + 9 * 4,   # Timer 2
    19: UVT + 10 * 4,  # Timer 3
    24: UVT + 11 * 4,  # Serial TX      (0x6FE4)
    25: UVT + 12 * 4,  # Serial RX      (0x6FE8)
    29: UVT + 14 * 4,  # End micro-DMA 0 (0x6FF0)
    30: UVT + 15 * 4,  # End micro-DMA 1
    31: UVT + 16 * 4,  # End micro-DMA 2
    32: UVT + 17 * 4,  # End micro-DMA 3
}

HW_TABLE_SLOTS = 60      # 0xFFFF00 .. 0xFFFFF0, 4 bytes each
SYSCALL_SLOTS = 32       # 0xFFFE00 .., swi 1 dispatch indices 0..0x1A used

FONT_FG = 3              # 2bpp pixel index for a lit pixel (default palette)
FONT_BG = 0


def build_font_2bpp():
    """Clean-room 8x8 font (Pillow's own public-domain default face), expanded
    to the K2GE 2bpp CHAR-RAM tile format: 256 tiles x 16 bytes, each row a
    big-endian word (pixel 0 in the high bits), matching the retail SYSFONTSET
    layout. Returns 0x1000 bytes to preload at 0xA000."""
    from PIL import Image, ImageFont, ImageDraw
    fnt = ImageFont.load_default()
    out = bytearray()
    for code in range(256):
        img = Image.new("L", (8, 8), 0)
        if 0x20 <= code < 0x7F:
            ImageDraw.Draw(img).text((0, -1), chr(code), fill=255, font=fnt)
        for y in range(8):
            word = 0
            for x in range(8):
                word = (word << 2) & 0xFFFF
                lit = img.getpixel((x, y)) > 96
                word |= FONT_FG if lit else FONT_BG
            out.append((word >> 8) & 0xFF)   # big-endian: pixels 0-3
            out.append(word & 0xFF)          # pixels 4-7
    assert len(out) == 0x1000
    return bytes(out)


def gen():
    out = []
    A = out.append
    A(";***************************************************************")
    A(";  NgpCraft Emulator - clean-room HLE BIOS image (M0)  [GENERATED]")
    A(";  Loaded via ngpc_load_bios when no real bios.bin is present.")
    A(";  Clean-room: reproduces the hardware/SDK CONTRACT, not SNK bytes.")
    A(";***************************************************************")
    A("\t$MAXIMUM")
    A("\tmodule\tbios_hle")
    A("")
    A("\tpublic\t_boot")
    A("")
    A(";===============================================================")
    A("f_code\tsection code large align=1,1")
    A(";===============================================================")
    A("")
    A("; --- RESET / power-on : hw vector slot 0 (0xFFFF00) ---")
    A("_boot:")
    A("\tld\txiy, _uvt_stub          ; 45 <stub>   ] ANCHOR the core's")
    A("\tld\txix, 0x00006fb8         ; 44 B8 6F..  ] seed_user_vector_table scans")
    A("\tld\tbc, 18")
    A("_uvt_fill:")
    A("\tldl\t(xix+), xiy")
    A("\tdjnz\tbc, _uvt_fill")
    A("\tldw\t(0x006c7a), 0xa5a5     ; 'booted once' marker")
    A("\t; preload the clean-room font into CHAR RAM (the warm-up captures it,")
    A("\t; the hand-off installs it) -- games that SYSFONTSET find it present.")
    A("\tld\txix, _font_data")
    A("\tld\txiy, 0x0000a000")
    A("\tld\tbc, 0x0400              ; 0x1000 bytes / 4")
    A("_font_copy:")
    A("\tldl\txwa, (xix+)")
    A("\tldl\t(xiy+), xwa")
    A("\tdjnz\tbc, _font_copy")
    A("_boot_idle:")
    A("\thalt")
    A("\tjr\t_boot_idle")
    A("")
    A("; --- default user-vector stub (bare RETI) ---")
    A("_uvt_stub:")
    A("\treti")
    A("")
    A("; --- generic unused-interrupt stub ---")
    A("_reti:")
    A("\treti")
    A("")
    A("; --- swi 1 : BIOS system-call trap. Dispatch through SYSCALL_TBL, same as")
    A(";  the SDK's SYSTEM_CALL wrapper -- some games (Card Fighters Clash 2) call")
    A(";  INTLVSET via `swi 1` directly, not the wrapper. ldf 3 makes W == RW3.")
    A(";  We emulate `call (stub)` with a push/RET (asm900 rejects `call (reg)`),")
    A(";  the stub returns via RET, then we RETI back to the caller.")
    A("_swi1:")
    A("\tldf\t3                     ; W = RW3 = vector index")
    A("\tadd\tw, w")
    A("\tadd\tw, w                   ; W = index * 4")
    A("\tld\txhl, 0x00fffe00")
    A("\tldl\txhl, (xhl+w)           ; XHL = SYSCALL_TBL[index]")
    A("\tld\txde, _swi1_after        ; return address for the emulated call")
    A("\tpush\txde")
    A("\tpush\txhl")
    A("\tret                            ; -> stub (returns via RET to _swi1_after)")
    A("_swi1_after:")
    A("\treti")
    A("")
    A(";  Syscall stubs reached by a DIRECT table CALL -- the SDK's SYSTEM_CALL")
    A(";  wrapper (calr, not swi 1): it does ldf 3 then `call (0xFFFE00 + RW3*4)`,")
    A(";  so a stub runs in bank 3 and returns via RET. Commercial COM-heavy games")
    A(";  (Nige-Ron-Pa, Koi Koi, KOF-BdP) DRAIN the serial RX with COMGETDATA until")
    A(";  RA3==1 ('no more data'); with no cable the real BIOS returns 1 at once.")
    A("_sc_ret:")
    A("\tret                            ; default: leave registers as-is")
    A("_sc_nodata:")
    A("\tldw\twa, 0x0101              ; RA3=1 (and RW3=1): 'buffer empty / no data'")
    A("\tret")
    A("_sc_zero:")
    A("\tldw\twa, 0x0000              ; RWA3=0: COM*STATUS = no error, no data")
    A("\tret")
    A(";  RTCGET: copy 7 packed-BCD clock bytes (year,month,day,hour,min,sec,")
    A(";  weekday) from the RTC I/O regs 0x91-0x97 into the caller's buffer at")
    A(";  XHL3. The core routes 0x90-0x9A reads to its seeded RTC. WA/XHL preserved.")
    A("_sc_rtcget:")
    A("\tpush\twa")
    A("\tpush\txhl")
    for reg in range(0x91, 0x98):
        A(f"\tldb\ta, ({reg:#04x})")
        A("\tldb\t(xhl+), a")
    A("\tpop\txhl")
    A("\tpop\twa")
    A("\tret")
    A(";  INTLVSET: set interrupt source's priority in the INTxx I/O regs. Bank 3:")
    A(";  C=source(0..9), B=level. MEASURED from the retail BIOS (5 games): the")
    A(";  written nibble = 0x08 | min(level&7,5) -- the 0x08 is the ENABLE bit the")
    A(";  Python ref omits. Even source -> low nibble, odd -> high; reg from table.")
    A("_sc_intlvset:")
    A("\tpush\txhl")
    A("\tpush\txde")
    A("\tcp\tc, 0x0a")
    A("\tjr\tnc, _intlv_done         ; source > 9: no INTxx register written")
    A("\tldb\ta, b")
    A("\tand\ta, 0x07")
    A("\tcp\ta, 0x06")
    A("\tjr\tc, _intlv_cap")
    A("\tldb\ta, 0x05                ; cap level at 5 (retail BIOS)")
    A("_intlv_cap:")
    A("\tor\ta, 0x08                 ; nibble = 0x08(enable) | level")
    A("\tldl\txde, 0")
    A("\tldl\txhl, _intlv_reg")
    A("\tldb\te, (xhl+c)             ; XDE = INTxx I/O address for this source")
    A("\tbit\t0, c")
    A("\tjr\tz, _intlv_lo            ; even source -> low nibble")
    A("\tsll\t4, a                   ; odd source -> high nibble")
    A("\tldb\tl, (xde)")
    A("\tand\tl, 0x0f")
    A("\tjr\t_intlv_or")
    A("_intlv_lo:")
    A("\tldb\tl, (xde)")
    A("\tand\tl, 0xf0")
    A("_intlv_or:")
    A("\tor\tl, a")
    A("\tldb\t(xde), l               ; write the INTxx register")
    A("_intlv_done:")
    A("\tpop\txde")
    A("\tpop\txhl")
    A("\tret")
    A("_intlv_reg:")
    A("\tdb\t0x70,0x71,0x73,0x73,0x74,0x74,0x79,0x79,0x7a,0x7a")
    A("")
    A(";  Register-preserving indirect jump used by every ISR trampoline:")
    A(";  save XWA to BIOS scratch RAM, push the user-handler address, restore")
    A(";  XWA, RET -> jumps to the handler with the interrupt frame intact and")
    A(";  every register unchanged. No stack turd, no unsupported EX/CALL form.")
    A("SCRATCH\tequ\t0x006c40")
    A("")
    A("; --- VBlank ISR : latch controller into Sys_Lever, then chain ---")
    A("_isr_vblank:")
    A("\tldl\t(SCRATCH), xwa        ; save XWA")
    A("\tldb\ta, (0x0000b0)          ; controller port")
    A("\tldb\t(0x006f82), a          ; -> Sys_Lever (games read this)")
    A("\tldl\txwa, (0x00006fcc)      ; user VBlank handler")
    A("\tpush\txwa")
    A("\tldl\txwa, (SCRATCH)         ; restore XWA")
    A("\tret                            ; -> user handler (it RETIs)")
    A("")
    A("; --- chain trampolines: forward IRQ to the game's user handler ---")
    # emit one chain routine per user-hooked vector (except vblank, above)
    chain_label = {}
    for idx, slot in sorted(USER_SLOT.items()):
        if idx == 11:
            chain_label[idx] = "_isr_vblank"
            continue
        lbl = f"_chain_{idx:02d}"
        chain_label[idx] = lbl
        A(f"{lbl}:")
        A("\tldl\t(SCRATCH), xwa")
        A(f"\tldl\txwa, ({slot:#08x})")
        A("\tpush\txwa")
        A("\tldl\txwa, (SCRATCH)")
        A("\tret")
    A("")
    A(";===============================================================")
    A("; clean-room font, pre-expanded to 2bpp CHAR-RAM tiles (256 x 16 B)")
    A(";===============================================================")
    A("_font_data:")
    font = build_font_2bpp()
    for off in range(0, len(font), 16):
        row = ", ".join(f"0x{b:02x}" for b in font[off:off + 16])
        A(f"\tdb\t{row}")
    A("")
    A(";===============================================================")
    A("; BIOS system-call table @ 0xFFFE00 (indexed by RW3)")
    A(";===============================================================")
    A("SYSCALL_TBL\tsection code large align=1,1")
    sc_stub = {
        0x02: "_sc_rtcget",   # RTCGET            -> copy the clock into XHL3 (Ganbare)
        0x04: "_sc_intlvset", # INTLVSET          -> set INTxx priority (Card Fighters Clash 2)
        0x14: "_sc_nodata",   # COMGETDATA        -> 'no data' so drain loops exit
        0x17: "_sc_zero",     # COMSENDSTATUS     -> no error / no pending
        0x18: "_sc_zero",     # COMRECIVESTATUS   -> no error / no data (SNK Gals')
    }
    for i in range(SYSCALL_SLOTS):
        lbl = sc_stub.get(i, "_sc_ret")
        A(f"\tdl\t{lbl:<10s}       ; {i:#04x}")
    A("")
    A(";===============================================================")
    A("; CPU hardware vector table @ 0xFFFF00 (index*4)")
    A(";===============================================================")
    A("INT_VECTOR\tsection code large align=1,1")
    for i in range(HW_TABLE_SLOTS):
        if i == 0:
            tgt, note = "_boot", "reset / swi0"
        elif i == 1:
            tgt, note = "_swi1", "swi 1 (system call)"
        elif i in chain_label:
            tgt, note = chain_label[i], f"-> user slot {USER_SLOT[i]:#06x}"
        else:
            tgt, note = "_reti", "unused"
        A(f"\tdl\t{tgt:<12s}       ; {i:#04x} {note}")
    A("")
    A("\tend")
    return "\r\n".join(out) + "\r\n"


if __name__ == "__main__":
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "src", "crt0.asm")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        f.write(gen())
    print("wrote", path)
