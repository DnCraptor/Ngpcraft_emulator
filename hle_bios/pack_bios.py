#!/usr/bin/env python3
"""Turn the linked S-record (main.s24) into a flat 64 KiB BIOS image.

tuconv emits Motorola S-records based at 0xFF0000. ngpc_load_bios wants a
raw 65536-byte image (offset = addr - 0xFF0000). We parse the S1/S2/S3 data
records, drop them into a 0xFF-filled buffer, and write bios_hle.bin.
"""
import sys
from pathlib import Path

BIOS_BASE = 0xFF0000
BIOS_SIZE = 0x10000


def parse_srec(text: str) -> dict[int, int]:
    mem: dict[int, int] = {}
    for line in text.splitlines():
        line = line.strip()
        if len(line) < 4 or line[0] != "S":
            continue
        t = line[1]
        if t not in "123":
            continue
        alen = {"1": 2, "2": 3, "3": 4}[t]           # address bytes
        count = int(line[2:4], 16)
        body = line[4:]
        addr = int(body[: alen * 2], 16)
        data = body[alen * 2 : (count - alen - 1) * 2 + alen * 2]
        for i in range(0, len(data), 2):
            mem[addr + i // 2] = int(data[i : i + 2], 16)
    return mem


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: pack_bios.py <main.s24> <bios_hle.bin>", file=sys.stderr)
        return 2
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    mem = parse_srec(src.read_text())
    if not mem:
        print("no S-record data parsed", file=sys.stderr)
        return 1
    lo, hi = min(mem), max(mem)
    img = bytearray(b"\xff" * BIOS_SIZE)
    for a, v in mem.items():
        if a < BIOS_BASE or a >= BIOS_BASE + BIOS_SIZE:
            print(f"warning: address {a:#08x} outside BIOS window, skipped", file=sys.stderr)
            continue
        img[a - BIOS_BASE] = v
    dst.write_bytes(img)
    print(f"packed {len(mem)} bytes ({lo:#08x}..{hi:#08x}) -> {dst} ({len(img)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
