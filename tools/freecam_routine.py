#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 mostdak1ng
"""The guest-side freecam routines.

Two pieces of EE code:

``tick``    runs once per frame from a hooked call site.  Reads the pad, turns
            the camera with the right stick, moves it along its own axes with
            the left stick, and keeps the result in our own state block.

``getter``  replaces the camera class's transform getter so every consumer -
            renderer included - reads our pose instead of the game's.

Conventions follow the desktop mod exactly: yaw about world Z pre-multiplied,
pitch about local X post-multiplied, right = q*(1,0,0), forward = q*(0,1,0),
movement = right*x + forward*y + worldUp*z.

The delta quaternions are (0,0,t,1) and (t,0,0,1) left unnormalised, so each
turn costs four multiplies and the pair is normalised once at the end.  A
rotation of ``2*atan(t)`` comes out, which is linear in the stick for the small
per-frame angles involved, so no sine or cosine is needed anywhere.
"""

from __future__ import annotations

import struct

from mips import (
    A0, A1, A2, A3, RA, SP, T0, T1, T2, T3, T4, T5, T6, T7, V0, V1, ZERO, Asm,
)

CAVE_TICK = 0x000F0000
CAVE_GETTER = 0x000F1000
STATE = 0x000F1200
CAVE_WINDOW = (0x000F0000, 0x000F1400)  # code and state; cleared as one block

OFF_COUNTER = 0x00
OFF_MAGIC = 0x04
OFF_ACTIVE = 0x08
OFF_GETTER_HITS = 0x0C
OFF_POS = 0x10
OFF_QUAT = 0x20
OFF_MOVE_SCALE = 0x30
OFF_VSTEP = 0x34
OFF_LOOK_SCALE = 0x38
OFF_ONE = 0x3C
OFF_RIGHT = 0x40
OFF_FORWARD = 0x50
OFF_PITCH_LIMIT = 0x60
OFF_NEUTRALIZE = 0x64
OFF_FAST = 0x68
OFF_SLOW = 0x6C
OFF_PREV_BUTTONS = 0x70
OFF_SCRATCH = 0x74
OFF_PAD_CAPTURE = 0x78  # eight bytes, doubleword aligned for the pad hook
OFF_PAUSE_ACTIVE = 0x84
OFF_PAUSE_SAVED = 0x88  # the exact original timestep bits
OFF_PAUSE_TARGET = 0x8C
OFF_PAUSE_SCALE = 0x90
OFF_GOD_ENABLED = 0x94
OFF_GOD_SAVED = 0x98  # the God bit as the player had it
OFF_GOD_HUMAN = 0x9C
OFF_CARRY_ENABLED = 0xA0
OFF_CARRY_FWD = 0xA4
OFF_CARRY_UP = 0xA8
OFF_NOTARGET_SAVED = 0xAC  # the no-target bit as the player had it
OFF_CARRY_TARGET = 0xB0  # quadword aligned, written straight into the player
OFF_PLAYER_POS = 0xC0  # quadword aligned copy of the player's position
OFF_HUD_HIDDEN = 0xD0  # 0 = shown; mutable, so never patched
OFF_HUD_CURSOR = 0xD4
OFF_SAVED_RA = 0xD8  # the tick has no stack frame, so ra parks here
OFF_SIGNATURE = 0xE0
OFF_HUD_TEXT = 0x120
OFF_CAM_SHOWN = 0x170  # what the readout prints: ours, or the game's
OFF_FMT_CAM = 0x180
OFF_FMT_NUM = 0x190
OFF_FMT_PLR = 0x1A0
STATE_SIZE = 0x200
HUD_TEXT_SIZE = 0x50

FMT_CAM = b"CAM %.1f "
FMT_NUM = b"%.1f "
FMT_PLR = b"PLR %.1f "

# Found through the scripting table: HUDSetTutorialText's implementation, and
# the game's own snprintf.  Passing a null text clears the slot again.
SET_TUTORIAL_TEXT = 0x001B4980
# Passing a null text does not clear: that path is gated behind a check our
# endless text fails.  HUDFlushTutorialText's implementation does clear it.
FLUSH_TUTORIAL_TEXT = 0x001B49D0
SNPRINTF = 0x00432810
HUD_REFRESH_MASK = 0x0F  # rebuild roughly twice a second

MAGIC_VALUE = 0x43465754  # "TWFC"
SIGNATURE = b"The Warriors Freecam v0.2.0 - mostdak1ng - GPL-3.0-only\0"

TICK_HOOK = 0x00185BFC
TICK_HOOK_ORIGINAL = 0x0C11C754
TICK_ORIGINAL_TARGET = 0x00471D50

VTABLE = 0x00535D50

# Fifteen camera subclasses share one inherited vtable tail, evenly spaced, so
# the transform getter sits at the same relative offset in every one of them.
# Patching all of them is what lets the freecam survive a cutscene, where the
# active camera is a different class.  Which camera actually gets substituted
# is decided at run time by comparing against the active-camera global, so the
# other fourteen behave exactly as before.
CAMERA_VTABLE_COUNT = 15
CAMERA_VTABLE_STRIDE = 0x2C0
GETTER_A_FIRST, GETTER_A_ORIGINAL = 0x005352F4, 0x004DB3D0
GETTER_B_FIRST, GETTER_B_ORIGINAL = 0x005352FC, 0x004DB3E8

GETTER_SLOTS = {
    first + index * CAMERA_VTABLE_STRIDE: original
    for first, original in (
        (GETTER_A_FIRST, GETTER_A_ORIGINAL),
        (GETTER_B_FIRST, GETTER_B_ORIGINAL),
    )
    for index in range(CAMERA_VTABLE_COUNT)
}

PAD = 0x005DE4D0
CAMERA_POINTER = 0x005D9148

