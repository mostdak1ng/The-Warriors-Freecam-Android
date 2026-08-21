#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 mostdak1ng
"""Install, inspect and remove the freecam routines in a live PCSX2 session.

This is the development loop only.  The shipped mod is a .pnach that contains
the same words, applied by the emulator itself.
"""

from __future__ import annotations

import argparse
import pathlib
import struct
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import freecam_routine as fc  # noqa: E402
from pine import STATUS_PAUSED, Pine  # noqa: E402


BACKUP_START = 0x00535000
BACKUP_SIZE = 0x3000  # covers all fifteen camera vtables


def backup_path() -> pathlib.Path:
    return pathlib.Path(__file__).parent / f"vtables-{BACKUP_START:08X}.bak"


def owns_tick(word: int) -> bool:
    if word >> 26 != 0x03:
        return False
    return fc.CAVE_WINDOW[0] <= (word & 0x03FFFFFF) << 2 < fc.CAVE_WINDOW[1]


def install(pine: Pine, neutralize: bool) -> None:
    fc.check_layout()

    tick = pine.read32(fc.TICK_HOOK)
    if tick != fc.TICK_HOOK_ORIGINAL and not owns_tick(tick):
        raise SystemExit(f"call site holds 0x{tick:08X}; refusing to touch it")
    live_tick = owns_tick(tick) and (tick & 0x03FFFFFF) << 2 == fc.CAVE_TICK
    if live_tick and pine.status() != STATUS_PAUSED:
        raise SystemExit(
            f"0x{fc.CAVE_TICK:08X} is the running routine; pause PCSX2 to replace it"
        )
    for address, original in fc.GETTER_SLOTS.items():
        live = pine.read32(address)
        if live not in (original, fc.CAVE_GETTER):
            raise SystemExit(f"vtable slot 0x{address:08X} holds 0x{live:08X}")

    pad = pine.read32(fc.PAD_REFRESH_CALL)
    if pad not in (fc.PAD_REFRESH_ORIGINAL, fc.pad_hook_word()):
        raise SystemExit(
            f"pad refresh call holds 0x{pad:08X}; another tool may own it"
        )
    if pad == fc.pad_hook_word() and pine.status() != STATUS_PAUSED:
        raise SystemExit(
            f"0x{fc.CAVE_PAD:08X} is the running pad hook; pause PCSX2 to replace it"
        )

    if not backup_path().exists():
        backup_path().write_bytes(pine.read_block(BACKUP_START, BACKUP_SIZE))

    tick_code = fc.build_tick()
    getter_code = fc.build_getter()
    pad_code = fc.build_pad_hook()
    # Code and state first; the hook words go last so nothing can run against
    # a half-written cave.
    pine.write_block(fc.CAVE_TICK, tick_code.ljust((len(tick_code) + 7) & ~7, b"\0"))
    pine.write_block(fc.CAVE_GETTER, getter_code.ljust((len(getter_code) + 7) & ~7, b"\0"))
    pine.write_block(fc.CAVE_PAD, pad_code.ljust((len(pad_code) + 7) & ~7, b"\0"))
    pine.write_block(fc.STATE, fc.state_blob(neutralize))
    pine.write32(fc.PAD_REFRESH_CALL, fc.pad_hook_word())
    pine.write32(fc.TICK_HOOK, fc.tick_hook_word())
    for address in fc.GETTER_SLOTS:
        pine.write32(address, fc.CAVE_GETTER)
    print(f"tick   {len(tick_code):4d} bytes ({len(tick_code)//4:3d} instructions) at 0x{fc.CAVE_TICK:08X}")
    print(f"getter {len(getter_code):4d} bytes at 0x{fc.CAVE_GETTER:08X} in {len(fc.GETTER_SLOTS)} slots")
    print(f"pad    {len(pad_code):4d} bytes at 0x{fc.CAVE_PAD:08X} hooking 0x{fc.PAD_REFRESH_CALL:08X}")
    print(f"pad neutralised while active: {neutralize}")


def uninstall(pine: Pine) -> None:
    for address, original in fc.GETTER_SLOTS.items():
        pine.write32(address, original)
    pine.write32(fc.TICK_HOOK, fc.TICK_HOOK_ORIGINAL)
    pine.write32(fc.PAD_REFRESH_CALL, fc.PAD_REFRESH_ORIGINAL)
    # Leave the pad buffer in a sane state if we were neutralising it.
    pine.write32(fc.PAD, fc.NEUTRAL_AXES)
    time.sleep(0.25)
    pine.write_block(fc.CAVE_WINDOW[0], b"\0" * (fc.CAVE_WINDOW[1] - fc.CAVE_WINDOW[0]))
    ok = all(pine.read32(a) == o for a, o in fc.GETTER_SLOTS.items())
    ok = ok and pine.read32(fc.TICK_HOOK) == fc.TICK_HOOK_ORIGINAL
    ok = ok and pine.read32(fc.PAD_REFRESH_CALL) == fc.PAD_REFRESH_ORIGINAL
    print("uninstalled" if ok else "UNINSTALL DID NOT VERIFY")


