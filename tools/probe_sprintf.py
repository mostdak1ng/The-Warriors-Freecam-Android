#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 mostdak1ng
"""Find out whether the game's snprintf can format a float for us.

``0x00432810(dest, size, fmt, arg, ...)`` is the game's snprintf: four
different format strings in the ELF are passed to it in a2, with the
destination in a0 and the first variadic argument in a3.

Whether ``%f`` works is the open question.  The R5900 has no double precision,
so the usual C promotion of float to double cannot apply and the implementation
must do something of its own.  Rather than guess, this calls it and reads the
result back, which needs no one watching the screen.

Writing float-to-decimal by hand in MIPS is the fallback, so this is worth a
few minutes to settle.
"""

from __future__ import annotations

import argparse
import pathlib
import struct
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from mips import A0, A1, A2, A3, RA, SP, T0, T1, T2, T3, ZERO, Asm  # noqa: E402
from pine import Pine  # noqa: E402

CAVE = 0x000F2000
FLAG = 0x000F2200
ARG = 0x000F2208
FMT = 0x000F2280
BUF = 0x000F2300
BUF_SIZE = 0x60

SNPRINTF = 0x00432810

HOOK = 0x00198948
HOOK_ORIGINAL = 0x0C1209EE
HOOK_TARGET = 0x004827B8


def float_to_double(asm: Asm, src, dest) -> None:
    """Widen the float bits in ``src`` to double bits in ``dest``.

    snprintf wants a double, and the R5900 FPU cannot make one - but its integer
    registers are 64 bits wide, so the pattern is assembled by hand: rebias the
    exponent by 1023-127, shift the 23-bit mantissa up by 29, and place the sign
    at bit 63.  Zero has to be special-cased, since the exponent rebias would
    otherwise turn it into a tiny denormal.
    """
    asm.srl(T1, src, 23)
    asm.andi(T1, T1, 0xFF)
    asm.sll(T2, src, 9)
    asm.srl(T2, T2, 9)
    asm.srl(T3, src, 31)
    asm.dsll32(T3, T3, 31)
    asm.beqz(T1, "fd_zero")
    asm.nop()
    asm.addiu(T1, T1, 1023 - 127)
    asm.dsll32(T1, T1, 20)
    asm.dsll(T2, T2, 29)
    asm.or_(dest, T1, T2)
    asm.or_(dest, dest, T3)
    asm.j("fd_done")
    asm.nop()
    asm.label("fd_zero")
    asm.or_(dest, T3, ZERO)
    asm.label("fd_done")


def build(convert: bool = False) -> bytes:
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
    asm.sw(0, 0x00, T0)
    asm.li(A0, BUF)
    asm.li(A1, BUF_SIZE)
    asm.li(A2, FMT)
    asm.li(T1, ARG)
    if convert:
        asm.lw(T0, 0x00, T1)
        float_to_double(asm, T0, A3)
    else:
        asm.ld(A3, 0x00, T1)  # 64-bit slot: the vararg needs double bits
    asm.jal(SNPRINTF)
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


def install(pine: Pine, convert: bool = False) -> None:
    live = pine.read32(HOOK)
    if live not in (HOOK_ORIGINAL, hook_word()):
        raise SystemExit(f"hook site holds 0x{live:08X}; refusing to touch it")
    blob = build(convert)
    pine.write_block(CAVE, blob.ljust((len(blob) + 7) & ~7, b"\0"))
    pine.write32(FLAG, 0)
    pine.write32(HOOK, hook_word())
    print(f"armed at 0x{HOOK:08X}")


def uninstall(pine: Pine) -> None:
    pine.write32(HOOK, HOOK_ORIGINAL)
    print("hook site restored")


def run(pine: Pine, fmt: str, value: str) -> None:
    payload = fmt.encode("ascii") + b"\0"
    pine.write_block(FMT, payload.ljust((len(payload) + 7) & ~7, b"\0"))
    if value.startswith("d:"):  # IEEE-754 double bits
        bits = struct.unpack("<Q", struct.pack("<d", float(value[2:])))[0]
    elif value.startswith("f:"):
        bits = struct.unpack("<I", struct.pack("<f", float(value[2:])))[0]
    else:
        bits = int(value, 0) & 0xFFFFFFFF
    pine.write64(ARG, bits)
    pine.write_block(BUF, b"\0" * BUF_SIZE)
    pine.write32(FLAG, 1)
    import time

    for _ in range(40):
        if pine.read32(FLAG) == 0:
            break
        time.sleep(0.05)
    out = pine.read_block(BUF, BUF_SIZE).split(b"\0")[0]
    print(f"  fmt={fmt!r} arg=0x{bits:016X} -> {out.decode('ascii', 'replace')!r}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "uninstall", "run", "suite"))
    parser.add_argument("--fmt", default="%d")
    parser.add_argument("--value", default="1234")
    parser.add_argument("--convert", action="store_true",
                        help="widen a float32 in the argument slot to a double")
    args = parser.parse_args()

    with Pine(timeout=8.0) as pine:
        if args.action == "install":
            install(pine, args.convert)
        elif args.action == "uninstall":
            uninstall(pine)
        elif args.action == "run":
            run(pine, args.fmt, args.value)
        else:
            cases = (("%.2f", "d:-198.113"),) if not args.convert else (
                ("%.2f", "f:123.5"), ("%.2f", "f:-198.113"), ("%.2f", "f:0"),
                ("%.1f", "f:-0.05"), ("%.2f", "f:6.447"), ("%.2f", "f:-209.699"),
            )
            for fmt, value in cases:
                run(pine, fmt, value)


if __name__ == "__main__":
    main()