DEADZONE = 12
MOVE_PER_FRAME = 0.25
VERTICAL_PER_FRAME = 0.20
LOOK_PER_FRAME = 0.02
PITCH_LIMIT = 0.995
FAST_MULTIPLIER = 4.0
SLOW_MULTIPLIER = 0.25

BTN_SELECT = 0x0100
BTN_L2 = 0x0001
BTN_R2 = 0x0002
BTN_L1 = 0x0004
BTN_R1 = 0x0008
BTN_SQUARE = 0x0080
BTN_CIRCLE = 0x0020
BTN_L3 = 0x0200
BTN_R3 = 0x0400
BTN_CROSS = 0x0040
BTN_TRIANGLE = 0x0010

# World pause: the game's own timestep, scaled down rather than zeroed, which
# is what the desktop mod settled on.  Actors and timers stop for practical
# purposes while the freecam keeps updating.
WORLD_TIMESTEP = 0x005102CC
PAUSE_SCALE = 0.0001

# God mode reaches the player through the entity registry, the same chain the
# desktop mod uses.  Only the God bit is touched; the no-target bit belongs to
# player carry, which v0.1.0 does not have.
PLAYER_MANAGER_POINTER = 0x0051489C
PLAYER_HANDLE_OFFSET = 0x228
ENTITY_REGISTRY = 0x006EBD38
HUMAN_VTABLE = 0x0053F088
HUMAN_SAFETY_OFFSET = 0xE0
GOD_BIT = 0x10
# The no-target bit is bit 44, so it lives in the high word of the safety pair.
NOTARGET_BIT_HIGH = 0x00001000

# Player carry writes the position into all three places the game keeps it,
# then zeroes the vertical speeds so gravity does not fight the camera.
MOVE_TRANSFORM_BASE = 0x00714B00
MOVE_TRANSFORM_STRIDE_SHIFT = 5  # 0x20 bytes per entry
MOVE_INDEX_LIMIT = 0x4000
HUMAN_MOVE_INDEX_OFFSET = 0x92
HUMAN_OBJECT_TRANSFORM_OFFSET = 0x10
HUMAN_LOCAL_POSITION_OFFSET = 0x2C0
HUMAN_VERTICAL_SPEED_OFFSETS = (0x38, 0x3A0)
CARRY_FORWARD = 3.0
CARRY_UP = -1.5

CAVE_PAD = 0x000F1100
PAD_REFRESH_CALL = 0x001498E0
PAD_REFRESH_ORIGINAL = 0x0C0525DC  # jal 0x00149770
PAD_REFRESH_FUNCTION = 0x00149770
NEUTRAL_AXES = 0x7F7F7F7F

# f12 upwards can hold arguments for the function we tail-jump into, so the
# routine stays inside f0-f11.
QX, QY, QZ, QW = 4, 5, 6, 7
FA, FB, FC, FD = 0, 1, 2, 3
FE, FF, FG, FH = 8, 9, 10, 11


def _axis(asm: Asm, byte_offset: int, tag: str, into: int, negate: bool) -> None:
    """Stick byte -> float in ``into``, deadzoned, still in raw -128..127 units."""
    asm.lbu(T2, byte_offset, T0)
    asm.addiu(T2, T2, -128)
    asm.slti(T3, T2, DEADZONE)
    asm.beqz(T3, f"{tag}_live")
    asm.nop()
    asm.slti(T3, T2, -DEADZONE)
    asm.bnez(T3, f"{tag}_live")
    asm.nop()
    asm.move(T2, ZERO)
    asm.label(f"{tag}_live")
    asm.mtc1(T2, into)
    asm.cvt_s_w(into, into)
    if negate:
        asm.neg_s(into, into)


def _resolve_human(asm: Asm, fail: str) -> None:
    """Player entity into T5, or branch to ``fail``.

    Manager, live handle, entity registry, generation check, Human vtable -
    the same chain and the same guards the desktop mod applies.
    """
    asm.li(T5, PLAYER_MANAGER_POINTER)
    asm.lw(T5, 0x00, T5)
    asm.li(T3, 0x00600000)
    asm.sltu(T6, T5, T3)
    asm.bnez(T6, fail)
    asm.nop()
    asm.li(T3, 0x02000000)
    asm.sltu(T6, T5, T3)
    asm.beqz(T6, fail)
    asm.nop()
    asm.lw(T2, PLAYER_HANDLE_OFFSET, T5)
    asm.addiu(T3, ZERO, -1)
    asm.beq(T2, T3, fail)
    asm.nop()
    asm.srl(T5, T2, 16)
    asm.andi(T2, T2, 0xFFFF)
    asm.sll(T5, T5, 3)
    asm.li(T3, ENTITY_REGISTRY)
    asm.addu(T5, T5, T3)
    asm.lhu(T3, 0x04, T5)
    asm.bne(T3, T2, fail)
    asm.nop()
    asm.lw(T5, 0x00, T5)
    asm.li(T3, 0x00600000)
    asm.sltu(T6, T5, T3)
    asm.bnez(T6, fail)
    asm.nop()
    asm.li(T3, 0x02000000)
    asm.sltu(T6, T5, T3)
    asm.beqz(T6, fail)
    asm.nop()
    asm.lw(T3, 0x00, T5)
    asm.li(T6, HUMAN_VTABLE)
    asm.bne(T3, T6, fail)
    asm.nop()


