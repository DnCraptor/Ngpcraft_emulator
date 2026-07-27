"""The two hardware-safety findings, and the line between reporting and enforcing.

Both contracts come from SNK's SysPro/ngpcspec documentation:

* user RAM ends at 0x6BFF, so a descending user stack starts at 0x6C00 and a
  cartridge that leaves XSP above it pushes into the BIOS's own page;
* I/O 0x006F must receive 0x4E periodically, and the retail BIOS hands the
  console over with the watchdog ARMED (WDMOD=0xF0), so that is the cart's duty.

The core used to model neither: the watchdog register was a byte nobody aged,
and the stack was nobody's business. A GB2NGP build that reset a real console
therefore played happily in emulation.

What it does now is COUNT them. Neither halts a console at the instruction that
commits it -- the stack overwrite corrupts something for later, the watchdog
raises INTWD -- so neither halts this one, and an emulator that stopped there
would be reporting a crash the hardware does not have. `set_hw_guard` is the
opt-in for the callers that do want a verdict.
"""

from __future__ import annotations

import unittest

from core import native


ENTRY = 0x200040

# `jr $`: five cycles, forever. The cheapest way to spend a lot of machine time.
SPIN = b"\x68\xFE"
# ldb (0x6F),0x4E -- the refresh, three bytes.
FEED = b"\x08\x6F\x4E"
# One watchdog period is a CPU second; the spin retires ~1.2 M instructions in it.
INSTRS_PER_PERIOD = 6_144_000 // 5


def _rom(code: bytes) -> bytes:
    image = bytearray(b"\x00" * max(0x100, 0x40 + len(code)))
    image[0x1C:0x20] = ENTRY.to_bytes(4, "little")
    image[0x23] = 0x10
    image[0x40:0x40 + len(code)] = code
    return bytes(image)


def _machine(code: bytes, *, guard: int = 0) -> native.NativeMachine:
    machine = native.NativeMachine(_rom(code))
    machine.reset(bios_handoff=False)
    machine.set_hw_guard(guard)
    # Nothing in these ROMs services an interrupt, and the vector table is zeroed:
    # mask them off so what is measured is the watchdog and nothing else.
    cpu = machine.cpu()
    cpu.iff_level = 7
    machine.set_cpu(cpu)
    return machine


@unittest.skipUnless(native.available(), "native core not built (cmake --build cpp/build)")
class WatchdogTests(unittest.TestCase):
    def test_a_starved_watchdog_is_reported_and_the_rom_keeps_running(self) -> None:
        with _machine(SPIN) as machine:
            summary, _ = machine.run(2 * INSTRS_PER_PERIOD, record=False)
            found = machine.hw_violations(native.HW_WATCHDOG)
            samples = machine.hw_violation_samples()

        # The finding is a finding, not a stop: the batch ended because it ran out
        # of instructions, exactly as it would have before this check existed.
        self.assertEqual(summary.stop_status, native.STATUS_COUNT_REACHED)
        self.assertGreaterEqual(found, 1)
        self.assertEqual(samples[0].kind, native.HW_WATCHDOG)
        self.assertEqual(samples[0].kind_name, "watchdog-starved")
        # It fired at one period, not at some multiple or fraction of it.
        self.assertGreater(samples[0].cycle, 6_100_000)
        self.assertLess(samples[0].cycle, 6_200_000)

    def test_it_re_arms_so_a_long_starve_reports_once_per_period(self) -> None:
        with _machine(SPIN) as machine:
            machine.run(3 * INSTRS_PER_PERIOD, record=False)
            self.assertEqual(machine.hw_violations(native.HW_WATCHDOG), 3)

    def test_refreshing_0x4e_keeps_it_quiet(self) -> None:
        with _machine(FEED + b"\x68\xFB") as machine:      # feed; jr back to the feed
            summary, _ = machine.run(2 * INSTRS_PER_PERIOD, record=False)
            self.assertEqual(machine.hw_violations(native.HW_WATCHDOG), 0)
        self.assertEqual(summary.stop_status, native.STATUS_COUNT_REACHED)

    def test_b1_disables_it_the_way_the_retail_bios_does(self) -> None:
        # WDMOD=0x14 then WDCR=0xB1 is what the BIOS writes at 0xFF215D/0xFF2160,
        # and what the Toshiba startup emits. A console with the watchdog switched
        # off may spin forever, and must not be accused of anything.
        code = b"\x08\x6E\x14\x08\x6F\xB1" + SPIN
        with _machine(code) as machine:
            machine.run(2 * INSTRS_PER_PERIOD, record=False)
            self.assertEqual(machine.hw_violations(native.HW_WATCHDOG), 0)

    def test_the_gate_turns_it_into_a_verdict(self) -> None:
        with _machine(SPIN, guard=native.HW_WATCHDOG) as machine:
            summary, _ = machine.run(2 * INSTRS_PER_PERIOD, record=False)

        self.assertEqual(summary.stop_status, native.STATUS_WATCHDOG_RESET)
        self.assertEqual(native.status_name(summary.stop_status), "watchdog-reset")
        self.assertGreater(summary.total_cycles, 6_100_000)
        self.assertLess(summary.total_cycles, 6_200_000)


