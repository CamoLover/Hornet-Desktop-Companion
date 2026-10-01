# Hornet Desktop Companion

<div align="center">

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)](#platform-notes)
[![Stars](https://img.shields.io/github/stars/CamoLover/Hornet-Desktop-Companion?style=social)](https://github.com/CamoLover/Hornet-Desktop-Companion/stargazers)
[![Last Commit](https://img.shields.io/github/last-commit/CamoLover/Hornet-Desktop-Companion)](https://github.com/CamoLover/Hornet-Desktop-Companion/commits/main)
[![Issues](https://img.shields.io/github/issues/CamoLover/Hornet-Desktop-Companion)](https://github.com/CamoLover/Hornet-Desktop-Companion/issues)
![GitHub Repo Views](https://gitviews.com/repo/CamoLover/Hornet-Desktop-Companion.svg)

*A physics-based desktop companion featuring Hornet from* Hollow Knight. *She sits, sleeps, falls with gravity, bounces or soft-lands, plays the Needoline soundtrack when sitting, reacts to velocity with different sprites, and gets annoyed if you hover near her too long.*

![Hornet Desktop Companion demo](assets/hdc.gif)

</div>

---

## Features

- **Physics simulation** -  gravity, bounce damping, and friction
- **Animated idle** -  6-frame idle animation cycle
- **Full sitting sequence** -  sit-down → pause → intro → looping play → outro → pause → get-up, with smooth position transitions
- **Sleep** -  after 5 minutes of inactivity on the ground, Hornet falls asleep; a small "z" floats above her head while she sleeps; click her to wake up
- **Soft landing** -  optional mode where Hornet doesn't bounce; plays a landing animation on the floor, and wall-slide / wall-cling animations against screen edges
- **Umbrella glide** -  optional landing mode: drop her from high up and she opens her cloak like an umbrella, drifting down slowly with a gentle sway (soft landing still applies on walls and the floor)
- **Taunt** -  hover the cursor near Hornet long enough and she'll get annoyed and taunt you; has a cooldown
- **Music** -  plays randomised segments of *Needoline* during the sitting loop; stops when she stands
- **Velocity-reactive sprites** -  fast-fall and tumble sprites trigger based on speed and direction
- **Tray icon** -  control volume, pick a song, and hot-reload config without restarting
- **Click-through** -  window is invisible to mouse clicks when not hovering (Windows)
- **Pixel-perfect transparency** -  no black box; uses the SHAPE extension on Linux or layered windows on Windows
- **Throwable** -  drag and release with momentum to fling her
- **Cloak recoloring** -  choose from color presets or a custom hex color for Hornet's cloak, from the tray icon
- **Spawn animation** -  choose whether Hornet falls in from the top or runs in from the left/right edge of the screen on launch
- **Wandering** -  when left alone she walks around on her own, turns, stops, pulls out her map to read it, and sometimes strolls while reading (toggle from the menu)
- **Climbing windows** (Windows, Linux X11) -  your open windows become platforms: she lands on them, climbs their sides, wall-jumps off the screen edge to reach high ones, sits on top, and jumps back down. Move a window and she falls off; close it and she tumbles down

---

## Quick Start

**Requirements:** Python 3.8+

**Linux / macOS**

```bash
chmod +x run.sh
./run.sh
```

**Windows**

```bat
run.bat
```

The script creates a `.venv`, installs dependencies, and launches the companion. All sprites are bundled -  no extraction step needed.

### Dependencies

| Package | Version |
|---|---|
| `pygame` | >= 2.5.0 |
| `Pillow` | >= 10.0.0 |
| `numpy` | >= 1.24.0 |
| `pystray` | >= 0.19.0 |

---

## Controls

| Action | Input |
|---|---|
| Drag Hornet | Left-click and drag |
| Throw her | Drag, then release with momentum |
| Sit / play music | Drop her on the floor and click when stationary |
| Stop sitting | Click her while in the looping sit animation |
| Wake up | Click her while she is sleeping |
| Quit | `ESC` |

> Dragging is disabled during sleep and sleep transitions. Clicks during the falling-asleep or waking animations are ignored.

---

## Sitting Animation Sequence

| Phase | Sprites | Notes |
|---|---|---|
| Sit down | `sit_down/sit_1–4.png` | Plays once |
| Pause | -  | `sit_pause_dur` seconds |
| Intro | `sit_intro/sit_play_1–4.png` | Plays once; music starts at end |
| Loop | `sit_loop/hornet_sit_play_1–11.png` | Loops until clicked |
| Outro | `sit_outro/sit_end_1–4.png` | Plays once; music stops at start |
| Pause | -  | `sit_pause_dur` seconds |
| Get up | `sit_up/sit_get_up_1–7.png` | Plays once; y-offset eases back to idle; returns to idle |

Dragging Hornet during any sit phase cancels the sequence immediately.

---

## Sleep

If Hornet is left idle on the ground for `sleep_timeout` seconds (default 5 minutes), she falls asleep automatically.

| Phase | Sprites | Notes |
|---|---|---|
| Falling asleep | `sleep_wake/sleep_wake_*.png` (reversed) | Plays once |
| Sleeping | `sleep_wake/sleep_wake_1.png` | Held until clicked; a floating "z" is drawn above her head |
| Waking | `sleep_wake/sleep_wake_*.png` (forward) | Plays once; returns to idle |

Click her while she is sleeping to wake her up. The inactivity timer resets any time she is dragged, thrown, or clicked.

The "z" overlay can be disabled by setting `sleep_z` to `false` in `config.json`.

---

## Landing Modes

Pick a mode from the **Landing** menu (tray or right-click), or set `land_mode` in `config.json`:

| Mode | Behaviour |
|---|---|
| `bounce` | Default -  Hornet bounces off the floor and walls |
| `soft` | No bouncing; landing and wall-cling animations play instead (see below) |
| `glide` | Same as `soft`, but high falls open the umbrella and she floats down |

### Soft Landing

In `soft` and `glide` modes, Hornet does not bounce on impact. Instead:

| Situation | Sprites | Notes |
|---|---|---|
| Hitting the floor | `land/land_1–10.png` | Landing animation plays once, then transitions to idle |
| Sliding down a wall | `wall_slide/wall_slide_1–9.png` | Plays while descending along a screen edge |
| Reaching the wall bottom | `wall_cling/wall_cling_1–4.png` | Cling animation plays once before transitioning to idle |

### Umbrella Glide

In `glide` mode, once Hornet is falling fast and is at least `glide_min_height` px above the floor:

| Phase | Sprites | Notes |
|---|---|---|
| Opening | `umbrella_open/umbrella_open_1–5.png` | Cloak inflates into an umbrella; her fall slows down |
| Floating | `umbrella_float/umbrella_float_1–11.png` | Looping drift at `glide_fall_vy`, swaying side to side |
| Closing | `umbrella_close/umbrella_close_1–2.png` | Plays when the needle touches the floor, then the soft landing |

Drifting into a screen edge while gliding still triggers the wall cling / wall slide.

---

## Climbing Windows

On Windows, the top edge of every visible, non-maximized window is a platform (only the parts not hidden behind other windows). She can be thrown onto them and lands there, and while wandering she goes exploring:

| Route | When | Animations |
|---|---|---|
| Jump on top | the window top is low (or below her) | run, `jump` / `hop` / `somersault`, `hop_land` |
| Climb the side | the window reaches down near her | `climb` (repeated scramble leaps), `wall_mantle`, `mantle_land` |
| Jump and grab the side | the window floats a little above her | `jump`, then climb the side |
| Screen edge + wall jump | the window is too high, near a screen edge | climb the edge, `climb_cling`, `walljump_antic`, `walljump`, then land on top or grab its side |

Up there she strolls, reads her map, or sits quietly with her legs over the edge (`sit_rest`, no music; click her to make her play). Then she jumps down to the taskbar, steps off the edge, or moves on to another window.

| Window event | Reaction |
|---|---|
| Moved (or resized from under her) | Falls off -  `fall`, then the landing animation |
| Closed / minimized | Tumbles -  `weak_fall`, then `bonk_land` |
| Covered by another window | She keeps standing |

Windows whose top is closer to the top of the screen than her height are skipped (there'd be no room for her). Toggle with **Climb Windows** in the menu, or `window_platforms` in `config.json`.

---

## Taunt

If the cursor hovers near Hornet for `taunt_hover_time` seconds (default 2.5 s) while she is idle or on the ground, she gets annoyed and plays her taunt animation.

| Phase | Sprites |
|---|---|
| Taunt | `taunt/taunt_1–19.png` |
| Silk effect | `taunt/taunt_silk_1–8.png` |

After taunting, she enters a cooldown (`taunt_cooldown`, default 120 s) before she can be triggered again.

---

## Tray Icon

Right-click (Windows) or click (Linux) the tray icon to access:

| Entry | Effect |
|---|---|
| **Songs → Random** | Pick a random Needoline segment each time she sits |
| **Songs → [name]** | Lock to a specific segment |
| **Volume → 0–100%** | Set playback volume |
| **Cloak Color → [preset]** | Recolor Hornet's cloak; takes effect immediately |
| **Cloak Color → Custom…** | Pick any color via a color picker dialog |
| **Spawn Mode → Fall (Default)** | Hornet drops in from the top on launch |
| **Spawn Mode → Walk from Right / Left** | Hornet runs in from the chosen screen edge and skids to a stop on launch |
| **Reload Config** | Hot-reload `config.json` -  applies all values instantly, including scale |
| **Reset Topmost** | Force the window back to the top of the z-order (Windows only) |
| **Quit** | Close the companion |

Available songs: Default Melody, Beastling Call, Elegy of the Deep, Conductor Melody, Vaultkeeper Melody, Architect Melody, Trial End.

Available cloak presets: Default, Red, Orange, Yellow, Green, Teal, Blue, Purple, Pink -  or any custom hex color.

> Spawn mode is saved to `config.json` and applies the next time the companion is launched.

---

## Configuration (`config.json`)

All values hot-reload instantly via **Tray → Reload Config**.

| Key | Default | Effect |
|---|---|---|
| `gravity` | `1800.0` | Downward acceleration (px/s²) |
| `bounce_damp` | `0.45` | Velocity fraction retained after bouncing |
| `friction` | `0.88` | Horizontal slowdown per bounce |
| `min_bounce_vy` | `80.0` | Minimum vertical speed to keep bouncing |
| `fast_fall_vy` | `300.0` | Vertical speed threshold for fast-fall sprite |
| `wrong_mix` | `0.65` | Horizontal speed ratio that triggers the tumble sprite |
| `idle_fps` | `0.15` | Seconds per frame for the idle animation |
| `sit_fps` | `0.1` | Seconds per frame for all sit animations |
| `sit_pause_dur` | `0.25` | Pause duration (seconds) between sit-down→intro and outro→get-up |
| `sit_y_offset` | `0.235` | Downward position offset while sitting (fraction of sprite height) |
| `idle_y_offset` | `-0.075` | Vertical position offset while idle (fraction of sprite height) |
| `on_ground_tol` | `8` | Pixel tolerance for "on ground" detection |
| `sleep_timeout` | `300.0` | Seconds of ground inactivity before falling asleep |
| `sleep_y_offset` | `0.12` | Vertical position offset during sleep transition frames (fraction of sprite height) |
| `volume` | `1.0` | Music volume (0.0 – 1.0) |
| `scale` | `100` | Sprite scale percentage (50 = half size, 200 = double) |
| `sleep_z` | `true` | Show a floating "z" above Hornet's head while she sleeps |
| `land_mode` | `"bounce"` | Landing mode -  `"bounce"`, `"soft"`, or `"glide"` (replaces the old `soft_land` toggle) |
| `glide_fall_vy` | `140.0` | Fall speed (px/s) while gliding with the umbrella |
| `glide_min_height` | `250.0` | Minimum height (px) above the floor for the umbrella to open |
| `glide_sway_amp` | `30.0` | Side-to-side sway amplitude (px) while gliding |
| `glide_fps` | `0.07` | Seconds per frame for the umbrella float loop |
| `land_fps` | `0.04` | Seconds per frame for landing and wall-cling animations |
| `wall_slide_fps` | `0.08` | Seconds per frame for the wall-slide animation |
| `taunt_fps` | `0.06` | Seconds per frame for the taunt animation |
| `taunt_cooldown` | `120.0` | Seconds before Hornet can be taunted again |
| `taunt_hover_time` | `2.5` | Seconds the cursor must hover near Hornet to trigger a taunt |
| `cloak_color` | `"default"` | Cloak hue -  `"default"` or a `"#RRGGBB"` hex string |
| `spawn_mode` | `"fall"` | How Hornet enters on launch -  `"fall"`, `"walk_from_right"`, or `"walk_from_left"` |
| `wander` | `true` | Let Hornet walk around and read her map on her own while idle |
| `wander_idle_min` | `4.0` | Minimum seconds she stands still between activities |
| `wander_idle_max` | `12.0` | Maximum seconds she stands still between activities |
| `wander_walk_fps` | `0.07` | Seconds per walk frame; walk speed follows it so her feet never slide |
| `window_platforms` | `true` | Windows and Linux (X11) -  stand on, climb and jump between open windows |

---

## File Overview

| File / Folder | Purpose |
|---|---|
| `companion.py` | Main app -  physics, animation, rendering, platform integration |
| `run.sh` / `run.bat` | One-shot launcher scripts |
| `requirements.txt` | Python dependencies |
| `config.json` | Tunable physics, animation and display parameters |
| `assets/sprites/idle/` | 6-frame idle animation |
| `assets/sprites/fast_fall/` | Fast-fall and tumble sprites |
| `assets/sprites/sit_down/` | Sit-down transition (4 frames) |
| `assets/sprites/sit_intro/` | Intro to playing (4 frames) |
| `assets/sprites/sit_loop/` | Looping sit animation (11 frames) |
| `assets/sprites/sit_outro/` | Outro from playing (4 frames) |
| `assets/sprites/sit_up/` | Get-up transition (7 frames) |
| `assets/sprites/land/` | Soft landing animation (10 frames) |
| `assets/sprites/wall_slide/` | Wall-slide animation (9 frames) |
| `assets/sprites/wall_cling/` | Wall-cling animation (4 frames) |
| `assets/sprites/umbrella_open/` | Umbrella glide opening (5 frames) |
| `assets/sprites/umbrella_float/` | Umbrella glide float loop (11 frames) |
| `assets/sprites/umbrella_close/` | Umbrella glide closing (2 frames) |
| `assets/sprites/taunt/` | Taunt animation (19 frames + 8-frame silk effect) |
| `assets/sprites/sleep_wake/` | Sleep / wake transition (14 frames, played forward and reversed) |
| `assets/sprites/walk/` | Walk cycle, used while wandering (10 frames) |
| `assets/sprites/walk_stop/` | Walk-to-idle transition, played backwards to start walking (5 frames) |
| `assets/sprites/turn/` | Turn-around while walking (3 frames) |
| `assets/sprites/map_open/` | Pull out the map, played backwards to put it away (2 frames) |
| `assets/sprites/map_idle/` | Standing and reading the map (6 frames) |
| `assets/sprites/map_walk/` | Walking while reading the map (10 frames) |
| `assets/sprites/map_turn/` | Turn-around while holding the map (2 frames) |
| `assets/sprites/run/`, `run_start/`, `run_stop/` | Running in on launch and to windows (10 / 8 / 6 frames) |
| `assets/sprites/jump/`, `hop/`, `somersault/` | Jumps (15 / 6 / 13 frames) |
| `assets/sprites/hop_land/` | Light landing after a jump (6 frames) |
| `assets/sprites/climb/`, `climb_cling/` | Wall scramble leap (crouch, leap, settle; 7 frames) and cling (8 frames) |
| `assets/sprites/walljump_antic/`, `walljump/` | Wall jump with somersault (3 / 14 frames) |
| `assets/sprites/wall_mantle/`, `mantle_land/` | Pulling up over a window's corner (2 / 6 frames) |
| `assets/sprites/fall/` | Falling off a moved window (7 frames) |
| `assets/sprites/weak_fall/`, `bonk_land/` | Tumbling off a closed window and landing (6 / 7 frames) |
| `assets/sprites/sit_rest/` | Quiet sitting loop on a window (42 frames) |
| `assets/audio/needoline.mp3` | Background music track |
| `assets/logo/` | App icon (PNG + ICO) |

---

## Platform Notes

### Windows
- Full transparency and always-on-top via the Win32 layered window API.
- Click-through is toggled automatically when not hovering over Hornet.
- `run.bat` uses `pythonw` to suppress the console window.

### Linux
- Requires a compositor (picom, kwin, mutter) for background transparency. Without one, the SHAPE extension is used for pixel-perfect clipping.
- Always-on-top is set via EWMH `_NET_WM_STATE_ABOVE`. If it doesn't stick: `wmctrl -r "Hornet" -b add,above`
- On Wayland: `SDL_VIDEODRIVER=x11 python companion.py`
- Window climbing works on X11 sessions with an EWMH window manager (GNOME, KDE, Xfce, Cinnamon, i3, Openbox...). It's turned off on Wayland, where native windows can't be seen by other apps.
- Multi-monitor layouts are read from `xrandr --listmonitors`. X11 only reports one desktop-wide work area, so a panel on one monitor also trims the floor of the others.

### macOS
- pygame transparency is unreliable on macOS -  the window may show a black background.
- Functional but not fully tested; Windows and Linux are better supported.

---

## Contributing

Issues and pull requests are welcome. If you find a bug or have a feature request, please [open an issue](https://github.com/CamoLover/Hornet-Desktop-Companion/issues).

---

## Legal

This project is a fan tool and is not affiliated with or endorsed by Team Cherry.

Hollow Knight and all associated assets, characters, and music are property of **Team Cherry**.

This project itself is released under the [GNU General Public License v3.0](LICENSE) -  you are free to use, modify, and distribute it under the same terms.