def _carry_apply(asm: Asm) -> None:
    """Hold the player in front of and below the camera.  T5 is the player.

    The position goes into all three places the game keeps it, the vertical
    speeds are zeroed so gravity does not drag the body down, and the no-target
    bit keeps enemies from swinging at a body that is riding the camera.
    """
    for index, offset in enumerate((0x00, 0x04, 0x08)):
        asm.lwc1(FA, OFF_FORWARD + offset, T4)
        asm.lwc1(FB, OFF_CARRY_FWD, T4)
        asm.mul_s(FA, FA, FB)
        asm.lwc1(FC, OFF_POS + offset, T4)
        asm.add_s(FA, FA, FC)
        if offset == 0x08:
            asm.lwc1(FB, OFF_CARRY_UP, T4)
            asm.add_s(FA, FA, FB)
        asm.swc1(FA, OFF_CARRY_TARGET + offset, T4)
    asm.lwc1(FA, OFF_ONE, T4)
    asm.swc1(FA, OFF_CARRY_TARGET + 0x0C, T4)

    asm.lq(T2, OFF_CARRY_TARGET, T4)
    # The movement table entry only exists while the index is in range.
    asm.lhu(T3, HUMAN_MOVE_INDEX_OFFSET, T5)
    asm.sltiu(T6, T3, MOVE_INDEX_LIMIT)
    asm.beqz(T6, "carry_no_movement")
    asm.nop()
    asm.sll(T3, T3, MOVE_TRANSFORM_STRIDE_SHIFT)
    asm.li(T6, MOVE_TRANSFORM_BASE)
    asm.addu(T3, T3, T6)
    asm.sq(T2, 0x00, T3)
    asm.label("carry_no_movement")

    asm.sq(T2, HUMAN_OBJECT_TRANSFORM_OFFSET, T5)
    asm.sq(T2, HUMAN_LOCAL_POSITION_OFFSET, T5)
    for offset in HUMAN_VERTICAL_SPEED_OFFSETS:
        asm.sw(ZERO, offset, T5)
    asm.lw(T2, HUMAN_SAFETY_OFFSET + 4, T5)
    asm.li(T3, NOTARGET_BIT_HIGH)
    asm.or_(T2, T2, T3)
    asm.sw(T2, HUMAN_SAFETY_OFFSET + 4, T5)


def _float_to_double(asm: Asm, src, dest, tag: str) -> None:
    """Widen float bits in ``src`` to double bits in ``dest``.

    The game's snprintf wants a double and the R5900 FPU cannot make one, but
    its integer registers are 64 bits: rebias the exponent by 1023-127, shift
    the 23-bit mantissa up by 29, and put the sign at bit 63.  Zero needs its
    own case, or the rebias would turn it into a tiny denormal.
    """
    asm.srl(T1, src, 23)
    asm.andi(T1, T1, 0xFF)
    asm.sll(T2, src, 9)
    asm.srl(T2, T2, 9)
    asm.srl(T3, src, 31)
    asm.dsll32(T3, T3, 31)
    asm.beqz(T1, f"{tag}_zero")
    asm.nop()
    asm.addiu(T1, T1, 1023 - 127)
    asm.dsll32(T1, T1, 20)
    asm.dsll(T2, T2, 29)
    asm.or_(dest, T1, T2)
    asm.or_(dest, dest, T3)
    asm.j(f"{tag}_done")
    asm.nop()
    asm.label(f"{tag}_zero")
    asm.or_(dest, T3, ZERO)
    asm.label(f"{tag}_done")


def _hud_number(asm: Asm, fmt_offset: int, value_offset: int, tag: str) -> None:
    """Append one formatted float to the HUD line.

    snprintf returns the length written, so the cursor walks along the buffer
    and each number needs only the single variadic argument that fits in a3.
    """
    asm.li(T4, STATE)
    asm.lw(T0, OFF_HUD_CURSOR, T4)
    asm.li(T5, STATE + OFF_HUD_TEXT)
    asm.addu(A0, T5, T0)
    asm.li(T1, HUD_TEXT_SIZE)
    asm.subu(A1, T1, T0)
    asm.li(A2, STATE + fmt_offset)
    asm.lw(T0, value_offset, T4)
    _float_to_double(asm, T0, A3, tag)
    asm.jal(SNPRINTF)
    asm.nop()
    asm.li(T4, STATE)
    asm.lw(T0, OFF_HUD_CURSOR, T4)
    asm.addu(T0, T0, V0)
    asm.sw(T0, OFF_HUD_CURSOR, T4)


def _hud_update(asm: Asm) -> None:
    asm.li(T4, STATE)
    asm.sw(ZERO, OFF_HUD_CURSOR, T4)
    for fmt, value, tag in (
        (OFF_FMT_CAM, OFF_CAM_SHOWN + 0x00, "hcx"),
        (OFF_FMT_NUM, OFF_CAM_SHOWN + 0x04, "hcy"),
        (OFF_FMT_NUM, OFF_CAM_SHOWN + 0x08, "hcz"),
        (OFF_FMT_PLR, OFF_PLAYER_POS + 0x00, "hpx"),
        (OFF_FMT_NUM, OFF_PLAYER_POS + 0x04, "hpy"),
        (OFF_FMT_NUM, OFF_PLAYER_POS + 0x08, "hpz"),
    ):
        _hud_number(asm, fmt, value, tag)
    asm.li(A0, STATE + OFF_HUD_TEXT)
    asm.li(A1, 0)
    asm.jal(SET_TUTORIAL_TEXT)
    asm.nop()


def _hud_clear(asm: Asm) -> None:
    """Free the slot so the game's own tutorials can appear again."""
    asm.li(A0, 0)
    asm.jal(FLUSH_TUTORIAL_TEXT)
    asm.nop()


