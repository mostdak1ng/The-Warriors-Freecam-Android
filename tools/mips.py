#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 mostdak1ng
"""Tiny EE (MIPS R5900) assembler.

Only the instructions the freecam routine needs are implemented.  Branches and
jumps take label names; addresses are resolved by ``assemble``.
"""

from __future__ import annotations

import struct

ZERO, AT = 0, 1
V0, V1 = 2, 3
A0, A1, A2, A3 = 4, 5, 6, 7
T0, T1, T2, T3, T4, T5, T6, T7 = 8, 9, 10, 11, 12, 13, 14, 15
S0, S1, S2, S3, S4, S5, S6, S7 = 16, 17, 18, 19, 20, 21, 22, 23
T8, T9 = 24, 25
K0, K1, GP, SP, FP, RA = 26, 27, 28, 29, 30, 31

FMT_S, FMT_W = 16, 20


def _i(op: int, rs: int, rt: int, imm: int) -> int:
    return (op << 26) | (rs << 21) | (rt << 16) | (imm & 0xFFFF)


def _r(rs: int, rt: int, rd: int, shamt: int, funct: int) -> int:
    return (rs << 21) | (rt << 16) | (rd << 11) | (shamt << 6) | funct


def _fp(fmt: int, ft: int, fs: int, fd: int, funct: int) -> int:
    return (0x11 << 26) | (fmt << 21) | (ft << 16) | (fs << 11) | (fd << 6) | funct


