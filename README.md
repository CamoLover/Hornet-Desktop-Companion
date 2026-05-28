# Hornet Desktop Companion

A physics-based desktop companion featuring Hollow Knight's Hornet. She sits on your desktop, falls with gravity, bounces, and reacts to how fast she's moving with different sprites.

## Quick start

```bash
chmod +x run.sh
./run.sh
```

Windows:
```
run.bat
```

Pass `--force` to re-extract sprites:
```bash
./run.sh --force
```

## Controls

| Action | Input |
|--------|-------|
| Drag Hornet | Left-click and drag |
| Throw her | Drag then release with momentum |
| Quit | ESC |

## Sprite states

| State | Trigger | Sprite |
|-------|---------|--------|
| Idle | Moving slowly or on the ground | Standing with needle |
| Slow fall | Falling gently (vy 200–550) | Drifting sideways |
| Fast fall | Dropping fast, mostly vertical | Diving pose |
| Fast fall (wrong) | High speed with large horizontal component | Tumbling / on back |

## Files

| File | Purpose |
|------|---------|
| `extract_sprite.py` | Extracts 4 Hornet sprites from atlas1 |
| `companion.py` | The companion app |
| `hornet_idle.png` | Standing idle (generated) |
| `hornet_slow_fall.png` | Gentle fall (generated) |
| `hornet_fast_fall.png` | Fast controlled dive (generated) |
| `hornet_fast_fall_wrong.png` | Tumbling fall (generated) |

## Tuning

**Sprite rotations** — if a fall sprite looks wrong, edit the `SPRITES` list in `extract_sprite.py`. Each entry has `(blob_idx, filename, rotation_degrees_CCW, flip_h)`. Run `python extract_sprite.py --force` to re-extract.

**Physics thresholds** — edit `SLOW_FALL_VY`, `FAST_FALL_VY`, `WRONG_FALL_MIX` at the top of `companion.py`.

**Gravity / bounce** — edit the class constants `GRAVITY`, `BOUNCE_DAMPING`, `FRICTION` in `Hornet`.

## Platform notes

### Linux
- Requires a compositor (picom, compton, kwin, mutter) for true black-background transparency.
- Always-on-top is set via `xprop` / `wmctrl`. If it doesn't stick: `wmctrl -r "Hornet" -b add,above`
- On Wayland: `SDL_VIDEODRIVER=x11 python companion.py`

### Windows
- Full transparency and always-on-top work via Win32 layered window API.
- Click-through is toggled automatically: enabled when not hovering over Hornet.
- `run.bat` uses `pythonw` to suppress the console window.

### macOS
- pygame transparency is unreliable on macOS. The window will function but may show a black background.
- For best results run on Windows or Linux first.

## Requirements

- Python 3.9+
- pygame >= 2.5
- Pillow >= 10
- scipy >= 1.11
- numpy >= 1.24
