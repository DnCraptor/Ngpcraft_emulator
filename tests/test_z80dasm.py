# -*- coding: utf-8 -*-
"""The Z80 disassembler for the sound CPU.

Encodings are taken from the instruction set itself, not from our core: the point of
a disassembler is to say what a byte IS, which is a different question from whether
we execute it. Several cases below are opcodes our Z80 traps — they must still
disassemble, or the listing agrees with the bug.
"""

from core import z80dasm as z


def _mem(*data, base=0):
    b = bytearray(0x10000)
    b[base:base + len(data)] = bytes(data)
    return lambda addr: b[addr & 0xFFFF]


def dis(*data, base=0):
    return z.disassemble_at(_mem(*data, base=base), base)


def text(*data, base=0):
    return dis(*data, base=base).text


# ---------------------------------------------------------------- unprefixed
def test_the_eight_bit_loads():
    assert text(0x78) == "ld a,b"
    assert text(0x46) == "ld b,(hl)"
    assert text(0x77) == "ld (hl),a"
    assert text(0x3E, 0x42) == "ld a,0x42"


def test_halt_is_not_ld_hl_hl():
    """0x76 sits exactly where `ld (hl),(hl)` would be. A decoder that misses the
    special case prints a load for the instruction that parks the CPU — and the
    sound driver spends most of its life on this byte."""
    assert text(0x76) == "halt"


def test_sixteen_bit_loads_and_arithmetic():
    assert text(0x21, 0x34, 0x12) == "ld hl,0x1234"
    assert text(0x31, 0x00, 0xC0) == "ld sp,0xC000"
    assert text(0x09) == "add hl,bc"
    assert text(0x2A, 0x00, 0x70) == "ld hl,(0x7000)"
    assert text(0x22, 0x00, 0x70) == "ld (0x7000),hl"


def test_relative_jumps_are_resolved_to_an_address():
    """A listing that prints `jr +5` makes you do the arithmetic; one that prints
    the destination lets you follow the code. Displacement is from the byte AFTER
    the instruction."""
    assert text(0x18, 0x05, base=0x100) == "jr 0x0107"
    assert text(0x20, 0xFE, base=0x100) == "jr nz,0x0100", "a backward jump to itself"
    assert text(0x10, 0xFB, base=0x200) == "djnz 0x01FD"


def test_a_jump_reports_its_target_and_its_kind():
    i = dis(0xC3, 0x00, 0x40)
    assert i.text == "jp 0x4000" and i.target == 0x4000
    assert i.is_jump and not i.is_call
    c = dis(0xCD, 0x34, 0x12)
    assert c.is_call and c.target == 0x1234
    assert dis(0xC9).is_return
    assert dis(0xC8).is_return, "ret z still leaves by the same door"


def test_alu_and_accumulator_operations():
    assert text(0x80) == "add a,b"
    assert text(0x9E) == "sbc a,(hl)"
    assert text(0xFE, 0x55) == "cp 0x55"
    assert text(0x07) == "rlca"
    assert text(0x2F) == "cpl"


def test_stack_io_and_control():
    assert text(0xC5) == "push bc"
    assert text(0xF1) == "pop af"
    assert text(0xD3, 0x00) == "out (0x00),a"
    assert text(0xDB, 0x01) == "in a,(0x01)"
    assert text(0xE9) == "jp (hl)"
    assert text(0xF3) == "di"
    assert text(0xFB) == "ei"
    assert text(0xFF) == "rst 0x38"


# ---------------------------------------------------------------- CB page
def test_bit_operations():
    assert text(0xCB, 0x00) == "rlc b"
    assert text(0xCB, 0x46) == "bit 0,(hl)"
    assert text(0xCB, 0xFF) == "set 7,a"
    assert text(0xCB, 0x86) == "res 0,(hl)"
    assert text(0xCB, 0x30) == "sll b", "undocumented, but the drivers do use it"


