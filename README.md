# Hornet Desktop Companion

> A physics-based desktop companion featuring Hornet from *Hollow Knight*. She sits on your desktop, falls with gravity, bounces off the floor, plays a looping Needles of the Deep soundtrack when at rest, and reacts to velocity with different sprites.

---

## Features

- **Physics simulation**-  gravity, bounce damping, and friction
- **Velocity-reactive sprites**-  four states: idle, slow fall, fast fall, and tumbling fall
- **Sitting animation**-  multi-frame sit cycle when she lands and comes to rest
- **Music**-  plays randomized segments of *Needoline* while sitting; stops when thrown
- **Click-through**-  the window is invisible to mouse clicks when not hovering (Windows)
- **Pixel-perfect transparency**-  no black box; uses the SHAPE extension on Linux or layered windows on Windows
- **Throwable**-  drag and release with momentum to fling her

---

## Requirements

- Python 3.9+
- The Hornet sprite atlas from Hollow Knight's game files (`assets/atlas/atlas1 #34863.png`)

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

The script creates a `.venv`, installs dependencies, extracts sprites from the atlas on first run, and launches the companion. Pass `--force` to re-extract sprites:

```bash
./run.sh --force
```

---

## Controls

| Action | Input |
|---|---|
| Drag Hornet | Left-click and drag |
| Throw her | Drag, then release with momentum |
| Land and sit | Drop her on the floor and let her settle |
| Quit | `ESC` |

---

## Sprite States

| State | Trigger |
|---|---|
| **Idle** | Slow or stationary / on the ground |
| **fall** | Falling gently |
| **Sitting** | Landed and at rest-  7-frame animation |

---

## File Overview

| File | Purpose |
|---|---|
| `companion.py` | Main app-  physics, rendering, platform integration |
| `extract_sprite.py` | Extracts sprites from the atlas on first run |
| `run.sh` / `run.bat` | One-shot launcher scripts |
| `requirements.txt` | Python dependencies |
| `assets/atlas/` | atlas sprite sheets |
| `assets/sprites/` | Extracted sprites (auto-generated) |
| `assets/audio/needoline.mp3` | Background music track |

---

## Tuning

**Sprite rotations**-  edit the `SPRITES` list in `extract_sprite.py`. Each entry is `(blob_idx, filename, rotation_degrees_CCW, flip_h)`. Re-extract with `python extract_sprite.py --force`.

**Physics**-  edit these constants at the top of `companion.py`:

| Constant | Default | Effect |
|---|---|---|
| `GRAVITY` | `1800.0` | Downward acceleration (px/s²) |
| `BOUNCE_DAMP` | `0.45` | Velocity retained after bouncing |
| `FRICTION` | `0.88` | Horizontal slowdown per frame |
| `FAST_FALL_VY` | `550.0` | Speed threshold for fast-fall sprite |
| `WRONG_MIX` | `0.65` | Horizontal-component ratio for tumbling |

---

## Platform Notes

### Linux
- Requires a compositor (picom, kwin, mutter) for background transparency. Without one, the SHAPE extension is used for pixel-perfect clipping.
- Always-on-top is set via `xprop` / `wmctrl`. If it doesn't stick: `wmctrl -r "Hornet" -b add,above`
- On Wayland: `SDL_VIDEODRIVER=x11 python companion.py`

### Windows
- Full transparency and always-on-top via the Win32 layered window API.
- Click-through is toggled automatically when not hovering over Hornet.
- `run.bat` uses `pythonw` to suppress the console window.

### macOS
- pygame transparency is unreliable on macOS-  the window may show a black background.
- Functional but not fully tested; Windows and Linux are better supported.

---

## Contributing

Pull requests are welcome. A few things to keep in mind:

- Keep the dependency list minimal (`pygame`, `Pillow`, `scipy`, `numpy`)
- Platform-specific code lives in clearly marked sections-  keep it that way
- Test on at least one platform before submitting

---

## Legal

This project is a fan tool and is not affiliated with or endorsed by Team Cherry.

Hollow Knight and all associated assets, characters, and music are property of **Team Cherry**.

This project itself is released under the [MIT License](LICENSE).