def report(pine: Pine) -> None:
    raw = pine.read_block(fc.STATE, 0x70)
    counter, magic, active, hits = struct.unpack_from("<4I", raw, 0)
    pos = struct.unpack_from("<4f", raw, fc.OFF_POS)
    quat = struct.unpack_from("<4f", raw, fc.OFF_QUAT)
    right = struct.unpack_from("<3f", raw, fc.OFF_RIGHT)
    forward = struct.unpack_from("<3f", raw, fc.OFF_FORWARD)
    neutralize = struct.unpack_from("<I", raw, fc.OFF_NEUTRALIZE)[0]
    print(f"status={pine.status()} magic=0x{magic:08X} frames={counter} "
          f"substitutions={hits} active={active} neutralize={neutralize}")
    print("  pos     %9.3f %9.3f %9.3f" % pos[:3])
    print("  quat    %9.4f %9.4f %9.4f %9.4f" % quat)
    print("  right   %9.4f %9.4f %9.4f" % right)
    print("  forward %9.4f %9.4f %9.4f" % forward)
    length = sum(v * v for v in quat) ** 0.5
    print(f"  |quat| = {length:.6f}")


def verify(pine: Pine) -> None:
    """Check the basis vectors the routine computed against the quaternion."""
    raw = pine.read_block(fc.STATE, 0x60)
    x, y, z, w = struct.unpack_from("<4f", raw, fc.OFF_QUAT)
    right = struct.unpack_from("<3f", raw, fc.OFF_RIGHT)
    forward = struct.unpack_from("<3f", raw, fc.OFF_FORWARD)
    expected_right = (1 - 2 * (y * y + z * z), 2 * (x * y + w * z), 2 * (x * z - w * y))
    expected_forward = (2 * (x * y - w * z), 1 - 2 * (x * x + z * z), 2 * (y * z + w * x))
    print(f"quat {x:.5f} {y:.5f} {z:.5f} {w:.5f}  |q|={(x*x+y*y+z*z+w*w)**0.5:.6f}")
    for name, got, want in (("right", right, expected_right),
                            ("forward", forward, expected_forward)):
        error = max(abs(a - b) for a, b in zip(got, want))
        verdict = "OK" if error < 1e-4 else "MISMATCH"
        print(f"  {name:8s} guest={got[0]:8.5f} {got[1]:8.5f} {got[2]:8.5f}")
        print(f"  {'':8s} host ={want[0]:8.5f} {want[1]:8.5f} {want[2]:8.5f}  "
              f"max error {error:.2e}  {verdict}")


def setflag(pine: Pine, name: str, value: float) -> None:
    offsets = {
        "neutralize": (fc.OFF_NEUTRALIZE, "int"),
        "move": (fc.OFF_MOVE_SCALE, "float"),
        "look": (fc.OFF_LOOK_SCALE, "float"),
        "vstep": (fc.OFF_VSTEP, "float"),
    }
    if name not in offsets:
        raise SystemExit(f"unknown setting {name}; try {', '.join(offsets)}")
    offset, kind = offsets[name]
    if kind == "int":
        pine.write32(fc.STATE + offset, int(value))
    else:
        pine.write32(fc.STATE + offset, struct.unpack("<I", struct.pack("<f", value))[0])
    print(f"{name} = {value}")


def watch(pine: Pine, seconds: float) -> None:
    deadline = time.time() + seconds
    seen = False
    while time.time() < deadline:
        raw = pine.read_block(fc.STATE, 0x80)
        counter, _, active, hits = struct.unpack_from("<4I", raw, 0)
        pos = struct.unpack_from("<3f", raw, fc.OFF_POS)
        quat = struct.unpack_from("<4f", raw, fc.OFF_QUAT)
        forward = struct.unpack_from("<3f", raw, fc.OFF_FORWARD)
        neutral = struct.unpack_from("<I", raw, fc.OFF_NEUTRALIZE)[0]
        camera = pine.read32(fc.CAMERA_POINTER)
        vtable = pine.read32(camera) if 0x00600000 <= camera < 0x02000000 else 0
        if active:
            seen = True
            length = sum(v * v for v in quat) ** 0.5
            print(f"  f={counter} subs={hits} pos=({pos[0]:7.1f},{pos[1]:7.1f},{pos[2]:6.1f}) "
                  f"fwd=({forward[0]:5.2f},{forward[1]:5.2f},{forward[2]:5.2f}) |q|={length:.4f} "
                  f"neutral={neutral} vtable=0x{vtable:08X}",
                  flush=True)
        time.sleep(0.4)
    if not seen:
        print("Select was never seen held")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action",
                        choices=("install", "uninstall", "report", "watch", "verify", "set"))
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--neutralize", action="store_true",
                        help="hand the game a neutral pad while the freecam is active")
    parser.add_argument("--name", default="")
    parser.add_argument("--value", type=float, default=0.0)
    args = parser.parse_args()

    with Pine() as pine:
        if args.action == "install":
            install(pine, args.neutralize)
        elif args.action == "uninstall":
            uninstall(pine)
        elif args.action == "report":
            report(pine)
        elif args.action == "verify":
            verify(pine)
        elif args.action == "set":
            setflag(pine, args.name, args.value)
        else:
            watch(pine, args.seconds)


if __name__ == "__main__":
    main()