class Asm:
    """Collects words, resolves labels, emits a blob for ``base``."""

    def __init__(self, base: int):
        if base % 4:
            raise ValueError("base must be word aligned")
        self.base = base
        self.words: list[int] = []
        self.labels: dict[str, int] = {}
        self.fixups: list[tuple[int, str, str]] = []

    # -- plumbing ---------------------------------------------------------
    @property
    def pc(self) -> int:
        return self.base + len(self.words) * 4

    def label(self, name: str) -> "Asm":
        if name in self.labels:
            raise ValueError(f"duplicate label {name}")
        self.labels[name] = self.pc
        return self

    def word(self, value: int) -> "Asm":
        self.words.append(value & 0xFFFFFFFF)
        return self

    def _branch(self, encoded: int, target: str) -> "Asm":
        self.fixups.append((len(self.words), target, "rel"))
        return self.word(encoded)

    def _jump(self, op: int, target) -> "Asm":
        if isinstance(target, int):
            return self.word((op << 26) | ((target >> 2) & 0x03FFFFFF))
        self.fixups.append((len(self.words), target, "abs"))
        return self.word(op << 26)

    def assemble(self) -> bytes:
        words = list(self.words)
        for index, name, kind in self.fixups:
            if name not in self.labels:
                raise ValueError(f"undefined label {name}")
            target = self.labels[name]
            if kind == "rel":
                delta = (target - (self.base + index * 4 + 4)) >> 2
                if not -0x8000 <= delta < 0x8000:
                    raise ValueError(f"branch to {name} out of range")
                words[index] = (words[index] & 0xFFFF0000) | (delta & 0xFFFF)
            else:
                words[index] |= (target >> 2) & 0x03FFFFFF
        return b"".join(struct.pack("<I", w) for w in words)

    # -- loads / stores ---------------------------------------------------
    def lb(self, rt, off, base): return self.word(_i(0x20, base, rt, off))
    def lbu(self, rt, off, base): return self.word(_i(0x24, base, rt, off))
    def lh(self, rt, off, base): return self.word(_i(0x21, base, rt, off))
    def lhu(self, rt, off, base): return self.word(_i(0x25, base, rt, off))
    def lw(self, rt, off, base): return self.word(_i(0x23, base, rt, off))
    def ld(self, rt, off, base): return self.word(_i(0x37, base, rt, off))
    def sb(self, rt, off, base): return self.word(_i(0x28, base, rt, off))
    def sh(self, rt, off, base): return self.word(_i(0x29, base, rt, off))
    def sw(self, rt, off, base): return self.word(_i(0x2B, base, rt, off))
    def sd(self, rt, off, base): return self.word(_i(0x3F, base, rt, off))
    def lq(self, rt, off, base): return self.word(_i(0x1E, base, rt, off))
    def sq(self, rt, off, base): return self.word(_i(0x1F, base, rt, off))
    def lwc1(self, ft, off, base): return self.word(_i(0x31, base, ft, off))
    def swc1(self, ft, off, base): return self.word(_i(0x39, base, ft, off))

    # -- immediates / arithmetic ------------------------------------------
    def lui(self, rt, imm): return self.word(_i(0x0F, 0, rt, imm))
    def ori(self, rt, rs, imm): return self.word(_i(0x0D, rs, rt, imm))
    def andi(self, rt, rs, imm): return self.word(_i(0x0C, rs, rt, imm))
    def xori(self, rt, rs, imm): return self.word(_i(0x0E, rs, rt, imm))
    def addiu(self, rt, rs, imm): return self.word(_i(0x09, rs, rt, imm))
    def slti(self, rt, rs, imm): return self.word(_i(0x0A, rs, rt, imm))
    def sltiu(self, rt, rs, imm): return self.word(_i(0x0B, rs, rt, imm))
    def addu(self, rd, rs, rt): return self.word(_r(rs, rt, rd, 0, 0x21))
    def daddu(self, rd, rs, rt): return self.word(_r(rs, rt, rd, 0, 0x2D))
    def subu(self, rd, rs, rt): return self.word(_r(rs, rt, rd, 0, 0x23))
    def and_(self, rd, rs, rt): return self.word(_r(rs, rt, rd, 0, 0x24))
    def or_(self, rd, rs, rt): return self.word(_r(rs, rt, rd, 0, 0x25))
    def xor_(self, rd, rs, rt): return self.word(_r(rs, rt, rd, 0, 0x26))
    def nor(self, rd, rs, rt): return self.word(_r(rs, rt, rd, 0, 0x27))
    def slt(self, rd, rs, rt): return self.word(_r(rs, rt, rd, 0, 0x2A))
    def sltu(self, rd, rs, rt): return self.word(_r(rs, rt, rd, 0, 0x2B))
    def sll(self, rd, rt, sa): return self.word(_r(0, rt, rd, sa, 0x00))
    def srl(self, rd, rt, sa): return self.word(_r(0, rt, rd, sa, 0x02))
    def sra(self, rd, rt, sa): return self.word(_r(0, rt, rd, sa, 0x03))
    # 64-bit shifts: the R5900 has no double-precision FPU, but its integer
    # registers are 64 bits, which is what lets us build double bit patterns.
    def dsll(self, rd, rt, sa): return self.word(_r(0, rt, rd, sa, 0x38))
    def dsrl(self, rd, rt, sa): return self.word(_r(0, rt, rd, sa, 0x3A))
    def dsll32(self, rd, rt, sa): return self.word(_r(0, rt, rd, sa, 0x3C))
    def dsrl32(self, rd, rt, sa): return self.word(_r(0, rt, rd, sa, 0x3E))
    def move(self, rd, rs): return self.addu(rd, rs, ZERO)

    def li(self, rt, value):
        value &= 0xFFFFFFFF
        if value < 0x10000:
            return self.ori(rt, ZERO, value)
        self.lui(rt, value >> 16)
        return self.ori(rt, rt, value & 0xFFFF) if value & 0xFFFF else self

    def nop(self):
        return self.word(0)

    # -- control flow ------------------------------------------------------
    def beq(self, rs, rt, target): return self._branch(_i(0x04, rs, rt, 0), target)
    def bne(self, rs, rt, target): return self._branch(_i(0x05, rs, rt, 0), target)
    def blez(self, rs, target): return self._branch(_i(0x06, rs, 0, 0), target)
    def bgtz(self, rs, target): return self._branch(_i(0x07, rs, 0, 0), target)
    def beqz(self, rs, target): return self.beq(rs, ZERO, target)
    def bnez(self, rs, target): return self.bne(rs, ZERO, target)
    def j(self, target): return self._jump(0x02, target)
    def jal(self, target): return self._jump(0x03, target)
    def jr(self, rs): return self.word(_r(rs, 0, 0, 0, 0x08))
    def jalr(self, rd, rs): return self.word(_r(rs, 0, rd, 0, 0x09))

    # -- COP1 (single precision) -------------------------------------------
    def mtc1(self, rt, fs): return self.word(_fp(4, rt, fs, 0, 0))
    def mfc1(self, rt, fs): return self.word(_fp(0, rt, fs, 0, 0))
    def add_s(self, fd, fs, ft): return self.word(_fp(FMT_S, ft, fs, fd, 0x00))
    def sub_s(self, fd, fs, ft): return self.word(_fp(FMT_S, ft, fs, fd, 0x01))
    def mul_s(self, fd, fs, ft): return self.word(_fp(FMT_S, ft, fs, fd, 0x02))
    def div_s(self, fd, fs, ft): return self.word(_fp(FMT_S, ft, fs, fd, 0x03))
    def sqrt_s(self, fd, ft): return self.word(_fp(FMT_S, ft, 0, fd, 0x04))
    def rsqrt_s(self, fd, fs, ft): return self.word(_fp(FMT_S, ft, fs, fd, 0x16))
    def abs_s(self, fd, fs): return self.word(_fp(FMT_S, 0, fs, fd, 0x05))
    def mov_s(self, fd, fs): return self.word(_fp(FMT_S, 0, fs, fd, 0x06))
    def neg_s(self, fd, fs): return self.word(_fp(FMT_S, 0, fs, fd, 0x07))
    def cvt_s_w(self, fd, fs): return self.word(_fp(FMT_W, 0, fs, fd, 0x20))
    def cvt_w_s(self, fd, fs): return self.word(_fp(FMT_S, 0, fs, fd, 0x24))
    def c_lt_s(self, fs, ft): return self.word(_fp(FMT_S, ft, fs, 0, 0x34))
    def c_le_s(self, fs, ft): return self.word(_fp(FMT_S, ft, fs, 0, 0x36))
    def c_eq_s(self, fs, ft): return self.word(_fp(FMT_S, ft, fs, 0, 0x32))
    def bc1t(self, target): return self._branch(_i(0x11, 8, 1, 0), target)
    def bc1f(self, target): return self._branch(_i(0x11, 8, 0, 0), target)


