"""One timer match, one micro-DMA transfer -- whatever the CPU happens to be doing.

⛔ THE BUG THIS CONDEMNS. "Une ligne glitch en bas de l'ecran juste au dessus de la
barre de special" -- SNK Gals' Fighters, playtest 2026-07-28. The game hands its bottom
HUD to SCR2 with a per-HBlank scroll table: micro-DMA channel 0 is armed on INTT0 and
walks one 16-bit entry per scanline into S2SO (0x8034), and the entry that carries
S2SO.V = 8 is the one that separates the playfield from the HUD.

`irq_pending` is a BITMASK, and the request used to be answered in deliver_irq, which
runs BETWEEN INSTRUCTIONS. So two matches falling inside one long instruction OR-ed the
same bit twice and produced ONE transfer. MEASURED on the player's own save state, 120
frames: a healthy frame delivers 152 entries and the split arrives on line 135, in time
to be latched for line 136. The frames that lost one -- always at line 0 or 1, where the
VBlank handler's block copy straddles the first HBlanks of the picture -- delivered 151,
every entry below ran a line late, and line 136 came out as a strip of playfield above
the special bar. Line 136 was junk on 57 of 240 frames before, 0 of 240 after.

⚖️ WHY THE CPU CANNOT BE ALLOWED TO MATTER. The DMA controller is not the processor: it
steals a bus cycle when the request happens, and it has no notion of an instruction
boundary. The pulses it counts are the K2GE's, 152 a frame on the raster's own clock
(see test_hint_raster_anchor). So the transfer count over a frame is a property of the
RASTER ALONE -- and that is exactly what this test measures.

🧪 THE CONTROL GROUP IS THE POINT. The same machine, the same frames, the same armed
channel, twice: once with the CPU parked on a two-byte spin, once with it running a
block copy long enough to straddle scanlines. If the two counts differ, the CPU changed
a schedule that belongs to the video chip. Without the parked run there is no baseline
and "152 transfers" would just be a number this test asserted into existence.
"""

from __future__ import annotations

import unittest

from core import native

# The micro-DMA channel-0 control registers, as memory.cpp indexes cregs.
CREG_DMAS0, CREG_DMAD0, CREG_DMAC0, CREG_DMAM0 = 0x00, 0x10, 0x20, 0x22
DMA0V = 0x00007C                 # channel 0's start vector
INTT0_VECTOR = 0x10              # ...and the vector a timer-0 match raises

TRUN, TREG0, T01MOD = 0x0020, 0x0022, 0x0024
TRUN_ARMED = 0x81                # PRRUN + T0RUN

TABLE = 0x005000                 # a quiet stretch of RAM to walk
SINK = 0x006000                  # ...and a quiet byte to drop the entries on
DMAM_INCREMENT_SOURCE_BYTE = 0x08   # kind 2 = (DMAD) <- (DMAS+), zz 0 = one byte

REG_XWA, REG_XBC, REG_XIX, REG_XIY = 0, 1, 4, 5
SPIN, BLOCK_COPY = 0x200040, 0x200044

FRAMES = 20
PULSES_PER_FRAME = 152           # lines 198 and 0..150, ngpcspec.txt


def _rom() -> bytes:
    """`nop ; jr $` at 0x200040, and `ldirw (XIX+),(XIY+) ; jr $` at 0x200044.

    `95 11` is LDIRW (XIX+),(XIY+) -- the encoding this project's own disassembler
    and execute tests already pin (test_decode, test_execute). One of them, with a
    large BC, is a single instruction that runs for several scanlines: the shape of
    the VBlank tilemap upload that was swallowing Gals' Fighters' HBlank requests.
    """
    rom = bytearray(b"\xFF" * 0x100000)
    rom[0:28] = b" LICENSED BY SNK CORPORATION"
    rom[0x1C:0x20] = (SPIN).to_bytes(4, "little")
    rom[0x23] = 0x10
    rom[0x40:0x44] = b"\x00\x68\xFE\x00"          # nop ; jr $   (park)
    rom[0x44:0x48] = b"\x95\x11\x68\xFE"          # ldirw ; jr $ (a long instruction)
    return bytes(rom)


@unittest.skipUnless(native.available(), "native core not built (cmake --build cpp/build)")
class MicroDmaRequestsDoNotCoalesceTests(unittest.TestCase):

    def _machine(self) -> native.NativeMachine:
        m = native.NativeMachine(_rom())
        m.reset(bios_handoff=True)
        # Timer 0 counts the HBlank pin and matches on every pulse.
        m.write(T01MOD, bytes([0x00]))            # T0 clocked by TI0
        m.write(TREG0, bytes([0x01]))             # one match per pulse
        # Channel 0 answers INTT0 by copying one table byte to a fixed sink.
        cpu = m.cpu()
        cpu.cregs[CREG_DMAS0] = TABLE
        cpu.cregs[CREG_DMAD0] = SINK
        cpu.cregs[CREG_DMAC0] = 0xFFFF
        cpu.cregs[CREG_DMAM0] = DMAM_INCREMENT_SOURCE_BYTE
        m.set_cpu(cpu)
        m.write(DMA0V, bytes([INTT0_VECTOR]))
        m.write(TRUN, bytes([TRUN_ARMED]))
        return m

    def _transfers(self, m: native.NativeMachine, *, busy: bool) -> int:
        """Transfers over FRAMES frames, read off the source pointer the DMA walks."""
        m.run_frames(2)                            # settle: the channel is armed
        before = m.cpu().cregs[CREG_DMAS0]
        for _ in range(FRAMES):
            if busy:
                # Re-launch the block copy each frame, so a long instruction is in
                # flight while the HBlanks come in -- the collapse window.
                cpu = m.cpu()
                cpu.pc = BLOCK_COPY
                cpu.regs[REG_XBC] = 0x0400         # words: several scanlines' worth
                cpu.regs[REG_XIX] = 0x007000
                cpu.regs[REG_XIY] = 0x007800
                m.set_cpu(cpu)
            m.run_frames(1)
        return m.cpu().cregs[CREG_DMAS0] - before

    def test_the_channel_walks_its_table_at_the_rasters_pace(self) -> None:
        with self._machine() as m:
            parked = self._transfers(m, busy=False)
        self.assertEqual(
            parked, PULSES_PER_FRAME * FRAMES,
            "an armed channel must take one transfer per HBlank pulse, and there are "
            f"{PULSES_PER_FRAME} of those in a frame",
        )

    def test_a_long_instruction_cannot_swallow_a_transfer(self) -> None:
        # THE CONTROL GROUP IS test_the_channel_walks_... above: it pins what the
        # raster alone produces. This one only changes what the CPU is doing.
        with self._machine() as m:
            busy = self._transfers(m, busy=True)
        self.assertEqual(
            busy, PULSES_PER_FRAME * FRAMES,
            "a block copy in flight lost HBlank DMA requests: they were being OR-ed "
            "into one pending bit and answered once, between instructions",
        )


if __name__ == "__main__":
    unittest.main()