def build_tick() -> bytes:
    asm = Asm(CAVE_TICK)

    asm.li(T4, STATE)
    asm.lw(T3, OFF_COUNTER, T4)
    asm.addiu(T3, T3, 1)
    asm.sw(T3, OFF_COUNTER, T4)

    # The pad hook captures the physical sample before the game is allowed to
    # see a neutral one, so read the capture rather than the live buffer.
    asm.li(T0, STATE + OFF_PAD_CAPTURE)
    asm.lhu(T1, 0x04, T0)
    # Last frame's buttons, kept for edge detection, refreshed on every path.
    asm.lw(T7, OFF_PREV_BUTTONS, T4)
    asm.sw(T1, OFF_PREV_BUTTONS, T4)

    # ---- Select+Circle toggles God mode, in or out of the freecam ----
    # It deliberately survives leaving the freecam: this is the only way to
    # switch it, so dropping it on every exit would make it useless.
    _resolve_human(asm, "no_god")
    asm.lq(T2, HUMAN_OBJECT_TRANSFORM_OFFSET, T5)
    asm.sq(T2, OFF_PLAYER_POS, T4)
    asm.andi(T3, T1, BTN_SELECT)
    asm.beqz(T3, "god_hold")
    asm.nop()
    asm.andi(T3, T1, BTN_CIRCLE)
    asm.beqz(T3, "god_hold")
    asm.nop()
    asm.andi(T3, T7, BTN_CIRCLE)
    asm.bnez(T3, "god_hold")
    asm.nop()
    asm.lw(T3, OFF_GOD_ENABLED, T4)
    asm.bnez(T3, "god_disable")
    asm.nop()
    asm.lw(T2, HUMAN_SAFETY_OFFSET, T5)
    asm.andi(T2, T2, GOD_BIT)
    asm.sw(T2, OFF_GOD_SAVED, T4)
    asm.li(T3, 1)
    asm.sw(T3, OFF_GOD_ENABLED, T4)
    asm.j("god_hold")
    asm.nop()
    asm.label("god_disable")
    asm.lw(T2, HUMAN_SAFETY_OFFSET, T5)
    asm.li(T3, GOD_BIT)
    asm.nor(T3, T3, ZERO)
    asm.and_(T2, T2, T3)
    asm.lw(T3, OFF_GOD_SAVED, T4)
    asm.or_(T2, T2, T3)
    asm.sw(T2, HUMAN_SAFETY_OFFSET, T5)
    asm.sw(ZERO, OFF_GOD_ENABLED, T4)

    # Re-applied every frame, so it survives respawns and level changes.
    asm.label("god_hold")
    asm.lw(T3, OFF_GOD_ENABLED, T4)
    asm.beqz(T3, "no_god")
    asm.nop()
    asm.lw(T2, HUMAN_SAFETY_OFFSET, T5)
    asm.ori(T2, T2, GOD_BIT)
    asm.sw(T2, HUMAN_SAFETY_OFFSET, T5)
    asm.label("no_god")

    # The readout runs whether or not the freecam is on: it shows our pose when
    # we own the camera, and the game's own camera when we do not.
    asm.li(T4, STATE)
    asm.lw(T3, OFF_ACTIVE, T4)
    asm.bnez(T3, "hud_src_ours")
    asm.nop()
    asm.li(T5, CAMERA_POINTER)
    asm.lw(T5, 0x00, T5)
    asm.li(T3, 0x00600000)
    asm.sltu(T6, T5, T3)
    asm.bnez(T6, "hud_src_done")
    asm.nop()
    asm.li(T3, 0x02000000)
    asm.sltu(T6, T5, T3)
    asm.beqz(T6, "hud_src_done")
    asm.nop()
    asm.lq(T2, 0x10, T5)
    asm.sq(T2, OFF_CAM_SHOWN, T4)
    asm.j("hud_src_done")
    asm.nop()
    asm.label("hud_src_ours")
    asm.lq(T2, OFF_POS, T4)
    asm.sq(T2, OFF_CAM_SHOWN, T4)
    asm.label("hud_src_done")

    # ---- world pause, in or out of the freecam ----
    # Guard first: the pause is held every frame, so if the world disappears
    # while it is on - a level change, a loaded state - the timestep has to go
    # back, or the next level would run at 0.01% speed with no way to undo it.
    asm.li(T5, CAMERA_POINTER)
    asm.lw(T5, 0x00, T5)
    asm.li(T3, 0x00600000)
    asm.sltu(T6, T5, T3)
    asm.bnez(T6, "pause_world_lost")
    asm.nop()
    asm.li(T3, 0x02000000)
    asm.sltu(T6, T5, T3)
    asm.bnez(T6, "pause_world_ok")
    asm.nop()
    asm.label("pause_world_lost")
    asm.lw(T3, OFF_PAUSE_ACTIVE, T4)
    asm.beqz(T3, "no_pause_hold")
    asm.nop()
    asm.li(T5, WORLD_TIMESTEP)
    asm.lw(T2, OFF_PAUSE_SAVED, T4)
    asm.sw(T2, 0x00, T5)
    asm.sw(ZERO, OFF_PAUSE_ACTIVE, T4)
    asm.j("no_pause_hold")
    asm.nop()
    asm.label("pause_world_ok")

    # ---- Select+Square toggles world pause ----
    asm.andi(T3, T1, BTN_SELECT)
    asm.beqz(T3, "no_pause_toggle")
    asm.nop()
    asm.andi(T3, T1, BTN_SQUARE)
    asm.beqz(T3, "no_pause_toggle")
    asm.nop()
    asm.andi(T3, T7, BTN_SQUARE)
    asm.bnez(T3, "no_pause_toggle")
    asm.nop()
    asm.lw(T3, OFF_PAUSE_ACTIVE, T4)
    asm.bnez(T3, "unpause")
    asm.nop()
    # Keep the exact original bits so unpausing restores them untouched.
    asm.li(T5, WORLD_TIMESTEP)
    asm.lw(T2, 0x00, T5)
    asm.sw(T2, OFF_PAUSE_SAVED, T4)
    asm.mtc1(T2, FA)
    asm.lwc1(FB, OFF_PAUSE_SCALE, T4)
    asm.mul_s(FA, FA, FB)
    asm.swc1(FA, OFF_PAUSE_TARGET, T4)
    asm.lw(T2, OFF_PAUSE_TARGET, T4)
    asm.sw(T2, 0x00, T5)
    asm.li(T3, 1)
    asm.sw(T3, OFF_PAUSE_ACTIVE, T4)
    asm.j("no_pause_toggle")
    asm.nop()
    asm.label("unpause")
    asm.li(T5, WORLD_TIMESTEP)
    asm.lw(T2, OFF_PAUSE_SAVED, T4)
    asm.sw(T2, 0x00, T5)
    asm.sw(ZERO, OFF_PAUSE_ACTIVE, T4)
    asm.label("no_pause_toggle")

    # The game rewrites the timestep, so hold it down every frame.
    asm.lw(T3, OFF_PAUSE_ACTIVE, T4)
    asm.beqz(T3, "no_pause_hold")
    asm.nop()
    asm.li(T5, WORLD_TIMESTEP)
    asm.lw(T2, OFF_PAUSE_TARGET, T4)
    asm.sw(T2, 0x00, T5)
    asm.label("no_pause_hold")

    # snprintf is a normal call and clobbers the temporaries, so the pad
    # pointer and both button words are parked before it and restored after.
    # Without this the stick reads garbage on every refresh frame and the
    # camera jumps once a second.
    asm.sw(T7, OFF_SCRATCH, T4)

    # ---- Select+Triangle toggles the coordinate readout ----
    asm.andi(T3, T1, BTN_SELECT)
    asm.beqz(T3, "no_hud_toggle")
    asm.nop()
    asm.andi(T3, T1, BTN_TRIANGLE)
    asm.beqz(T3, "no_hud_toggle")
    asm.nop()
    asm.andi(T3, T7, BTN_TRIANGLE)
    asm.bnez(T3, "no_hud_toggle")
    asm.nop()
    asm.lw(T3, OFF_HUD_HIDDEN, T4)
    asm.xori(T3, T3, 1)
    asm.sw(T3, OFF_HUD_HIDDEN, T4)
    asm.beqz(T3, "no_hud_toggle")
    asm.nop()
    asm.sd(RA, OFF_SAVED_RA, T4)
    _hud_clear(asm)
    asm.li(T4, STATE)
    asm.ld(RA, OFF_SAVED_RA, T4)
    asm.label("no_hud_toggle")

    # Rebuilding twice a second is plenty and keeps the cost off most frames.
    asm.li(T4, STATE)
    asm.lw(T3, OFF_HUD_HIDDEN, T4)
    asm.bnez(T3, "no_hud")
    asm.nop()
    asm.lw(T3, OFF_COUNTER, T4)
    asm.andi(T3, T3, HUD_REFRESH_MASK)
    asm.bnez(T3, "no_hud")
    asm.nop()
    asm.sd(RA, OFF_SAVED_RA, T4)
    _hud_update(asm)
    asm.li(T4, STATE)
    asm.ld(RA, OFF_SAVED_RA, T4)
    asm.label("no_hud")

    asm.li(T4, STATE)
    asm.li(T0, STATE + OFF_PAD_CAPTURE)
    asm.lhu(T1, 0x04, T0)
    asm.lw(T7, OFF_SCRATCH, T4)

    # ---- Select+L3 toggles the freecam ----
    asm.andi(T3, T1, BTN_SELECT)
    asm.beqz(T3, "no_mode_toggle")
    asm.nop()
    asm.andi(T3, T1, BTN_L3)
    asm.beqz(T3, "no_mode_toggle")
    asm.nop()
    asm.andi(T3, T7, BTN_L3)
    asm.bnez(T3, "no_mode_toggle")
    asm.nop()
    asm.lw(T3, OFF_ACTIVE, T4)
    asm.beqz(T3, "activate")
    asm.nop()
    asm.sw(ZERO, OFF_ACTIVE, T4)
    asm.j("shutdown")
    asm.nop()

    asm.label("activate")
    asm.li(T5, CAMERA_POINTER)
    asm.lw(T5, 0x00, T5)
    asm.li(T3, 0x00600000)
    asm.sltu(T6, T5, T3)
    asm.bnez(T6, "done")
    asm.nop()
    asm.li(T3, 0x02000000)
    asm.sltu(T6, T5, T3)
    asm.beqz(T6, "done")
    asm.nop()
    asm.lq(T2, 0x10, T5)
    asm.sq(T2, OFF_POS, T4)
    asm.lq(T2, 0x20, T5)
    asm.sq(T2, OFF_QUAT, T4)
    asm.li(T3, 1)
    asm.sw(T3, OFF_ACTIVE, T4)
    asm.label("no_mode_toggle")

    asm.lw(T3, OFF_ACTIVE, T4)
    asm.beqz(T3, "done")
    asm.nop()

    # Drop out if the world went away under us, the way a level transition or
    # a loaded save state does.  A stale pose would otherwise hold the camera.
    asm.li(T5, CAMERA_POINTER)
    asm.lw(T5, 0x00, T5)
    asm.li(T3, 0x00600000)
    asm.sltu(T6, T5, T3)
    asm.bnez(T6, "world_lost")
    asm.nop()
    asm.li(T3, 0x02000000)
    asm.sltu(T6, T5, T3)
    asm.bnez(T6, "world_ok")
    asm.nop()
    asm.label("world_lost")
    asm.sw(ZERO, OFF_ACTIVE, T4)
    asm.j("shutdown")
    asm.nop()
    asm.label("world_ok")

    # ---- Select+Cross toggles whether the game still receives the pad ----
    asm.andi(T3, T1, BTN_SELECT)
    asm.beqz(T3, "no_toggle")
    asm.nop()
    asm.andi(T3, T1, BTN_CROSS)
    asm.beqz(T3, "no_toggle")
    asm.nop()
    asm.andi(T3, T7, BTN_CROSS)
    asm.bnez(T3, "no_toggle")
    asm.nop()
    asm.lw(T3, OFF_NEUTRALIZE, T4)
    asm.xori(T3, T3, 1)
    asm.sw(T3, OFF_NEUTRALIZE, T4)
    asm.label("no_toggle")

    asm.lwc1(QX, OFF_QUAT + 0x00, T4)
    asm.lwc1(QY, OFF_QUAT + 0x04, T4)
    asm.lwc1(QZ, OFF_QUAT + 0x08, T4)
    asm.lwc1(QW, OFF_QUAT + 0x0C, T4)

    # ---- yaw about world Z, pre-multiplied: (0,0,t,1) x q ----
    _axis(asm, 0x02, "rx", FA, negate=True)  # right stick X
    asm.lwc1(FB, OFF_LOOK_SCALE, T4)
    asm.mul_s(FA, FA, FB)  # FA = t
    asm.mul_s(FC, FA, QY)
    asm.sub_s(FC, QX, FC)  # x' = x - t*y
    asm.mul_s(FD, FA, QX)
    asm.add_s(FD, QY, FD)  # y' = y + t*x
    asm.mul_s(FE, FA, QW)
    asm.add_s(FE, QZ, FE)  # z' = z + t*w
    asm.mul_s(FF, FA, QZ)
    asm.sub_s(FF, QW, FF)  # w' = w - t*z
    asm.mov_s(QX, FC)
    asm.mov_s(QY, FD)
    asm.mov_s(QZ, FE)
    asm.mov_s(QW, FF)

    # ---- pitch about local X, post-multiplied: q x (t,0,0,1) ----
    _axis(asm, 0x03, "ry", FA, negate=True)  # right stick Y
    asm.lwc1(FB, OFF_LOOK_SCALE, T4)
    asm.mul_s(FA, FA, FB)  # FA = t
    asm.mul_s(FC, FA, QW)
    asm.add_s(FC, QX, FC)  # x' = x + t*w
    asm.mul_s(FD, FA, QZ)
    asm.add_s(FD, QY, FD)  # y' = y + t*z
    asm.mul_s(FE, FA, QY)
    asm.sub_s(FE, QZ, FE)  # z' = z - t*y
    asm.mul_s(FF, FA, QX)
    asm.sub_s(FF, QW, FF)  # w' = w - t*x

    # Reject the pitch if it would tip the forward vector past the limit:
    # forward.Z = 2*(y*z + w*x), same guard the desktop mod uses.
    asm.mul_s(FG, FD, FE)
    asm.mul_s(FH, FF, FC)
    asm.add_s(FG, FG, FH)
    asm.add_s(FG, FG, FG)
    asm.abs_s(FG, FG)
    asm.lwc1(FH, OFF_PITCH_LIMIT, T4)
    asm.c_lt_s(FH, FG)
    asm.bc1t("keep_pitch")
    asm.nop()
    asm.mov_s(QX, FC)
    asm.mov_s(QY, FD)
    asm.mov_s(QZ, FE)
    asm.mov_s(QW, FF)
    asm.label("keep_pitch")

    # ---- normalise once ----
    asm.mul_s(FA, QX, QX)
    asm.mul_s(FB, QY, QY)
    asm.add_s(FA, FA, FB)
    asm.mul_s(FB, QZ, QZ)
    asm.add_s(FA, FA, FB)
    asm.mul_s(FB, QW, QW)
    asm.add_s(FA, FA, FB)
    asm.lwc1(FB, OFF_ONE, T4)
    asm.rsqrt_s(FA, FB, FA)  # FA = 1 / sqrt(len2)
    asm.mul_s(QX, QX, FA)
    asm.mul_s(QY, QY, FA)
    asm.mul_s(QZ, QZ, FA)
    asm.mul_s(QW, QW, FA)
    asm.swc1(QX, OFF_QUAT + 0x00, T4)
    asm.swc1(QY, OFF_QUAT + 0x04, T4)
    asm.swc1(QZ, OFF_QUAT + 0x08, T4)
    asm.swc1(QW, OFF_QUAT + 0x0C, T4)

    # ---- basis vectors ----
    # right   = (1-2(y^2+z^2), 2(xy+wz), 2(xz-wy))
    # forward = (2(xy-wz),     1-2(x^2+z^2), 2(yz+wx))
    asm.lwc1(FH, OFF_ONE, T4)
    asm.mul_s(FA, QX, QY)  # xy
    asm.mul_s(FB, QW, QZ)  # wz
    asm.add_s(FC, FA, FB)
    asm.add_s(FC, FC, FC)  # right.y
    asm.sub_s(FD, FA, FB)
    asm.add_s(FD, FD, FD)  # forward.x
    asm.mul_s(FA, QX, QZ)  # xz
    asm.mul_s(FB, QW, QY)  # wy
    asm.sub_s(FE, FA, FB)
    asm.add_s(FE, FE, FE)  # right.z
    asm.mul_s(FA, QY, QZ)  # yz
    asm.mul_s(FB, QW, QX)  # wx
    asm.add_s(FF, FA, FB)
    asm.add_s(FF, FF, FF)  # forward.z
    asm.swc1(FC, OFF_RIGHT + 0x04, T4)
    asm.swc1(FE, OFF_RIGHT + 0x08, T4)
    asm.swc1(FD, OFF_FORWARD + 0x00, T4)
    asm.swc1(FF, OFF_FORWARD + 0x08, T4)
    asm.mul_s(FA, QY, QY)
    asm.mul_s(FB, QZ, QZ)
    asm.add_s(FA, FA, FB)
    asm.add_s(FA, FA, FA)
    asm.sub_s(FA, FH, FA)  # right.x
    asm.swc1(FA, OFF_RIGHT + 0x00, T4)
    asm.mul_s(FA, QX, QX)
    asm.add_s(FA, FA, FB)
    asm.add_s(FA, FA, FA)
    asm.sub_s(FA, FH, FA)  # forward.y
    asm.swc1(FA, OFF_FORWARD + 0x04, T4)

    # ---- movement ----
    asm.lwc1(FG, OFF_MOVE_SCALE, T4)
    asm.andi(T3, T1, BTN_L2)
    asm.beqz(T3, "no_fast")
    asm.nop()
    asm.lwc1(FA, OFF_FAST, T4)
    asm.mul_s(FG, FG, FA)
    asm.label("no_fast")
    asm.andi(T3, T1, BTN_R2)
    asm.beqz(T3, "no_slow")
    asm.nop()
    asm.lwc1(FA, OFF_SLOW, T4)
    asm.mul_s(FG, FG, FA)
    asm.label("no_slow")

    _axis(asm, 0x00, "lx", FA, negate=False)
    asm.mul_s(FA, FA, FG)  # strafe amount
    _axis(asm, 0x01, "ly", FB, negate=True)
    asm.mul_s(FB, FB, FG)  # forward amount

    for index, (right_off, fwd_off) in enumerate(
        ((OFF_RIGHT + 0x00, OFF_FORWARD + 0x00),
         (OFF_RIGHT + 0x04, OFF_FORWARD + 0x04),
         (OFF_RIGHT + 0x08, OFF_FORWARD + 0x08))
    ):
        asm.lwc1(FC, right_off, T4)
        asm.mul_s(FC, FC, FA)
        asm.lwc1(FD, fwd_off, T4)
        asm.mul_s(FD, FD, FB)
        asm.add_s(FC, FC, FD)
        asm.lwc1(FE, OFF_POS + index * 4, T4)
        asm.add_s(FE, FE, FC)
        asm.swc1(FE, OFF_POS + index * 4, T4)

    # World Z holds the vertical axis in this game, so L1/R1 act on it directly.
    asm.lwc1(FC, OFF_VSTEP, T4)
    asm.lwc1(FD, OFF_POS + 0x08, T4)
    asm.andi(T3, T1, BTN_R1)
    asm.beqz(T3, "no_up")
    asm.nop()
    asm.add_s(FD, FD, FC)
    asm.label("no_up")
    asm.andi(T3, T1, BTN_L1)
    asm.beqz(T3, "no_down")
    asm.nop()
    asm.sub_s(FD, FD, FC)
    asm.label("no_down")
    asm.swc1(FD, OFF_POS + 0x08, T4)




    # ---- player carry ----
    _resolve_human(asm, "no_player")

    # Select+R3 toggles carry, the same button the desktop mod uses for it.
    asm.label("carry_toggle")
    asm.andi(T3, T1, BTN_SELECT)
    asm.beqz(T3, "carry_hold")
    asm.nop()
    asm.andi(T3, T1, BTN_R3)
    asm.beqz(T3, "carry_hold")
    asm.nop()
    asm.andi(T3, T7, BTN_R3)
    asm.bnez(T3, "carry_hold")
    asm.nop()
    asm.lw(T3, OFF_CARRY_ENABLED, T4)
    asm.bnez(T3, "carry_disable")
    asm.nop()
    asm.lw(T2, HUMAN_SAFETY_OFFSET + 4, T5)
    asm.li(T3, NOTARGET_BIT_HIGH)
    asm.and_(T2, T2, T3)
    asm.sw(T2, OFF_NOTARGET_SAVED, T4)
    asm.sw(T5, OFF_GOD_HUMAN, T4)
    asm.li(T3, 1)
    asm.sw(T3, OFF_CARRY_ENABLED, T4)
    asm.j("carry_hold")
    asm.nop()
    asm.label("carry_disable")
    asm.lw(T2, HUMAN_SAFETY_OFFSET + 4, T5)
    asm.li(T3, NOTARGET_BIT_HIGH)
    asm.nor(T3, T3, ZERO)
    asm.and_(T2, T2, T3)
    asm.lw(T3, OFF_NOTARGET_SAVED, T4)
    asm.or_(T2, T2, T3)
    asm.sw(T2, HUMAN_SAFETY_OFFSET + 4, T5)
    asm.sw(ZERO, OFF_CARRY_ENABLED, T4)

    asm.label("carry_hold")
    asm.lw(T3, OFF_CARRY_ENABLED, T4)
    asm.beqz(T3, "no_player")
    asm.nop()
    asm.sw(T5, OFF_GOD_HUMAN, T4)
    _carry_apply(asm)
    asm.label("no_player")


    asm.j("done")
    asm.nop()

    # ---- leaving the freecam, for any reason, puts the world back ----
    asm.label("shutdown")
    asm.label("shutdown_carry")
    asm.lw(T3, OFF_CARRY_ENABLED, T4)
    asm.beqz(T3, "done")
    asm.nop()
    asm.sw(ZERO, OFF_CARRY_ENABLED, T4)
    asm.lw(T5, OFF_GOD_HUMAN, T4)
    asm.li(T3, 0x00600000)
    asm.sltu(T6, T5, T3)
    asm.bnez(T6, "done")
    asm.nop()
    asm.li(T3, 0x02000000)
    asm.sltu(T6, T5, T3)
    asm.beqz(T6, "done")
    asm.nop()
    asm.lw(T3, 0x00, T5)
    asm.li(T6, HUMAN_VTABLE)
    asm.bne(T3, T6, "done")
    asm.nop()
    asm.lw(T2, HUMAN_SAFETY_OFFSET + 4, T5)
    asm.li(T3, NOTARGET_BIT_HIGH)
    asm.nor(T3, T3, ZERO)
    asm.and_(T2, T2, T3)
    asm.lw(T3, OFF_NOTARGET_SAVED, T4)
    asm.or_(T2, T2, T3)
    asm.sw(T2, HUMAN_SAFETY_OFFSET + 4, T5)

    asm.label("done")
    asm.j(TICK_ORIGINAL_TARGET)
    asm.nop()
    return asm.assemble()


