#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 mostdak1ng
"""Freeze the freecam routines into a .pnach the emulator applies by itself.

Everything the development loop writes over PINE lives at fixed addresses, so
the shipped mod is just those words as patch lines.  No host program, no
memory access from outside the emulator, which is what makes it work on Android
where one app cannot touch another's memory.

Deliberately NOT patched: the mutable part of the state block.  ``patch=1``
lines are re-applied every frame, so patching the camera pose or the active
flag would pin them to their initial values and freeze the freecam.  Those
fields start at zero, which is exactly the right initial state.
"""

from __future__ import annotations

import argparse
import pathlib
import struct
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import freecam_routine as fc  # noqa: E402

CRC = "B99A75DE"
SERIAL = "SLUS-21215"
VERSION = "0.2.0"

# State fields that are constant for the whole session and therefore safe to
# re-apply every frame.
CONSTANT_FIELDS = (
    (fc.OFF_MAGIC, "identification marker"),
    (fc.OFF_MOVE_SCALE, "movement units per frame at full stick"),
    (fc.OFF_VSTEP, "vertical units per frame"),
    (fc.OFF_LOOK_SCALE, "look sensitivity"),
    (fc.OFF_ONE, "1.0, used by the normalise step"),
    (fc.OFF_PITCH_LIMIT, "pitch guard"),
    (fc.OFF_FAST, "L2 multiplier"),
    (fc.OFF_SLOW, "R2 multiplier"),
    (fc.OFF_PAUSE_SCALE, "world pause scale"),
    (fc.OFF_CARRY_FWD, "carry distance in front of the camera"),
    (fc.OFF_CARRY_UP, "carry offset below the camera"),
)


def words(blob: bytes) -> list[int]:
    padded = blob.ljust((len(blob) + 3) & ~3, b"\0")
    return list(struct.unpack(f"<{len(padded) // 4}I", padded))


def emit(address: int, value: int) -> str:
    return f"patch=1,EE,{address:08X},word,{value:08X}"


def build() -> str:
    fc.check_layout()
    state = fc.state_blob(neutralize=False)
    lines: list[str] = []

    lines.append(f"gametitle=The Warriors (USA) [{SERIAL}] (v1.03) [CRC {CRC}]")
    lines.append(f"comment=The Warriors Freecam v{VERSION} by mostdak1ng - GPL-3.0-only")
    lines.append("")
    lines.append(f"[The Warriors Freecam v{VERSION}]")
    lines.append("author=mostdak1ng")
    lines.append("description=Select+L3 toggles the freecam. Left stick moves, right "
                 "stick looks, L1/R1 drop and lift, L2 fast, R2 precise. "
                 "Select+Square pauses the world, Select+Circle toggles God mode, "
                 "Select+R3 carries the player, Select+Cross stops the player "
                 "moving with the camera, Select+Triangle hides the readout.")
    lines.append("")
    lines.append("// Runs entirely inside the emulated machine: no external program,")
    lines.append("// so it works the same on desktop PCSX2 and on Android.")
    lines.append("")

    for name, base, blob in (
        ("per-frame tick: reads the pad, turns and moves the camera",
         fc.CAVE_TICK, fc.build_tick()),
        ("camera transform getter: hands our pose to every consumer",
         fc.CAVE_GETTER, fc.build_getter()),
        ("pad refresh hook: captures the physical pad, optionally neutralises it",
         fc.CAVE_PAD, fc.build_pad_hook()),
    ):
        lines.append(f"// {name}")
        for index, word in enumerate(words(blob)):
            lines.append(emit(base + index * 4, word))
        lines.append("")

    lines.append("// Read-only fields of the state block.")
    for offset, note in CONSTANT_FIELDS:
        value = struct.unpack_from("<I", state, offset)[0]
        lines.append(f"// {note}")
        lines.append(emit(fc.STATE + offset, value))
    lines.append("")

    lines.append("// Format strings for the coordinate readout.")
    for offset, length in ((fc.OFF_FMT_CAM, 16), (fc.OFF_FMT_NUM, 16),
                           (fc.OFF_FMT_PLR, 16)):
        for index, word in enumerate(words(state[offset : offset + length])):
            lines.append(emit(fc.STATE + offset + index * 4, word))
    lines.append("")

    lines.append("// Authorship, left in memory where a memory search will find it.")
    signature = state[fc.OFF_SIGNATURE : fc.OFF_SIGNATURE + len(fc.SIGNATURE)]
    for index, word in enumerate(words(signature)):
        lines.append(emit(fc.STATE + fc.OFF_SIGNATURE + index * 4, word))
    lines.append("")

    lines.append("// Hooks. These are the only words belonging to the game that change.")
    lines.append(f"// per-frame call site (was {fc.TICK_HOOK_ORIGINAL:08X})")
    lines.append(emit(fc.TICK_HOOK, fc.tick_hook_word()))
    lines.append(f"// pad refresh call site (was {fc.PAD_REFRESH_ORIGINAL:08X})")
    lines.append(emit(fc.PAD_REFRESH_CALL, fc.pad_hook_word()))
    lines.append("// transform getter in all fifteen camera vtables, so the freecam")
    lines.append("// keeps working through cutscenes and scripted cameras.")
    for address in sorted(fc.GETTER_SLOTS):
        lines.append(emit(address, fc.CAVE_GETTER))

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=pathlib.Path,
                        default=pathlib.Path(__file__).parent.parent / f"{CRC}.pnach")
    args = parser.parse_args()

    text = build()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="ascii", newline="\n")
    patch_lines = sum(1 for line in text.splitlines() if line.startswith("patch="))
    print(f"wrote {args.out}")
    print(f"  {patch_lines} patch lines, {len(text)} bytes")
    print(f"  code {len(fc.build_tick()) + len(fc.build_getter()) + len(fc.build_pad_hook())} bytes")
    print(f"  hooks: 2 call sites + {len(fc.GETTER_SLOTS)} vtable slots")


if __name__ == "__main__":
    main()
