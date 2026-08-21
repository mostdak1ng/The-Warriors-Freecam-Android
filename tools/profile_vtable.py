#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 mostdak1ng
"""Count how often each method of a vtable is called.

Every entry is redirected to a stub that bumps a counter and tail-jumps to the
original function, so behaviour is unchanged.  Reading the counters after a few
seconds of play shows which methods run per frame - that is the camera update
we need, without guessing from static disassembly.

Stubs only clobber t0/t1, which a callee is free to use, and reach the original
with ``j`` so the caller's return address survives.
"""

from __future__ import annotations

import argparse
import pathlib
import struct
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from mips import T0, T1, Asm  # noqa: E402
from pine import Pine  # noqa: E402

CAVE = 0x000F1000
COUNTERS = 0x000F1C00
STUB_STRIDE = 0x20
MAX_SLOTS = 48

FOLLOW_CAMERA_VTABLE = 0x00535D50
CAMERA_POINTER = 0x005D9148


def slots(pine: Pine, vtable: int) -> list[tuple[int, int]]:
    """Return (vtable address, function pointer) for every plausible entry."""
    raw = pine.read_block(vtable, 0x100)
    found = []
    for index in range(0x100 // 4):
        word = struct.unpack_from("<I", raw, index * 4)[0]
        if 0x00100000 <= word < 0x00600000:
            found.append((vtable + index * 4, word))
    return found[:MAX_SLOTS]


def stub(index: int, original: int) -> bytes:
    asm = Asm(CAVE + index * STUB_STRIDE)
    asm.li(T0, COUNTERS + index * 4)
    asm.lw(T1, 0x00, T0)
    asm.addiu(T1, T1, 1)
    asm.sw(T1, 0x00, T0)
    asm.j(original)
    asm.nop()
    return asm.assemble()


def backup_path(vtable: int) -> pathlib.Path:
    return pathlib.Path(__file__).parent / f"vtable-{vtable:08X}.bak"


def install(pine: Pine, vtable: int) -> None:
    entries = slots(pine, vtable)
    if not entries:
        raise SystemExit(f"no function pointers found in vtable 0x{vtable:08X}")
    # Keep the untouched bytes on disk.  Never infer originals from our own
    # stubs: a wrong guess leaves the vtable pointing at nothing.
    backup_path(vtable).write_bytes(pine.read_block(vtable, 0x100))
    for index, (address, original) in enumerate(entries):
        if COUNTERS <= original < COUNTERS + 0x100 or CAVE <= original < COUNTERS:
            raise SystemExit("vtable already redirected; uninstall first")
        blob = stub(index, original)
        pine.write_block(CAVE + index * STUB_STRIDE, blob.ljust(STUB_STRIDE, b"\0"))
    pine.write_block(COUNTERS, b"\0" * 0x100)
    # Flip the pointers only once every stub is in place.
    for index, (address, _) in enumerate(entries):
        pine.write32(address, CAVE + index * STUB_STRIDE)
    print(f"instrumented {len(entries)} entries of vtable 0x{vtable:08X}")
    for index, (address, original) in enumerate(entries):
        print(f"  [{index:2d}] 0x{address:08X} -> 0x{original:08X}")


def original_vtable(vtable: int) -> bytes:
    """Prefer the install-time backup, fall back to the ELF inside the ISO."""
    path = backup_path(vtable)
    if path.exists():
        return path.read_bytes()
    from elf_bytes import load_elf, read_vaddr

    iso = pathlib.Path("C:/DevMST/TheWarriors/Warriors, The (USA).iso")
    return read_vaddr(load_elf(iso), vtable, 0x100)


def uninstall(pine: Pine, vtable: int) -> None:
    good = original_vtable(vtable)
    has_code = any(
        0x00100000 <= struct.unpack_from("<I", good, index)[0] < 0x00600000
        for index in range(0, 0x100, 4)
    )
    if not has_code:
        raise SystemExit("recovered vtable holds no code pointers; aborting")
    # Restore the pointers first, then let in-flight calls drain, then clear.
    pine.write_block(vtable, good)
    time.sleep(0.25)
    pine.write_block(CAVE, b"\0" * (MAX_SLOTS * STUB_STRIDE))
    pine.write_block(COUNTERS, b"\0" * 0x100)
    live = pine.read_block(vtable, 0x100)
    print(f"vtable restored, {sum(1 for i in range(0, 0x100, 4) if live[i:i+4] != good[i:i+4])} words still differ")


def report(pine: Pine, vtable: int, seconds: float) -> None:
    first = pine.read_block(COUNTERS, 0x100)
    time.sleep(seconds)
    second = pine.read_block(COUNTERS, 0x100)
    frames = seconds * 30.0
    print(f"calls over {seconds:.0f}s (~{frames:.0f} frames at 30fps):")
    for index in range(MAX_SLOTS):
        start = struct.unpack_from("<I", first, index * 4)[0]
        end = struct.unpack_from("<I", second, index * 4)[0]
        delta = end - start
        if not end:
            continue
        # The stub is lui, ori, lw, addiu, sw, j, nop: the jump is word five.
        target = pine.read32(CAVE + index * STUB_STRIDE + 20)
        original = (target & 0x03FFFFFF) << 2 if target >> 26 == 0x02 else 0
        per_frame = delta / frames
        note = "  <== per frame" if 0.8 <= per_frame <= 1.2 else ""
        print(
            f"  [{index:2d}] 0x{original:08X}  total={end:7d}  +{delta:6d}  "
            f"{per_frame:5.2f}/frame{note}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "uninstall", "report"))
    parser.add_argument("--vtable", type=lambda v: int(v, 0), default=FOLLOW_CAMERA_VTABLE)
    parser.add_argument("--seconds", type=float, default=5.0)
    args = parser.parse_args()

    with Pine() as pine:
        if args.action == "install":
            install(pine, args.vtable)
        elif args.action == "uninstall":
            uninstall(pine, args.vtable)
        else:
            report(pine, args.vtable, args.seconds)


if __name__ == "__main__":
    main()