def build_getter() -> bytes:
    """GetTransform(a0 = destination, a1 = source) -> a0.

    Clobbers only v0, v1 and a2 - exactly what the original writes.
    """
    asm = Asm(CAVE_GETTER)

    asm.li(A2, STATE)
    asm.lw(V1, OFF_ACTIVE, A2)
    asm.beqz(V1, "passthrough")
    asm.nop()
    asm.lw(V1, OFF_GETTER_HITS, A2)
    asm.addiu(V1, V1, 1)
    asm.sw(V1, OFF_GETTER_HITS, A2)
    asm.li(V0, CAMERA_POINTER)
    asm.lw(V0, 0x00, V0)
    asm.bne(V0, A1, "passthrough")
    asm.nop()

    asm.lq(V1, OFF_POS, A2)
    asm.daddu(V0, A0, ZERO)
    asm.sq(V1, 0x00, A0)
    asm.lq(V1, OFF_QUAT, A2)
    asm.jr(RA)
    asm.sq(V1, 0x10, A0)

    asm.label("passthrough")
    asm.lq(A2, 0x10, A1)
    asm.daddu(V0, A0, ZERO)
    asm.sq(A2, 0x00, A0)
    asm.lq(V1, 0x20, A1)
    asm.jr(RA)
    asm.sq(V1, 0x10, A0)
    return asm.assemble()


