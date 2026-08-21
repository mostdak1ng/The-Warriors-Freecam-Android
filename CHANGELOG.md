# Changelog

## 0.2.0 - 2026-08-21

- First release. A guest-side mod delivered as a single `.pnach`, so it runs on
  Android PS2 emulators as well as on desktop PCSX2, with no companion program.
- Freecam with camera-relative movement and right-stick look, toggled with
  `Select`+`L3`. `L1`/`R1` drop and lift, `L2` and `R2` scale the speed.
- Camera and player coordinates on screen, using the game's own text system, on
  `Select`+`Triangle`. Shown by default, in or out of the freecam.
- World pause, scaling the native world timestep to 0.01%, on `Select`+`Square`.
- God mode on `Select`+`Circle`, kept across level changes and respawns, and
  restoring the player's original bit when switched off.
- Player carry on `Select`+`R3`, holding the player in front of the camera with
  gravity and enemy targeting suppressed.
- `Select`+`Cross` hands the game a neutral pad so the player stays put while
  the camera moves.
- Works through cutscene and scripted cameras: the transform getter is replaced
  in all fifteen camera vtables, and substitution is limited at run time to the
  camera the game considers active.
- Restores the world timestep, the player's targeting flag and the camera when
  the world goes away, as it does on a level transition or a loaded save state.
