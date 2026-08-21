# The Warriors Freecam (Android / pnach)

A free camera, on-screen coordinates, world pause, God mode and player carry for
the USA release of *The Warriors*, delivered as a single cheat file. Everything
runs **inside the emulated PlayStation 2**, so it needs no companion program, no
memory access from outside the emulator, and no root.

That is what makes it work on Android. The desktop
[The Warriors Freecam](https://github.com/mostdak1ng/The-Warriors-Freecam) drives
the game from a Windows program over PCSX2's PINE socket; Android sandboxes every
app away from every other app's memory, so that approach cannot be ported. This
one is a text file the emulator applies by itself, and it behaves identically on
desktop PCSX2 and on Android PS2 emulators that read `.pnach` cheats.

`The Warriors Freecam v0.2.0 - mostdak1ng` is written into the emulated machine's
memory at a fixed address, where a memory search will find it. There is no
on-screen watermark: a guest-side mod has no overlay to draw one in.

## Requirements

- *The Warriors*, USA release: serial `SLUS-21215`, version `1.03`, CRC
  `B99A75DE`. The cheat file is named after that CRC and applies to nothing else.
- An emulator that reads PCSX2 `.pnach` cheats:
  - **Desktop:** PCSX2. Verified on 2.7.523.
  - **Android:** NetherSX2 or AetherSX2.
- A controller, or a touch layout that includes `L3` and `R3`. Every combination
  uses `Select` plus one other button.

## Installing

**Desktop PCSX2**

1. Copy `B99A75DE.pnach` into the `cheats` folder beside your PCSX2 files.
2. **Settings → Emulation → Enable Cheats**, or enable Cheats in the per-game
   settings.
3. Restart the game. Patches are read when the game boots, not while it runs.

**Android (NetherSX2 / AetherSX2)**

1. Copy `B99A75DE.pnach` to `Android/data/<emulator package>/files/cheats/`,
   creating the `cheats` folder if it is not there.
2. **Settings → System → Enable Cheats/Patches.**
3. Restart the game.

NetherSX2 enables every cheat in the file at once; there is no per-cheat switch.
If you already have a widescreen or no-interlace `B99A75DE.pnach`, merge the
files rather than replacing one with the other - keep both sets of `patch=`
lines under their own headings.

## Controls

| Input | Action | Works outside the freecam |
| --- | --- | --- |
| `Select` + `L3` | Enter or leave the freecam | - |
| Left stick | Move, relative to where the camera is looking | |
| Right stick | Look | |
| `L1` / `R1` | Move down / up | |
| `L2` | Fast movement (4x) | |
| `R2` | Precise movement (0.25x) | |
| `Select` + `Square` | World pause on/off | yes |
| `Select` + `Circle` | God mode on/off | yes |
| `Select` + `Triangle` | Hide/show the coordinate readout | yes |
| `Select` + `R3` | Carry the player with the camera | freecam only |
| `Select` + `Cross` | Stop the player moving with the camera | freecam only |

Button names follow the PS2 layout, matching the desktop mod's scheme.

### Coordinate readout

Camera and player coordinates appear on screen from the moment the game starts,
so you can note a position during ordinary play. While the freecam is on it
shows the freecam's own position; the rest of the time it shows the game's
camera. It also makes God mode visible, which otherwise leaves no sign that it
is on.

The readout borrows the game's tutorial-text slot, and that slot holds one
message at a time. **While the readout is visible the game's own tutorial
messages will not appear** - they queue behind it. Hide it with
`Select` + `Triangle` to let them through. Mission objectives, shown with the
D-pad, and cutscenes both take the screen normally and the readout returns
afterwards.

### Moving the camera without moving the player

By default the game still receives your input, so the character walks around
while the camera flies. `Select` + `Cross` hands the game a neutral pad while
the freecam keeps reading the real one, which leaves the character standing
still. Press it again to give control back.

### Player carry

`Select` + `R3` holds the player slightly in front of and below the camera, so
you can fly somewhere and bring the character along. Gravity is suppressed while
it is on, and enemies stop targeting the body. Leaving the freecam releases it
and restores the targeting flag.

### World pause

The world timestep is scaled to 0.01% of its own value rather than set to zero,
so actors, timers and simulation are frozen for practical purposes while the
camera keeps moving. The exact original timestep bits are restored when you
switch it off, and automatically if the world goes away underneath it.

### God mode

Only the game's God bit is touched. It survives leaving the freecam, level
changes and respawns on purpose - it is the only way to switch it, so dropping
it on every exit would make it useless. It stays on until you turn it off, and
then the value the player had before is put back.

## Leaving the freecam

`Select` + `L3` hands the camera back to the game, which resumes whatever it was
doing - gameplay camera, scripted shot, or cutscene - and releases player carry.

World pause, God mode and the readout are deliberately independent of the
freecam and keep their state.

## Save states

Loading a save state replaces the emulated machine's memory, including this
mod's code and its working state. The patches are re-applied on the next frame,
so the mod repairs itself; but the freecam turns off, and anything it had
changed at the moment the state was *created* is part of that state.

Back up your memory card and your save states before using the mod. Prefer
turning the freecam off before saving a state.

## Testing status

Verified in play on **desktop PCSX2 2.7.523** and on **Android** (an ARM64
device running NetherSX2), with `SLUS-21215` v1.03.

What has been exercised: freecam movement and look, camera-relative movement,
cutscene and scripted cameras, world pause, God mode, player carry, the
player-freeing toggle, the coordinate readout and its interaction with the
game's own tutorial text and objectives, and restoration on exit.

Reports are welcome, particularly on whether the combinations are reachable in
the default touch layout of Android emulators.

## How it works

Three hooks, all of them single words in the game's static memory, which is
exactly what a `.pnach` can write:

| Hook | Where | What it does |
| --- | --- | --- |
| Per-frame tick | a call site that runs once a frame | reads the pad, turns and moves the camera, applies pause, God mode, carry and the readout |
| Transform getter | one slot in each of the 15 camera vtables | hands our pose to everything that asks where the camera is |
| Pad refresh | the game's pad refresh call | captures the physical pad, and optionally hands the game a neutral one |

The interesting one is the middle. The game rewrites the camera transform every
frame, so writing to it is a race - the desktop mod wins that race by brute
force, writing roughly 300 times per frame from outside the emulator, which is
not something guest code can do. Instead of competing to *write* the camera,
this mod replaces the class method every consumer calls to *read* it. When the
freecam is off, that method is byte-for-byte the original. When it is on, and
only for the camera the game currently considers active, it returns our pose
instead. Timing within the frame stops mattering.

Rotation avoids trigonometry entirely. Yaw and pitch are applied as unnormalised
delta quaternions `(0,0,t,1)` and `(t,0,0,1)`, which collapse to four multiplies
each, and the pair is normalised once with the R5900's `rsqrt.s`.

The readout reuses the game's own text system rather than drawing anything: the
game's `snprintf` formats the numbers and its tutorial-text call displays them.
Since the R5900 has no double-precision FPU and `snprintf` wants a double, the
double's bit pattern is assembled with integer instructions - the R5900's
integer registers are 64 bits wide even though its FPU is not.

### A note for anyone changing the routine

The tick began life calling nothing at all, so it uses `t0`-`t7` freely on the
assumption that nothing clobbers them. The readout broke that assumption the
moment it called `snprintf`, and the pad pointer and button words now have to be
saved and restored around it. Any new call into the game needs the same care.

## Building from source

`B99A75DE.pnach` is generated, not hand-written. With Python 3 and this
repository:

```sh
python tools/make_pnach.py
```

`tools/freecam_routine.py` assembles the EE (MIPS R5900) routines through the
small assembler in `tools/mips.py`, and `tools/make_pnach.py` emits them as
patch lines.

The remaining tools are the development loop, which drives a running PCSX2 over
its PINE socket so the routines can be changed without restarting the game:
`install.py` writes them into a live session, `padlog.py` reports which buttons
reach the game, `profile_vtable.py` counts vtable method calls, `probe_text.py`
and `probe_sprintf.py` exercise the game's text and formatting functions, and
`elf_bytes.py` reads static data straight out of the ELF in the disc image.
None of it is needed to *use* the mod.

## License

Copyright (C) 2026 mostdak1ng.

Licensed under the GNU General Public License version 3 only. See `LICENSE`.

The generated `.pnach` is a build artifact of the sources in `tools/` and
carries the same license.