# ---------------------------------------------------------------- ED page
def test_ed_page():
    assert text(0xED, 0x4B, 0x00, 0x70) == "ld bc,(0x7000)"
    assert text(0xED, 0x52) == "sbc hl,de"
    assert text(0xED, 0x44) == "neg"
    assert text(0xED, 0x56) == "im 1"
    assert text(0xED, 0x4D) == "reti"
    assert text(0xED, 0x45) == "retn"
    assert text(0xED, 0xB0) == "ldir"
    assert text(0xED, 0xB8) == "lddr"
    assert text(0xED, 0xA1) == "cpi"


def test_undefined_ed_opcodes_are_named_as_data_not_invented():
    """The undefined ED page behaves as two NOPs on hardware. Printing a made-up
    mnemonic would put a claim in the listing that nothing supports."""
    assert text(0xED, 0x00) == "db 0xED,0x00"
    assert dis(0xED, 0x00).length == 2, "it still consumes both bytes, so we resync"


# ---------------------------------------------------------------- DD / FD
def test_index_register_forms():
    assert text(0xDD, 0x21, 0x34, 0x12) == "ld ix,0x1234"
    assert text(0xFD, 0x21, 0x34, 0x12) == "ld iy,0x1234"
    assert text(0xDD, 0x7E, 0x05) == "ld a,(ix+5)"
    assert text(0xDD, 0x77, 0xFB) == "ld (ix-5),a"
    assert text(0xDD, 0x36, 0x02, 0x99) == "ld (ix+2),0x99"
    assert text(0xDD, 0xE9) == "jp (ix)"
    assert text(0xDD, 0x23) == "inc ix"


def test_the_index_halves_exist_but_not_where_a_displacement_does():
    """Under DD, H and L become IXh/IXl — EXCEPT in an instruction that already
    carries a displacement, where they stay plain H and L. Getting this backwards
    prints `ld ixh,(ix+3)`, an instruction that does not exist."""
    assert text(0xDD, 0x7C) == "ld a,ixh"
    assert text(0xDD, 0x66, 0x03) == "ld h,(ix+3)"
    assert text(0xDD, 0x74, 0x03) == "ld (ix+3),h"


def test_ddcb_takes_its_displacement_before_the_opcode():
    """DD CB d op — the displacement sits BETWEEN the prefix and the opcode. A
    decoder that reads them in the other order gets both the operation and the
    length wrong, and every following instruction with them."""
    i = dis(0xDD, 0xCB, 0x04, 0x46)
    assert i.text == "bit 0,(ix+4)"
    assert i.length == 4


def test_ddcb_reports_the_undocumented_write_back():
    """An indexed CB whose low three bits are not 6 also copies the result into
    that register. Real hardware, and a listing that hides it leaves a register
    changing for no visible reason."""
    assert text(0xDD, 0xCB, 0x04, 0x00) == "rlc (ix+4),b"
    assert text(0xDD, 0xCB, 0x04, 0xC6) == "set 0,(ix+4)", "z=6: no write-back"


# ---------------------------------------------------------------- listing
def test_lengths_and_a_running_listing():
    read = _mem(0x21, 0x00, 0x70,      # ld hl,0x7000
                0x7E,                  # ld a,(hl)
                0xFE, 0x01,            # cp 0x01
                0x28, 0xF9,            # jr z,-7
                0xC9)                  # ret
    out = z.disassemble(read, 0, 5)
    assert [i.text for i in out] == [
        "ld hl,0x7000", "ld a,(hl)", "cp 0x01", "jr z,0x0001", "ret"]
    assert [i.addr for i in out] == [0, 3, 4, 6, 8]
    assert out[0].hex == "21 00 70"


def test_a_dead_bus_still_produces_a_listing():
    """A detached window or a torn-down core reads nothing. A disassembler that
    raises there disappears exactly when something has gone wrong."""
    def dead(addr):
        raise RuntimeError("no core")
    out = z.disassemble(dead, 0, 4)
    assert len(out) == 4
    assert all(i.length >= 1 for i in out), "never zero length -- that would loop"


def test_the_listing_wraps_at_the_top_of_the_address_space():
    read = _mem(0xC9, base=0)
    out = z.disassemble(read, 0xFFFF, 2)
    assert out[0].addr == 0xFFFF
    assert out[1].addr == (0xFFFF + out[0].length) & 0xFFFF
