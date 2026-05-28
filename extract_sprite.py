#!/usr/bin/env python3
"""
extract_sprite.py-  extract Hornet sprites from atlas1.
Usage:
    python extract_sprite.py           # extract (skips if files exist)
    python extract_sprite.py --force   # re-extract
    python extract_sprite.py --debug   # save debug_blobs.png
"""
import argparse
import os
import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import label

ATLAS       = "assets/atlas/atlas1 #34863.png"
PAD         = 8
CHROMA_KEY  = (0, 0, 255)   # bright blue-  absent from all Hornet sprites
ALPHA_THRESH = 100           # pixels below this alpha become chroma background

# Reference scale derived from idle sprite (blob 50) so all sprites are
# physically proportional to each other.
# blob 50 crop = 200x235 (with pad) → scale to 150px tall → factor = 0.6383
REF_H    = 235   # idle crop height (with padding)
TARGET_H = 150   # idle display height
REF_SCALE = TARGET_H / REF_H   # ≈ 0.638-  applied to all sprites

# ── Sprite table ─────────────────────────────────────────────────────────────
# (blob_index, output_file, rotation_CCW_deg, flip_h)
# Rotation applied BEFORE scaling.  All heights derived from REF_SCALE so
# Hornet appears the same physical size in every sprite.
SPRITES = [
    (50,  "assets/sprites/idle/hornet_idle.png",            0,  False),
    (383, "assets/sprites/fast_fall/hornet_fast_fall_wrong.png", 0,  False),   # horizontal already
    (98,  "assets/sprites/fast_fall/hornet_fast_fall.png",      90,  False),   # horizontal rush → CCW → dive
]

# Sitting animation-  blobs in row-by-row atlas order.
# All 219×96 in atlas; after CCW-90° rotation → 96×219 → scale by REF_SCALE.
SIT_BLOBS    = [213, 215, 220, 224, 225, 226, 231]
SIT_ROTATION = 90    # CCW
SIT_PREFIX   = "assets/sprites/sit/hornet_sit_{}.png"


# ── Helpers ───────────────────────────────────────────────────────────────────

def bake_chroma(img: Image.Image) -> Image.Image:
    """Snap semi-transparent edge pixels: alpha < ALPHA_THRESH → opaque chroma blue,
    alpha >= ALPHA_THRESH → fully opaque.  Eliminates dark-fringe artifacts."""
    arr = np.array(img.convert("RGBA"))
    bg = arr[:,:,3] < ALPHA_THRESH
    arr[bg]  = [*CHROMA_KEY, 255]   # opaque blue background
    arr[~bg, 3] = 255               # fully opaque sprite pixel
    return Image.fromarray(arr, "RGBA")


def scale_ref(img: Image.Image) -> Image.Image:
    """Scale image by REF_SCALE using nearest-neighbour (keeps pixel art crisp)."""
    w, h = img.size
    new_w = max(1, round(w * REF_SCALE))
    new_h = max(1, round(h * REF_SCALE))
    return img.resize((new_w, new_h), Image.NEAREST)


def build_blobs(arr: np.ndarray) -> list:
    """Return the filtered blob list that matches debug_blobs.png numbering."""
    mask = (arr[:,:,3] > 30).astype(np.int32)
    labeled, n = label(mask, structure=np.ones((3,3), int))
    blobs = []
    for bid in range(1, n+1):
        ys, xs = np.where(labeled == bid)
        if len(ys) == 0:
            continue
        y0,y1 = int(ys.min()), int(ys.max())
        x0,x1 = int(xs.min()), int(xs.max())
        h = y1-y0+1;  w = x1-x0+1
        if h < 40 or h > 300: continue
        if w < 20 or w > 400: continue
        if w/h < 0.3 or w/h > 3.0: continue
        blobs.append({"bbox":(x0,y0,x1,y1), "w":w, "h":h, "area":len(ys)})
    return blobs


def extract_one(img: Image.Image, blob: dict, rotation: int, flip_h: bool) -> Image.Image:
    x0,y0,x1,y1 = blob["bbox"]
    iw,ih = img.size
    crop = img.crop((max(0,x0-PAD), max(0,y0-PAD),
                     min(iw,x1+PAD), min(ih,y1+PAD)))
    if rotation:
        crop = crop.rotate(rotation, expand=True)
    if flip_h:
        crop = crop.transpose(Image.FLIP_LEFT_RIGHT)
    crop = bake_chroma(crop)
    crop = scale_ref(crop)
    return crop


def save_debug(img: Image.Image, blobs: list, out: str):
    overlay = Image.new("RGBA", img.size, (0,0,0,0))
    draw = ImageDraw.Draw(overlay)
    COLS = [(255,0,0),(0,200,0),(0,80,255),(255,200,0),(255,0,255),(0,220,220)]
    for i,b in enumerate(blobs):
        x0,y0,x1,y1 = b["bbox"]
        c = COLS[i%len(COLS)]
        draw.rectangle([x0,y0,x1,y1], outline=c+(255,), width=2)
        draw.text((x0, max(0,y0-1)), str(i), fill=(255,220,0,255))
    Image.alpha_composite(img.convert("RGBA"), overlay).save(out)
    print(f"  Debug saved: {out}")


# ── Main ──────────────────────────────────────────────────────────────────────

def all_exist():
    files = [s[1] for s in SPRITES] + [SIT_PREFIX.format(i) for i in range(len(SIT_BLOBS))]
    return all(os.path.exists(f) for f in files)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    if not args.force and all_exist():
        print("All sprite files exist. Use --force to re-extract.")
        return

    if not os.path.exists(ATLAS):
        print(f"ERROR: {ATLAS} not found.")
        return

    print(f"Loading {ATLAS} …")
    img  = Image.open(ATLAS).convert("RGBA")
    arr  = np.array(img)
    iw,ih = img.size

    print("Building blob list …")
    blobs = build_blobs(arr)
    print(f"  {len(blobs)} qualifying blobs.")

    if args.debug:
        save_debug(img, blobs, "assets/atlas/debug_blobs_1.png")

    # ── Fall / idle sprites ───────────────────────────────────────────────────
    for blob_idx, out_file, rot, flip in SPRITES:
        if not args.force and os.path.exists(out_file):
            print(f"  {out_file} already exists, skipping.")
            continue
        if blob_idx >= len(blobs):
            print(f"  ERROR: blob {blob_idx} out of range (max {len(blobs)-1})")
            continue
        sprite = extract_one(img, blobs[blob_idx], rot, flip)
        sprite.save(out_file)
        x0,y0,x1,y1 = blobs[blob_idx]["bbox"]
        print(f"  {out_file}: {sprite.size}  blob {blob_idx} @ ({x0},{y0}) rot={rot}°")

    # ── Sitting animation ─────────────────────────────────────────────────────
    print(f"\nExtracting {len(SIT_BLOBS)} sitting frames …")
    for frame_num, blob_idx in enumerate(SIT_BLOBS):
        out_file = SIT_PREFIX.format(frame_num)
        if not args.force and os.path.exists(out_file):
            print(f"  {out_file} already exists, skipping.")
            continue
        if blob_idx >= len(blobs):
            print(f"  ERROR: blob {blob_idx} out of range, skipping frame {frame_num}")
            continue
        sprite = extract_one(img, blobs[blob_idx], SIT_ROTATION, False)
        sprite.save(out_file)
        x0,y0,x1,y1 = blobs[blob_idx]["bbox"]
        print(f"  {out_file}: {sprite.size}  blob {blob_idx} @ ({x0},{y0})")

    print("\nDone.")


if __name__ == "__main__":
    main()
