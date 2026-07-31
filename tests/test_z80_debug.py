# -*- coding: utf-8 -*-
"""The sound-CPU view: its address map, its registers, and why it stopped.

Pure -- a fake main bus and a stand-in for the aux state. The address map is the
part that matters most: the Z80 does NOT see the main CPU's memory, and reading its
addresses as main-bus addresses gives you the video registers. Plausible, and wrong.
"""

from types import SimpleNamespace

from core import z80_debug as zd


def _bus(values=None):
    b = bytearray(0x1000000)
    for addr, val in (values or {}).items():
        b[addr] = val & 0xFF
    return lambda a, n=1: bytes(b[a:a + n]), b


def _aux(**kw):
    base = dict(
        z80_a=0, z80_f=0, z80_b=0, z80_c=0, z80_d=0, z80_e=0, z80_h=0, z80_l=0,
        z80_a2=0, z80_f2=0, z80_b2=0, z80_c2=0, z80_d2=0, z80_e2=0, z80_h2=0, z80_l2=0,
        z80_ix=0, z80_iy=0, z80_sp=0x0FF0, z80_pc=0,
        z80_i=0, z80_r=0, z80_im=1, z80_iff1=1, z80_iff2=1,
        z80_halted=0, z80_running=1, z80_nmi_pending=0, z80_int_pending=0,
        z80_trapped=0, z80_trap_prefix=0, z80_trap_opcode=0, z80_trap_pc=0,
        z80_cycle_credit=0, z80_executed=0,
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------- address map
def test_the_z80_reads_the_shared_ram_not_the_main_bus():
    """Z80 0x0000 is main-bus 0x7000. Reading it as 0x0000 lands in the CPU's own
    I/O page -- a completely different set of bytes that would still look like
    data."""
    read_main, b = _bus({0x007000: 0xAB, 0x000000: 0xCD})
    read = zd.make_reader(read_main)
    assert read(0x0000) == 0xAB


def test_the_shared_ram_mirrors_four_times():
    """4 KiB seen across a 16 KiB window. A driver that runs at 0x2000 is running
    the same bytes as one at 0x0000, and a viewer that shows 0xFF there hides
    live code."""
    read_main, b = _bus({0x007123: 0x5A})
    read = zd.make_reader(read_main)
    for base in (0x0000, 0x1000, 0x2000, 0x3000):
        assert read(base + 0x123) == 0x5A


def test_the_sound_chip_is_write_only():
    read_main, _ = _bus({0x004000: 0x11})
    assert zd.make_reader(read_main)(0x4000) == 0xFF


def test_everything_above_the_chip_reads_the_comm_latch():
    """0x8000 (mailbox) and 0xC000 (interrupt request) both read back the same
    latch on hardware -- so they do here."""
    read_main, _ = _bus({zd.COMM_REGISTER_MAIN: 0x42})
    read = zd.make_reader(read_main)
    assert read(0x8000) == 0x42
    assert read(0xC000) == 0x42


def test_regions_are_named():
    assert zd.region_of(0x0100)[0] == "work RAM"
    assert zd.region_of(0x4001)[0] == "T6W28"
    assert zd.region_of(0x8000)[0] == "comm"
    assert zd.region_of(0xC000)[0] == "INT to main CPU"


def test_a_dead_bus_reads_as_open_bus_not_an_exception():
    def dead(a, n=1):
        raise RuntimeError("no core")
    assert zd.make_reader(dead)(0x0000) == 0xFF


# ---------------------------------------------------------------- registers
def test_registers_pair_the_bytes_the_way_the_cpu_does():
    view = zd.registers(_aux(z80_b=0x12, z80_c=0x34, z80_h=0xAB, z80_l=0xCD))
    pairs = dict(view.pairs)
    assert pairs["BC"] == "1234"
    assert pairs["HL"] == "ABCD"


def test_flags_are_spelled_out():
    assert zd.flags_text(0b1100_0000) == "SZ"
    assert zd.flags_text(0x00) == "-"
    assert zd.flags_text(0b0000_0001) == "C"


def test_the_shadow_set_is_shown_separately():
    """`exx` swaps them in one instruction, so a driver's real state can be in
    either bank. Showing only one is showing half the CPU."""
    view = zd.registers(_aux(z80_b2=0x99, z80_c2=0x88))
    assert dict(view.shadow)["BC'"] == "9988"


def test_the_cycle_credit_is_reported_signed():
    """It is signed on purpose: an instruction that overruns its budget BORROWS
    from the next tick. Throwing the overrun away is what made the Z80 run five
    times too fast."""
    assert dict(zd.registers(_aux(z80_cycle_credit=-7)).control)["cycle credit"] == "-7"


# ---------------------------------------------------------------- stop reason
def test_halted_and_trapped_are_never_the_same_message():
    """A halt is the driver sleeping between timer ticks -- normal, and where it
    spends most of its life. A trap is our core refusing an opcode. Merging them
    turns a real emulator hole into background noise."""
    read_main, _ = _bus()
    halted = zd.stop_reason(_aux(z80_halted=1), read_main)
    assert not halted.stopped and "halted" in halted.title

    trapped = zd.stop_reason(_aux(z80_trapped=1, z80_trap_pc=0x0123,
                                  z80_trap_opcode=0xDD), read_main)
    assert trapped.stopped and "TRAPPED" in trapped.title


def test_a_trap_names_the_instruction_that_caused_it():
    """'trapped' alone is the least useful true statement available. With the PC
    and the opcode it becomes 'implement THIS instruction'."""
    read_main, b = _bus()
    b[0x007100:0x007104] = bytes([0xED, 0xB0, 0x00, 0x00])   # ldir at Z80 0x0100
    why = zd.stop_reason(_aux(z80_trapped=1, z80_trap_pc=0x0100,
                              z80_trap_opcode=0xB0, z80_trap_prefix=0xED), read_main)
    assert "0x0100" in why.title and "ED B0" in why.title
    assert "ldir" in why.detail
    assert "hole in the emulator" in why.detail


def test_held_in_reset_names_the_register_that_releases_it():
    why = zd.stop_reason(_aux(z80_running=0), _bus()[0])
    assert why.stopped and "reset" in why.title
    assert "0x00B9" in why.detail


def test_running_says_nothing_alarming():
    why = zd.stop_reason(_aux(), _bus()[0])
    assert not why.stopped and why.title == "running"


# ---------------------------------------------------------------- stack + report
def test_the_stack_is_read_little_endian_through_the_z80_map():
    read_main, b = _bus()
    b[0x007F00] = 0x34
    b[0x007F01] = 0x12
    entries = zd.stack(read_main, 0x0F00, depth=1)
    assert entries == [(0x0F00, 0x1234)]


def test_the_report_holds_the_registers_the_stack_and_a_listing():
    read_main, b = _bus()
    b[0x007000:0x007003] = bytes([0xC3, 0x00, 0x40])       # jp 0x4000
    text = zd.format_report(_aux(z80_pc=0x0000, z80_b=0x12, z80_c=0x34), read_main)
    assert "BC 1234" in text
    assert "jp 0x4000" in text
    assert "> 0000" in text, "the PC line is marked"
    assert "stack" in text


def test_the_report_survives_a_dead_bus():
    def dead(a, n=1):
        raise RuntimeError("no core")
    assert "Z80 sound CPU" in zd.format_report(_aux(), dead)