def build_pad_hook() -> bytes:
    """Replace the call to the pad refresh so the freecam sees the real pad.

    The desktop mod established the shape of this: the refresh path treats the
    pad buffer as last frame's state, so the previous physical sample goes back
    before calling it.  The fresh sample is then copied into our state, and only
    afterwards is the buffer neutralised, so the game reads a still pad while
    the freecam still gets every stick and button.
    """
    asm = Asm(CAVE_PAD)

    asm.addiu(SP, SP, -0x20)
    asm.sd(RA, 0x10, SP)

    asm.li(T4, STATE)
    asm.li(T0, PAD)
    asm.ld(T2, OFF_PAD_CAPTURE, T4)
    asm.sd(T2, 0x00, T0)

    asm.jal(PAD_REFRESH_FUNCTION)
    asm.nop()

    asm.li(T4, STATE)
    asm.li(T0, PAD)
    asm.ld(T2, 0x00, T0)
    asm.sd(T2, OFF_PAD_CAPTURE, T4)

    asm.lw(T3, OFF_ACTIVE, T4)
    asm.beqz(T3, "leave")
    asm.nop()
    asm.lw(T3, OFF_NEUTRALIZE, T4)
    asm.beqz(T3, "leave")
    asm.nop()
    asm.li(T2, NEUTRAL_AXES)
    asm.sd(T2, 0x00, T0)
    asm.sd(ZERO, 0x08, T0)
    asm.sh(ZERO, 0x10, T0)

    asm.label("leave")
    asm.ld(RA, 0x10, SP)
    asm.jr(RA)
    asm.addiu(SP, SP, 0x20)
    return asm.assemble()


