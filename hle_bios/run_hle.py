"""Launch the emulator shell using the clean-room HLE BIOS, for playtesting the
'out of the box, no bios.bin' experience -- WITHOUT changing your saved settings.

    python hle_bios/run_hle.py [optional_rom.ngc]

It forces the BIOS to hle_bios/bios_hle.bin and the instant hand-off mode (the
working path); your real bios.bin and your QSettings are left untouched.
"""
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.chdir(REPO)

HLE = str(REPO / "hle_bios" / "bios_hle.bin")
if not Path(HLE).is_file():
    raise SystemExit("bios_hle.bin not found -- run `bash hle_bios/build.sh` first.")

import ngpc_settings  # noqa: E402

# Force the HLE image + hand-off mode at resolution time (no persisted change).
ngpc_settings.bios_path = lambda s: HLE
ngpc_settings.real_bios = lambda s: False

import ngpc_shell  # noqa: E402

print(f"Launching emulator with clean-room HLE BIOS:\n  {HLE}")
raise SystemExit(ngpc_shell.main())
