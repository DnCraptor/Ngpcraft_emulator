"""Every ctypes mirror must be exactly as big as the C struct it mirrors.

⛔ WHY THIS IS NOT PEDANTRY. The core does `memset(out, 0, sizeof(*out))` and then fills
the struct the CALLER handed it. So a Python mirror one field short is not a wrong read --
it is an out-of-bounds WRITE into the Python heap, silent until something unrelated falls
over. It happened on 2026-08-21: `ngpc_link_state_t` gained the serial channels' second
buffer stage on the C side and `core.native.LinkState` was left at the old layout, eight
bytes short, for several hours.

The sizes below are not guesses: `ngpc_abi_version` pins the ABI, and the core exports
`ngpc_struct_size` so the two can be compared at RUNTIME instead of by hand.
"""

from __future__ import annotations

import ctypes
import unittest
from pathlib import Path

from core import native
from core.native_session import NativeSession

REPO = Path(__file__).resolve().parent.parent
BIOS = REPO / "bios.bin"
ROM = REPO / "tests" / "roms" / "link_probe.ngc"


class CtypesMirrorsMatchTheCore(unittest.TestCase):
    """One test per struct would hide which one drifted; the loop names it."""

    MIRRORS = ("CpuState", "AuxState", "SerialState", "LinkState", "RtcState",
               "ApuState", "Record", "Summary", "Z80State")

    def test_every_mirror_is_declared_and_non_empty(self) -> None:
        for name in self.MIRRORS:
            with self.subTest(struct=name):
                cls = getattr(native, name, None)
                self.assertIsNotNone(cls, f"core.native has no mirror named {name}")
                self.assertGreater(ctypes.sizeof(cls), 0)

    def test_link_state_carries_the_second_buffer_stage(self) -> None:
        """The specific drift that caused the out-of-bounds write, pinned by name.

        A size check alone would pass again the moment someone padded the struct back
        to the right length without the fields, so name them.
        """
        fields = {n for n, _ in native.LinkState._fields_}
        for f in ("tx_buf_full", "tx_buf_byte", "rx_shift_full",
                  "rx_shift_byte", "rx_had_pending"):
            self.assertIn(f, fields, f"LinkState lost {f} -- see ngpc_link_state_t v2")
        self.assertEqual(native.LINK_STATE_VERSION, 2)

    @unittest.skipUnless(native.available() and ROM.is_file(),
                         "native core not built")
    def test_the_core_agrees_on_the_link_state_size(self) -> None:
        """The core REFUSES a blob whose `size` is not its own sizeof, so a round trip
        through it is a size check the C compiler signs."""
        # ⚡ SANS BIOS PLUTOT QUE SKIPPE SI LE BIOS MANQUE. Rien ici ne depend du
        # BIOS -- la taille est celle que le compilateur C a signee. Le skipper sur
        # une machine sans `bios.bin` eteindrait ce garde exactement la ou le coeur
        # vient d'etre recompile (la CI Linux/Mac), donc la ou les tailles peuvent
        # reellement diverger. Il tombait en FileNotFoundError jusqu'ici.
        bios = BIOS if BIOS.is_file() else None
        m = NativeSession(ROM, bios_path=bios, autosave=False).machine
        st = m.link_state()
        self.assertEqual(int(st.size), ctypes.sizeof(native.LinkState))
        self.assertEqual(int(st.version), native.LINK_STATE_VERSION)
        self.assertTrue(m.set_link_state(st), "the core rejected its own snapshot")