def pad_hook_word() -> int:
    asm = Asm(0)
    asm.jal(CAVE_PAD)
    return asm.words[0]


def check_layout() -> None:
    """The routines and the state block must not overlap each other."""
    tick_end = CAVE_TICK + len(build_tick())
    getter_end = CAVE_GETTER + len(build_getter())
    if tick_end > CAVE_GETTER:
        raise SystemExit(
            f"tick reaches 0x{tick_end:08X}, past the getter at 0x{CAVE_GETTER:08X}"
        )
    pad_end = CAVE_PAD + len(build_pad_hook())
    if getter_end > CAVE_PAD:
        raise SystemExit(
            f"getter reaches 0x{getter_end:08X}, past the pad hook at 0x{CAVE_PAD:08X}"
        )
    if pad_end > STATE:
        raise SystemExit(
            f"pad hook reaches 0x{pad_end:08X}, past the state at 0x{STATE:08X}"
        )
    if STATE + STATE_SIZE > CAVE_WINDOW[1]:
        raise SystemExit("state block runs past the cave window")


def tick_hook_word() -> int:
    asm = Asm(0)
    asm.jal(CAVE_TICK)
    return asm.words[0]


def state_blob(neutralize: bool = False) -> bytes:
    blob = bytearray(STATE_SIZE)
    struct.pack_into("<I", blob, OFF_MAGIC, MAGIC_VALUE)
    struct.pack_into("<f", blob, OFF_MOVE_SCALE, MOVE_PER_FRAME / 127.0)
    struct.pack_into("<f", blob, OFF_VSTEP, VERTICAL_PER_FRAME)
    struct.pack_into("<f", blob, OFF_LOOK_SCALE, LOOK_PER_FRAME / 127.0)
    struct.pack_into("<f", blob, OFF_ONE, 1.0)
    struct.pack_into("<f", blob, OFF_PITCH_LIMIT, PITCH_LIMIT)
    struct.pack_into("<I", blob, OFF_NEUTRALIZE, 1 if neutralize else 0)
    struct.pack_into("<f", blob, OFF_FAST, FAST_MULTIPLIER)
    struct.pack_into("<f", blob, OFF_SLOW, SLOW_MULTIPLIER)
    struct.pack_into("<f", blob, OFF_PAUSE_SCALE, PAUSE_SCALE)
    struct.pack_into("<f", blob, OFF_CARRY_FWD, CARRY_FORWARD)
    struct.pack_into("<f", blob, OFF_CARRY_UP, CARRY_UP)
    # A neutral pad until the pad hook fills it, so a zeroed capture is
    # never read as both sticks slammed to the corner.
    struct.pack_into("<I", blob, OFF_PAD_CAPTURE, NEUTRAL_AXES)
    blob[OFF_SIGNATURE : OFF_SIGNATURE + len(SIGNATURE)] = SIGNATURE
    for offset, text in ((OFF_FMT_CAM, FMT_CAM), (OFF_FMT_NUM, FMT_NUM),
                         (OFF_FMT_PLR, FMT_PLR)):
        terminated = text + bytes(1)
        blob[offset : offset + len(terminated)] = terminated
    return bytes(blob)
