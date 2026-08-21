#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 mostdak1ng
"""Ask the game to draw our own string, using its own tutorial-text path.

The scripting table registers ``HUDSetTutorialText``; that binding pulls its
arguments out of the script VM and then calls ``0x001B4980(text, flags)``,
which hands them to the HUD singleton.  Calling that inner function directly
skips the VM entirely.

A one-shot flag drives it: set the flag over PINE, the next frame runs the call
once and clears it.  Nothing happens until the flag is set, so leaving this
installed is harmless.

Uses 0x000F1000 upwards, clear of the addresses the freecam .pnach owns.
"""

from __future__ import annotations

import argparse
import pathlib
import struct
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from mips import A0, A1, A2, A3, RA, SP, T0, T1, Asm  # noqa: E402
from pine import Pine  # noqa: E402

CAVE = 0x000F2000
FLAG = 0x000F2200
TEXT = 0x000F2210

# Two channels found through the scripting table.  Tutorial text is exclusive:
# it holds the slot until it ends, and ours never does, so the game's own
# tutorials queue up behind it.  Announce is the alternative worth trying.
SET_TUTORIAL_TEXT = 0x001B4980  # HUDSetTutorialText -> (text, flags)
SET_ANNOUNCE_MSG = 0x001B5B08   # HUDSetAnnounceMsg  -> (text, a1, a2)
CLEAR_TUTORIAL = 0x001B9290     # the clear the null-text path calls, but only
HUD_OBJECT = 0x00609250         # after a virtual check that our endless text fails
FLUSH_TUTORIAL = 0x001B49D0     # HUDFlushTutorialText -> (flags) -> 001CE748
CHANNELS = {"tutorial": SET_TUTORIAL_TEXT, "announce": SET_ANNOUNCE_MSG,
            "clear": CLEAR_TUTORIAL, "flush": FLUSH_TUTORIAL}

# A call site that runs often, and that the freecam does not already own.
HOOK = 0x00198948
HOOK_ORIGINAL = 0x0C1209EE  # jal 0x004827B8
HOOK_TARGET = 0x004827B8


def build(target: int, arg0, arg1, arg2) -> bytes:
    asm = Asm(CAVE)
    asm.addiu(SP, SP, -0x40)
    asm.sd(RA, 0x10, SP)
    asm.sd(A0, 0x18, SP)
    asm.sd(A1, 0x20, SP)
    asm.sd(A2, 0x28, SP)
    asm.sd(A3, 0x30, SP)

    asm.li(T0, FLAG)
    asm.lw(T1, 0x00, T0)
    asm.beqz(T1, "skip")
    asm.nop()
    asm.sw(0, 0x00, T0)  # $zero: fire once
    for register, value in zip((A0, A1, A2), (arg0, arg1, arg2)):
        asm.li(register, TEXT if value == "text" else value)
    asm.jal(target)
    asm.nop()

    asm.label("skip")
    asm.ld(A3, 0x30, SP)
    asm.ld(A2, 0x28, SP)
    asm.ld(A1, 0x20, SP)
    asm.ld(A0, 0x18, SP)
    asm.ld(RA, 0x10, SP)
    asm.addiu(SP, SP, 0x40)
    asm.j(HOOK_TARGET)
    asm.nop()
    return asm.assemble()


def hook_word() -> int:
    asm = Asm(0)
    asm.jal(CAVE)
    return asm.words[0]


def install(pine: Pine, text: str, target: int, arg0, arg1, arg2) -> None:
    live = pine.read32(HOOK)
    if live not in (HOOK_ORIGINAL, hook_word()):
        raise SystemExit(f"hook site holds 0x{live:08X}; refusing to touch it")
    blob = build(target, arg0, arg1, arg2)
    pine.write_block(CAVE, blob.ljust((len(blob) + 7) & ~7, b"\0"))
    payload = text.encode("ascii", "replace")[:0x40] + b"\0"
    pine.write_block(TEXT, payload.ljust((len(payload) + 7) & ~7, b"\0"))
    pine.write32(FLAG, 0)
    pine.write32(HOOK, hook_word())
    print(f"armed at 0x{HOOK:08X}; text {text!r} -> 0x{target:08X}({arg0}, {arg1}, {arg2})")


def fire(pine: Pine) -> None:
    pine.write32(FLAG, 1)
    print("flag set; the next frame makes the call")


def uninstall(pine: Pine) -> None:
    pine.write32(HOOK, HOOK_ORIGINAL)
    print("hook site restored")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "fire", "uninstall", "dump"))
    parser.add_argument("--text", default="FREECAM TEXT TEST")
    parser.add_argument("--channel", choices=tuple(CHANNELS), default="tutorial")
    # "text" puts the string pointer in that position; anything else is a literal.
    def argument(value: str):
        return "text" if value == "text" else int(value, 0)

    parser.add_argument("--arg0", type=argument, default="text")
    parser.add_argument("--arg1", type=argument, default=0)
    parser.add_argument("--arg2", type=argument, default=0)
    args = parser.parse_args()

    if args.action == "dump":
        sys.path.insert(0, "C:/DevMST/TheWarriors/research")
        from disassemble_mips import disasm

        code = build(CHANNELS[args.channel], args.arg0, args.arg1, args.arg2)
        for index in range(0, len(code), 4):
            word = struct.unpack_from("<I", code, index)[0]
            print(f"  {CAVE + index:08X}  {word:08X}  {disasm(CAVE + index, word)}")
        return

    with Pine(timeout=8.0) as pine:
        if args.action == "install":
            install(pine, args.text, CHANNELS[args.channel], args.arg0, args.arg1, args.arg2)
        elif args.action == "fire":
            fire(pine)
        else:
            uninstall(pine)


if __name__ == "__main__":
    main()
