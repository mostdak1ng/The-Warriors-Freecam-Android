#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 mostdak1ng
"""Log which pad buttons actually reach the game, by name.

Reads the freecam's pad capture, so it shows the physical sample even when the
game is being handed a neutral one.
"""

from __future__ import annotations

import pathlib
import struct
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import freecam_routine as fc  # noqa: E402
from pine import Pine  # noqa: E402

NAMES = {
    0x0001: "L2", 0x0002: "R2", 0x0004: "L1", 0x0008: "R1",
    0x0010: "Triangle", 0x0020: "Circle", 0x0040: "Cross", 0x0080: "Square",
    0x0100: "Select", 0x0200: "L3", 0x0400: "R3", 0x0800: "Start",
    0x1000: "Up", 0x2000: "Right", 0x4000: "Down", 0x8000: "Left",
}


def decode(mask: int) -> str:
    if not mask:
        return "-"
    return "+".join(name for bit, name in NAMES.items() if mask & bit)


def main() -> None:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 45.0
    deadline = time.time() + seconds
    seen: dict[int, int] = {}
    previous = None
    with Pine() as pine:
        while time.time() < deadline:
            capture = pine.read_block(fc.STATE + fc.OFF_PAD_CAPTURE, 8)
            mask = struct.unpack_from("<H", capture, 4)[0]
            if mask and mask != previous:
                seen[mask] = seen.get(mask, 0) + 1
                print(f"  0x{mask:04X}  {decode(mask)}", flush=True)
            previous = mask
            time.sleep(0.05)
    print("\nbuttons that reached us:")
    union = 0
    for mask in seen:
        union |= mask
    for bit, name in NAMES.items():
        if union & bit:
            print(f"  {name}")
    for bit, name in NAMES.items():
        if not union & bit:
            print(f"  (never seen: {name})")


if __name__ == "__main__":
    main()
