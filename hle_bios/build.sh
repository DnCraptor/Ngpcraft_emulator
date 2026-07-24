#!/usr/bin/env bash
# Build the clean-room HLE BIOS image (official Toshiba chain).
set -e
cd "$(dirname "$0")"
export THOME="${THOME:-C:\\t900}"
python gen_crt0.py
python tools/build_utils.py asm src/crt0.asm build/crt0.rel
python tools/build_utils.py link build/main.abs ngpc_bios.lcf build/crt0.rel
# tuconv writes main.s24 into the CWD
python pack_bios.py main.s24 bios_hle.bin
echo "OK -> bios_hle.bin"
