"""The clean-room BIOS image's COM rings, at the unit level.

`tests/test_link_cable.py` proves two consoles exchange bytes end to end. That is the
claim that matters, but it exercises one path: a byte at a time, with a peer. These
pin the parts it does not reach -- the block transfers (two games use them), the ring
wrap, and the structural invariant a stack slip broke while this was being written.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
IMAGE = REPO / "hle_bios" / "bios_hle.bin"

from core import native  # noqa: E402

XWA, XBC, XDE, XHL = 0, 1, 2, 3
CODE, BUF = 0x004000, 0x004100
COM_TXRING, COM_RXRING = 0x006C80, 0x006CC0
COM_TXCNT, COM_RXCNT = 0x006D00, 0x006D01
COM_TXHEAD, COM_TXTAIL, COM_RXHEAD, COM_RXTAIL = 0x006D02, 0x006D03, 0x006D04, 0x006D05
V_INIT, V_CREATE, V_GET, V_SENDSTAT, V_RECVSTAT, V_BUFSEND, V_BUFGET = (
    0x10, 0x13, 0x14, 0x17, 0x18, 0x19, 0x1A)


def _rom() -> bytes:
    rom = bytearray(b"\xFF" * 0x100000)
    rom[0:28] = b" LICENSED BY SNK CORPORATION"
    rom[0x1C:0x20] = (0x200040).to_bytes(4, "little")
    rom[0x23] = 0x10
    rom[0x40] = 0x05
    return bytes(rom)


class PushPopBalance(unittest.TestCase):
    """Every push must have its pop -- checked on the generated source, no toolchain.

    Not a theoretical worry: adding a critical section to the transmit pump left a
    `push sr` without its `pop`, and the image still assembled, linked and packed
    perfectly. A stack that unwinds one word short returns into nowhere.
    """

    def test_every_critical_section_is_closed(self):
        """Counted on `sr` alone, and deliberately so.

        A general push/pop audit cannot work here: every ISR trampoline PUSHES the
        user handler's address and `ret`s to it -- an intentional imbalance, sixteen
        times over -- so a whole-image tally is noise. `push sr` has no such use: it
        opens a critical section and the only correct thing to do with it is close it.
        That is exactly the slip this caught, where a masked section returned with the
        stack one word short and the image still assembled, linked and packed fine.
        """
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "gen_crt0", REPO / "hle_bios" / "gen_crt0.py")
        gen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gen)

        masked, opened, leaks = False, 0, []
        for n, line in enumerate(gen.gen().splitlines(), 1):
            if re.match(r"^\s+push\s+sr\b", line):
                masked, opened = True, opened + 1
            elif re.match(r"^\s+pop\s+sr\b", line):
                masked = False
            elif masked and re.match(r"^\s+ret(i)?\b", line):
                leaks.append(n)

        # Counting occurrences would not do either: a masked routine has one `push`
        # and one `pop` PER EXIT, so the totals differ legitimately. What must hold is
        # that no return is reachable while still masked.
        self.assertGreater(opened, 0, "the COM rings should run masked")
        self.assertEqual(leaks, [], f"returns while a critical section is open: {leaks}")


@unittest.skipUnless(IMAGE.exists(), "hle_bios/bios_hle.bin not built")
@unittest.skipUnless(native.available(), "native core not built")
class ComRings(unittest.TestCase):
    def setUp(self) -> None:
        self.m = native.NativeMachine(_rom(), bios=IMAGE.read_bytes())
        self.m.reset(bios_handoff=True)
        self._call(wa=(V_INIT << 8))

    def tearDown(self) -> None:
        self.m.close()

    def _call(self, *, wa: int, bc: int = 0, de: int = 0, hl: int = 0):
        self.m.write(CODE, bytes([0xF9, 0x05]))
        st = self.m.cpu(); st.pc = CODE; self.m.set_cpu(st)
        st = self.m.cpu()
        b3 = st.regs if st.rfp == 3 else st.banks[3]
        b3[XWA], b3[XBC], b3[XDE], b3[XHL] = wa, bc, de, hl
        self.m.set_cpu(st)
        summary, _ = self.m.run(2_000_000, record=False)
        self.assertEqual(summary.stop_status, native.STATUS_HALTED)
        st = self.m.cpu()
        b3 = st.regs if st.rfp == 3 else st.banks[3]
        return b3[XWA] & 0xFF, (b3[XBC] >> 8) & 0xFF, b3[XWA] & 0xFFFF   # RA3, RB3, RWA3

    def test_cominit_empties_the_rings(self):
        for addr in (COM_TXCNT, COM_RXCNT, COM_TXHEAD, COM_TXTAIL, COM_RXHEAD, COM_RXTAIL):
            self.assertEqual(self.m.read(addr, 1)[0], 0, f"{addr:#06x} not cleared")

    def test_a_queued_byte_lands_in_the_ring(self):
        ra, _, _ = self._call(wa=(V_CREATE << 8), bc=0xA5 << 8)
        self.assertEqual(ra, 0x00, "COM_BUF_OK expected")
        self.assertEqual(self.m.read(COM_TXRING, 1)[0], 0xA5)
        self.assertEqual(self.m.read(COM_TXCNT, 1)[0], 1)
        self.assertEqual(self.m.read(COM_TXHEAD, 1)[0], 1)

    def test_the_ring_reports_full_rather_than_overwriting(self):
        """64 bytes is the whole ring. The 65th must be REFUSED, not wrapped over an
        unsent byte -- a silently overwritten queue is a corrupted stream."""
        for i in range(64):
            self.assertEqual(self._call(wa=(V_CREATE << 8), bc=i << 8)[0], 0x00)
        self.assertEqual(self._call(wa=(V_CREATE << 8), bc=0xFF << 8)[0], 0xFF,
                         "COM_BUF_OVER expected")
        self.assertEqual(self.m.read(COM_TXCNT, 1)[0], 64)
        self.assertEqual(self.m.read(COM_TXRING, 64), bytes(range(64)))

    def test_getting_from_an_empty_ring_says_empty(self):
        ra, _, rwa = self._call(wa=(V_GET << 8))
        self.assertEqual(ra, 0x01, "COM_BUF_EMPTY expected")
        self.assertEqual(rwa, 0x0101, "drain loops compare RW3 as well as RA3")

    def test_a_received_byte_comes_back_out(self):
        self.m.write(COM_RXRING, bytes([0x5A]))
        self.m.write(COM_RXCNT, bytes([1]))
        ra, rb, _ = self._call(wa=(V_GET << 8))
        self.assertEqual((ra, rb), (0x00, 0x5A))
        self.assertEqual(self.m.read(COM_RXCNT, 1)[0], 0)

    def test_the_ring_wraps_at_64(self):
        self.m.write(COM_RXHEAD, bytes([63]))
        self.m.write(COM_RXTAIL, bytes([63]))
        self.m.write(COM_RXRING + 63, bytes([0x11]))
        self.m.write(COM_RXCNT, bytes([1]))
        ra, rb, _ = self._call(wa=(V_GET << 8))
        self.assertEqual((ra, rb), (0x00, 0x11))
        self.assertEqual(self.m.read(COM_RXTAIL, 1)[0], 0, "the tail did not wrap to 0")

    def test_a_block_send_queues_every_byte(self):
        payload = bytes(range(10, 30))
        self.m.write(BUF, payload)
        ra, rb, _ = self._call(wa=(V_BUFSEND << 8), bc=len(payload) << 8, hl=BUF)
        self.assertEqual(rb, 0, "RB3 should report nothing left over")
        self.assertEqual(self.m.read(COM_TXRING, len(payload)), payload)
        self.assertEqual(self.m.read(COM_TXCNT, 1)[0], len(payload))

    def test_a_block_send_stops_at_the_ring_and_reports_the_remainder(self):
        payload = bytes(range(100))
        self.m.write(BUF, payload)
        _, rb, _ = self._call(wa=(V_BUFSEND << 8), bc=100 << 8, hl=BUF)
        self.assertEqual(self.m.read(COM_TXCNT, 1)[0], 64)
        self.assertEqual(rb, 100 - 64, "RB3 must say how many did not fit")

    def test_a_block_get_returns_what_is_there_and_no_more(self):
        self.m.write(COM_RXRING, bytes(range(200, 205)))
        self.m.write(COM_RXCNT, bytes([5]))
        self.m.write(BUF, b"\x00" * 16)
        _, rb, _ = self._call(wa=(V_BUFGET << 8), bc=16 << 8, hl=BUF)
        self.assertEqual(self.m.read(BUF, 5), bytes(range(200, 205)))
        self.assertEqual(self.m.read(BUF + 5, 1), b"\x00", "wrote past what it had")
        self.assertEqual(rb, 16 - 5, "RB3 must say how many were missing")

    def test_the_status_words_report_the_counts(self):
        """serial.h: the low byte of the status word IS the number of bytes in that
        buffer (COM_COUNT_MASK). Returning a flat zero looks fine while idle and lies
        the moment anything is queued."""
        for i in range(3):
            self._call(wa=(V_CREATE << 8), bc=i << 8)
        self.m.write(COM_RXCNT, bytes([7]))
        self.assertEqual(self._call(wa=(V_SENDSTAT << 8))[2] & 0xFF, 3)
        self.assertEqual(self._call(wa=(V_RECVSTAT << 8))[2] & 0xFF, 7)


if __name__ == "__main__":
    unittest.main()