def self_test() -> None:
    """Verify encodings against instructions known to exist in the game."""
    for target, expected in (
        (0x00149770, 0x0C0525DC),
        (0x00471D50, 0x0C11C754),
        (0x004827B8, 0x0C1209EE),
        (0x0017C308, 0x0C05F0C2),
    ):
        asm = Asm(0)
        asm.jal(target)
        assert asm.words[0] == expected, f"jal {target:08X} -> {asm.words[0]:08X}"

    asm = Asm(0)
    asm.addiu(SP, SP, -0x20).sd(RA, 0x10, SP).jr(RA).nop()
    assert asm.words[0] == 0x27BDFFE0, f"{asm.words[0]:08X}"
    assert asm.words[1] == 0xFFBF0010, f"{asm.words[1]:08X}"
    assert asm.words[2] == 0x03E00008, f"{asm.words[2]:08X}"

    asm = Asm(0x00100000)
    asm.beq(T0, ZERO, "end").nop().label("end").jr(RA)
    words = struct.unpack("<3I", asm.assemble())
    assert words[0] == 0x11000001, f"{words[0]:08X}"

    # Checked against the game's own camera transform getter at 0x004DB3D0.
    asm = Asm(0)
    asm.lq(A2, 0x10, A1).daddu(V0, A0, ZERO).sq(A2, 0x00, A0)
    assert asm.words[0] == 0x78A60010, f"{asm.words[0]:08X}"
    assert asm.words[1] == 0x0080102D, f"{asm.words[1]:08X}"
    assert asm.words[2] == 0x7C860000, f"{asm.words[2]:08X}"

    # Quadword forms, checked against the game's own camera transform setter.
    asm = Asm(0)
    asm.lq(V0, 0x00, A1).sq(V0, 0x10, A0).lq(V1, 0x10, A1).sq(V1, 0x20, A0)
    assert asm.words[0] == 0x78A20000, f"{asm.words[0]:08X}"
    assert asm.words[1] == 0x7C820010, f"{asm.words[1]:08X}"
    assert asm.words[2] == 0x78A30010, f"{asm.words[2]:08X}"
    assert asm.words[3] == 0x7C830020, f"{asm.words[3]:08X}"

    asm = Asm(0)
    asm.lui(T0, 0x005E).lw(T2, -0x5788, T1).swc1(0, 0x10, T3)
    assert asm.words[0] == 0x3C08005E, f"{asm.words[0]:08X}"
    assert asm.words[1] == 0x8D2AA878, f"{asm.words[1]:08X}"

    print("mips.py self-test OK")


if __name__ == "__main__":
    self_test()
