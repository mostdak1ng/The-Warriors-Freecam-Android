#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 mostdak1ng
"""Record the arguments the game passes to its HUD draw-request queue.

``0x0018AEC0`` takes a context plus seven integer arguments, a float in f12 and
one on the stack, and hands them to the routine that fills a 0x78-byte entry in
a 255-slot ring.  Which argument is the string, and which are position, colour
and font, is far quicker to learn by watching real calls than by reading more
disassembly - so this logs them and dumps anything that points at ASCII.

Lives at 0x000F1000, clear of the addresses the .pnach owns, so the freecam can
stay installed while this runs.
"""

from __future__ import annotations

import argparse
import pathlib
import struct
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from mips import SP, T3, T9, Asm  # noqa: E402
from pine import Pine  # noqa: E402

CAVE = 0x000F2000
LOG = 0x000F2100
ENTRY_SIZE = 0x20
MAX_ENTRIES = 24

QUEUE_FUNCTION = 0x0018AEC0
# Hooking the entry catches all forty call sites; individual ones are
# conditional and may never run in a given scene.
CALL_SITE = QUEUE_FUNCTION
CALL_ORIGINAL = 0x27BDFFC0  # addiu $sp, $sp, -0x40
RESUME = QUEUE_FUNCTION + 8  # the displaced instruction and its delay slot

ARG_REGISTERS = (4, 5, 6, 7, 8, 9, 10)  # a0-a3, t0, t1, t2
ARG_NAMES = ("a0", "a1", "a2", "a3", "t0", "t1", "t2")


def build() -> bytes:
    """Log the arguments, then fall through to the real queue function.

    Only t3 and t9 are touched; every register the callee reads is left alone.
    """
    asm = Asm(CAVE)
    asm.li(T9, LOG)
    asm.lw(T3, 0x00, T9)
    asm.sltiu(T3, T3, MAX_ENTRIES)
    asm.beqz(T3, "full")
    asm.nop()
    asm.lw(T3, 0x00, T9)
    asm.addiu(T3, T3, 1)
    asm.sw(T3, 0x00, T9)
    asm.addiu(T3, T3, -1)
    # entry = LOG + 0x20 + index * 0x20
    asm.sll(T3, T3, 5)
    asm.addu(T9, T9, T3)
    for index, register in enumerate(ARG_REGISTERS):
        asm.sw(register, 0x20 + index * 4, T9)
    asm.swc1(12, 0x20 + len(ARG_REGISTERS) * 4, T9)
    asm.label("full")
    # Replay the prologue we displaced, then rejoin past the delay slot the
    # jump into here already executed.
    asm.addiu(SP, SP, -0x40)
    asm.j(RESUME)
    asm.nop()
    return asm.assemble()


def hook_word() -> int:
    # A plain jump: this replaces an ordinary instruction, not a call, so the
    # caller's return address must survive untouched.
    asm = Asm(0)
    asm.j(CAVE)
    return asm.words[0]


def install(pine: Pine) -> None:
    live = pine.read32(CALL_SITE)
    if live not in (CALL_ORIGINAL, hook_word()):
        raise SystemExit(f"call site holds 0x{live:08X}; refusing to touch it")
    blob = build()
    pine.write_block(CAVE, blob.ljust((len(blob) + 7) & ~7, b"\0"))
    pine.write_block(LOG, b"\0" * (0x20 + MAX_ENTRIES * ENTRY_SIZE))
    pine.write32(CALL_SITE, hook_word())
    print(f"logging calls at 0x{CALL_SITE:08X} -> 0x{QUEUE_FUNCTION:08X}")


def uninstall(pine: Pine) -> None:
    pine.write32(CALL_SITE, CALL_ORIGINAL)
    print("call site restored")


def looks_like_text(data: bytes) -> str | None:
    text = bytearray()
    for byte in data:
        if byte == 0:
            break
        if not (0x20 <= byte < 0x7F):
            return None
        text.append(byte)
    return text.decode("ascii") if len(text) >= 2 else None


def report(pine: Pine) -> None:
    count = pine.read32(LOG)
    print(f"{count} calls captured")
    for index in range(min(count, MAX_ENTRIES)):
        raw = pine.read_block(LOG + 0x20 + index * ENTRY_SIZE, ENTRY_SIZE)
        values = struct.unpack_from("<7I", raw, 0)
        f12 = struct.unpack_from("<f", raw, 28)[0]
        print(f"  [{index:2d}] " + "  ".join(
            f"{name}={value:08X}" for name, value in zip(ARG_NAMES, values)))
        print(f"       f12={f12:g}")
        for name, value in zip(ARG_NAMES, values):
            if 0x00100000 <= value < 0x02000000:
                try:
                    text = looks_like_text(pine.read_block(value & ~7, 48))
                except Exception:
                    text = None
                if text:
                    print(f"       {name} -> \"{text}\"")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "uninstall", "report", "dump"))
    args = parser.parse_args()

    if args.action == "dump":
        sys.path.insert(0, "C:/DevMST/TheWarriors/research")
        from disassemble_mips import disasm

        code = build()
        for index in range(0, len(code), 4):
            word = struct.unpack_from("<I", code, index)[0]
            print(f"  {CAVE + index:08X}  {word:08X}  {disasm(CAVE + index, word)}")
        return

    with Pine(timeout=8.0) as pine:
        {"install": install, "uninstall": uninstall, "report": report}[args.action](pine)


if __name__ == "__main__":
    main()