@unittest.skipUnless(native.available(), "native core not built (cmake --build cpp/build)")
class SystemStackTests(unittest.TestCase):
    # ld XSP,0x00006EFF -- the broken GB2NGP startup, then spin.
    BROKEN = b"\x47\xFF\x6E\x00\x00" + SPIN
    # ld XSP,0x00006C00 -- the fixed one. Valid: a push pre-decrements to 0x6BFF.
    FIXED = b"\x47\x00\x6C\x00\x00" + SPIN

    def test_a_cart_stack_in_the_system_page_is_reported(self) -> None:
        with _machine(self.BROKEN) as machine:
            summary, records = machine.run(4)
            samples = machine.hw_violation_samples()

        self.assertEqual(summary.stop_status, native.STATUS_COUNT_REACHED)
        self.assertEqual(records[0].status, native.STATUS_OK)
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0].kind_name, "system-stack")
        self.assertEqual(samples[0].pc, ENTRY)        # the code that moved XSP
        self.assertEqual(samples[0].detail, 0x6EFF)   # where it left it

    def test_it_reports_the_crossing_and_not_every_instruction_after_it(self) -> None:
        with _machine(self.BROKEN) as machine:
            machine.run(50_000, record=False)
            self.assertEqual(machine.hw_violations(native.HW_SYSTEM_STACK), 1)

    def test_the_exclusive_boundary_is_not_a_finding(self) -> None:
        with _machine(self.FIXED) as machine:
            machine.run(50_000, record=False)
            xsp = machine.cpu().regs[native.REG_NAMES.index("xsp")]
            self.assertEqual(machine.hw_violations(native.HW_SYSTEM_STACK), 0)
        self.assertEqual(xsp, 0x6C00)

    def test_the_gate_turns_it_into_a_verdict(self) -> None:
        with _machine(self.BROKEN, guard=native.HW_SYSTEM_STACK) as machine:
            summary, records = machine.run(8)

        self.assertEqual(summary.stop_status, native.STATUS_SYSTEM_STACK_VIOLATION)
        self.assertEqual(native.status_name(summary.stop_status), "system-stack-violation")
        self.assertEqual(summary.stop_pc, ENTRY)
        self.assertEqual(records[-1].status, native.STATUS_SYSTEM_STACK_VIOLATION)

    def test_the_two_builds_are_told_apart_by_the_same_core(self) -> None:
        """The whole point: one core, two builds, opposite verdicts."""
        verdicts = {}
        for name, code in (("broken", self.BROKEN), ("fixed", self.FIXED)):
            with _machine(code, guard=native.HW_SYSTEM_STACK) as machine:
                summary, _ = machine.run(50_000, record=False)
                verdicts[name] = native.status_name(summary.stop_status)

        self.assertEqual(verdicts["broken"], "system-stack-violation")
        self.assertEqual(verdicts["fixed"], "count-reached")


if __name__ == "__main__":
    unittest.main()
