#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 mostdak1ng
"""Diagnose why a guest-side camera write does not stick.

Phase 1 writes the camera position once and samples it back for a second, which
shows whether the game rewrites the transform every frame.

Phase 2 reproduces what the desktop mod does: write the pose as fast as the
host can for a few seconds, so the effect is visible on screen.

Both phases restore the original position.  Waits for PCSX2 to be running.
"""

from __future__ import annotations

import pathlib
import struct
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from pine import Pine  # noqa: E402

CAMERA_POINTER = 0x005D9148
WAIT_SECONDS = 180.0


def wait_for_running(pine: Pine) -> bool:
    deadline = time.time() + WAIT_SECONDS
    while time.time() < deadline:
        if pine.status() == 0:
            return True
        time.sleep(0.5)
    return False


def transform(pine: Pine) -> int:
    return pine.read32(CAMERA_POINTER) + 0x10


def read_pose(pine: Pine, address: int) -> tuple[float, ...]:
    return struct.unpack("<8f", pine.read_block(address, 0x20))


def write_float(pine: Pine, address: int, value: float) -> None:
    pine.write32(address, struct.unpack("<I", struct.pack("<f", value))[0])


def main() -> None:
    with Pine() as pine:
        if not wait_for_running(pine):
            print("PCSX2 stayed paused; nothing measured")
            return

        address = transform(pine)
        original = read_pose(pine, address)
        print(f"transform 0x{address:08X}")
        print("pos %.3f %.3f %.3f" % original[:3])

        print("\nphase 1: single write of x+10, sampled back")
        write_float(pine, address, original[0] + 10.0)
        reverted_at = None
        for index in range(15):
            live = struct.unpack("<3f", pine.read_block(address, 12))
            drift = abs(live[0] - (original[0] + 10.0))
            print(f"  t+{index * 40:4d}ms  x={live[0]:9.3f} y={live[1]:9.3f} z={live[2]:9.3f}")
            if reverted_at is None and drift > 0.5:
                reverted_at = index * 40
            time.sleep(0.04)
        if reverted_at is None:
            print("  -> the write SURVIVED: the game does not rewrite the transform")
        else:
            print(f"  -> the game overwrote it after ~{reverted_at} ms")

        print("\nphase 2: host-rate rewrite for 5 s (watch the screen)")
        target = list(original)
        deadline = time.time() + 5.0
        writes = 0
        while time.time() < deadline:
            live_address = transform(pine)
            target[1] = original[1] + 8.0
            blob = struct.pack("<8f", *target)
            pine.write_block(live_address, blob)
            writes += 1
        print(f"  {writes} writes ({writes / 5.0:.0f}/s)")
        moved = struct.unpack("<3f", pine.read_block(transform(pine), 12))
        print("  after: %.3f %.3f %.3f" % moved)

        pine.write_block(transform(pine), struct.pack("<8f", *original))
        print("\noriginal pose restored")


if __name__ == "__main__":
    main()
