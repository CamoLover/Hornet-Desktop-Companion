# Hornet Desktop Companion

> A physics-based desktop companion featuring Hornet from *Hollow Knight*. She sits on your desktop, falls with gravity, bounces off the floor, plays a looping Needoline soundtrack when sitting, and reacts to velocity with different sprites.

---

## Features

- **Physics simulation** -  gravity, bounce damping, and friction
- **Animated idle** -  6-frame idle animation cycle
- **Full sitting sequence** -  sit-down → pause → intro → looping play → outro → pause → get-up, with smooth position transitions
- **Music** -  plays randomised segments of *Needoline* during the sitting loop; stops when she stands
- **Tray icon** -  control volume, pick a song, and hot-reload config without restarting
- **Click-through** -  window is invisible to mouse clicks when not hovering (Windows)
- **Pixel-perfect transparency** -  no black box; uses the SHAPE extension on Linux or layered windows on Windows
- **Throwable** -  drag and release with momentum to fling her

---

## Quick Start

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

---

## Controls

| Action | Input |
|---|---|
| Drag Hornet | Left-click and drag |
| Throw her | Drag, then release with momentum |
| Sit / play music | Drop her on the floor and click when stationary |
| Stop sitting | Click her while in the looping sit animation |
| Quit | `ESC` |

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

## Tray Icon

Right-click (Windows) or click (Linux) the tray icon to access:

| Entry | Effect |
|---|---|
| **Songs → Random** | Pick a random Needoline segment each time she sits |
| **Songs → [name]** | Lock to a specific segment |
| **Volume → 0–100%** | Set playback volume |
| **Reload Config** | Hot-reload `config.json` -  applies all values instantly, including scale |
| **Quit** | Close the companion |

Available songs: Default Melody, Beastling Call, Elegy of the Deep, Conductor Melody, Vaultkeeper Melody, Architect Melody, Trial End.

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
| `assets/audio/needoline.mp3` | Background music track |
| `assets/logo/` | App icon (PNG + ICO) |

---

## Tuning (config.json)

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
| `volume` | `1.0` | Music volume (0.0 – 1.0) |
| `scale` | `100` | Sprite scale percentage (50 = half size, 200 = double) |

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

### macOS
- pygame transparency is unreliable on macOS -  the window may show a black background.
- Functional but not fully tested; Windows and Linux are better supported.

---

## Legal

This project is a fan tool and is not affiliated with or endorsed by Team Cherry.

Hollow Knight and all associated assets, characters, and music are property of **Team Cherry**.

This project itself is released under the [MIT License](LICENSE).
