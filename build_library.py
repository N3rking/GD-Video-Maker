"""Build the object library from your installed GD sprite sheets.

Usage: python build_library.py [--max 220]
Writes data/library.npz plus data/library_sheet.png for a visual check.
Only still sprites that can be tinted to any color are kept: pale, unsaturated art that doesn't animate.
"""
import argparse
import json
import plistlib
import re
import subprocess
import sys

import numpy as np

RES = r"C:\Program Files (x86)\Steam\steamapps\common\Geometry Dash\Resources"
# GJ_ParticleSheet and FireSheet_01 also hold mapped objects, but those animate, so a still frame can't rely on them.
SHEETS = ["GJ_GameSheet", "GJ_GameSheet02", "PixelSheet_01"]
SPRITE = 48  # stored sprite resolution
UHD_PER_UNIT = 4  # at UHD a 30-unit block is 120 pixels
# Gameplay objects and triggers would change how the level plays or be invisible during play.
SKIP = re.compile(r"edit|trigger|portal|ring|bump|pad|coin|speed|boost|checkpoint|sawblade|saw_|icon|player|key_|keyframe|collision|particle", re.I)
# Anything that moves by itself would break a still frame: spinning parts, animated art, music pulsing, proximity fades.
MOVING = re.compile(r"anim|wheel|cog|blade|spin|rotat|_ball|fireball|waterfall|waterSplash|lava|_zag_|shine|invis|smartBlock", re.I)
# Only simple solid art. In-game testing showed block, pit, triangle_b and similar families render dark parts a tint can't
# reach, and that decorative pieces with extra parts look wrong. Their sprite art doesn't reveal this, so these are opt-in.
SAFE = re.compile(r"^(lightsquare.*_color_|lighttriangle.*_color_|d_gradient|d_wave|d_square_0|d_largeSquare|d_brick|d_bar_|d_smallbar|d_circle|d_roundCloud|d_cloud|d_grass_|d_thorn|d_spikes|d_spikeWave|d_pit06wave|d_pillar)")
ALWAYS = {211}  # plain square, used as the guaranteed fallback shape


def read_sheet(name, res=None):
    path = f"{res or RES}\\{name}-uhd.png"
    with open(path, "rb") as f:  # PNG header holds the size, so only ffmpeg is needed
        head = f.read(24)
    w, h = int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", "rgba", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, 4)


def nums(s):
    return [int(float(v)) for v in re.findall(r"-?\d+\.?\d*", s)]


def cut_sprite(sheet, f):
    """Returns the sprite on its full original canvas, undoing rotation and trimming."""
    x, y, w, h = nums(f["textureRect"])
    if f.get("textureRotated"):
        img = np.rot90(sheet[y:y + w, x:x + h], 1)
    else:
        img = sheet[y:y + h, x:x + w]
    src_w, src_h = nums(f["spriteSourceSize"])
    off_x, off_y = nums(f["spriteOffset"])
    canvas = np.zeros((src_h, src_w, 4), np.uint8)
    left = max(0, int((src_w - w) / 2 + off_x))
    top = max(0, int((src_h - h) / 2 - off_y))
    part = img[:src_h - top, :src_w - left]
    canvas[top:top + part.shape[0], left:left + part.shape[1]] = part
    return canvas


def shrink(img, size=SPRITE):
    """Area-average a canvas down to size x size (premultiplied, so transparent edges don't darken)."""
    h, w = img.shape[:2]
    rgba = img.astype(np.float32) / 255
    a = rgba[..., 3]
    lum = rgba[..., :3].mean(-1) * a
    ys = (np.arange(h) * size // h).astype(int)
    xs = (np.arange(w) * size // w).astype(int)
    idx = ys[:, None] * size + xs[None, :]
    count = np.bincount(idx.ravel(), minlength=size * size).astype(np.float32)
    # A canvas smaller than the target repeats pixels instead of leaving holes.
    if (count == 0).any():
        ys = np.minimum(np.arange(size) * h // size, h - 1)
        xs = np.minimum(np.arange(size) * w // size, w - 1)
        al, lu = a[np.ix_(ys, xs)], lum[np.ix_(ys, xs)]
    else:
        al = (np.bincount(idx.ravel(), a.ravel(), size * size) / count).reshape(size, size)
        lu = (np.bincount(idx.ravel(), lum.ravel(), size * size) / count).reshape(size, size)
    return al.astype(np.float32), np.where(al > 1e-3, lu / np.maximum(al, 1e-3), 1).astype(np.float32)


def tintable(img):
    """Pale and unsaturated art turns into any color when tinted; dark or colored art can't."""
    a = img[..., 3] > 128
    if a.sum() < 16:
        return False
    rgb = img[..., :3][a].astype(np.float32) / 255
    sat = (rgb.max(1) - rgb.min(1)).mean()
    return rgb.mean() >= 0.35 and sat <= 0.08


def library_from_names(names, ids, res, out):
    """Rebuilds a library from a list of verified sprite names using the player's own GD install, so no art ships with the app."""
    frames, sheets = {}, {}
    for s in SHEETS:
        with open(f"{res}\\{s}-uhd.plist", "rb") as f:
            for name, info in plistlib.load(f)["frames"].items():
                frames[name] = (s, info)

    def art(name):
        s, info = frames[name]
        if s not in sheets:
            sheets[s] = read_sheet(s, res)
        return cut_sprite(sheets[s], info)

    alpha, lum, units, kept_ids, kept_names = [], [], [], [], []
    for name, object_id in zip(names, ids):
        if name not in frames:  # a different game version may have dropped it
            continue
        img = art(name)
        detail = name.replace("_001.png", "_color_001.png")
        if detail in frames:
            img = merge_layers(img, art(detail))
        al, lu = shrink(img)
        alpha.append(al)
        lum.append(lu)
        units.append((img.shape[1] / UHD_PER_UNIT, img.shape[0] / UHD_PER_UNIT))
        kept_ids.append(object_id)
        kept_names.append(name)
    if 211 not in kept_ids:
        raise SystemExit("The plain square (object 211) wasn't found in this GD install.")
    np.savez_compressed(out, alpha=np.stack(alpha), lum=np.stack(lum), ids=np.array(kept_ids), units=np.array(units, np.float32),
                        names=np.array(kept_names))
    return len(kept_ids)


def merge_layers(base, detail):
    """Both color layers get the same tint, so a two-layer object behaves like one sprite with both drawn."""
    h, w = max(base.shape[0], detail.shape[0]), max(base.shape[1], detail.shape[1])
    canvas = np.zeros((h, w, 4), np.float32)
    for layer in (base, detail):
        top, left = (h - layer.shape[0]) // 2, (w - layer.shape[1]) // 2
        src = layer.astype(np.float32)
        al = src[..., 3:] / 255
        area = canvas[top:top + layer.shape[0], left:left + layer.shape[1]]
        area[..., :3] = area[..., :3] * (1 - al) + src[..., :3] * al
        area[..., 3:] = area[..., 3:] * (1 - al) + 255 * al
    return canvas.astype(np.uint8)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=4000)
    ap.add_argument("--min-distance", type=float, default=0.03, help="how different two objects must look to both be kept")
    ap.add_argument("--out", default="data/library")
    ap.add_argument("--sheet", help="only keep objects whose art is all on this sheet, so layer order holds between them")
    a = ap.parse_args()

    frame_map = json.load(open("data/frame_map.json"))
    frames_all, sheets = {}, {}
    for s in SHEETS:
        with open(f"{RES}\\{s}-uhd.plist", "rb") as f:
            for name, info in plistlib.load(f)["frames"].items():
                frames_all[name] = (s, info)
    stats = dict(mapped=len(frame_map), found=0, skip_name=0, moving=0, two_layer=0, not_tintable=0)
    candidates = []
    for name, ids in frame_map.items():
        if name not in frames_all:
            continue
        stats["found"] += 1
        forced = bool(ALWAYS & set(ids))
        if not forced and not SAFE.match(name):
            stats["not_safe"] = stats.get("not_safe", 0) + 1
            continue
        if SKIP.search(name) and not forced:
            stats["skip_name"] += 1
            continue
        # Animations are stored as numbered frames, so a sprite with a _002 sibling (or that isn't _001 itself) moves.
        numbered = re.match(r"(.*)_(\d{3})\.png$", name)
        if not forced and (MOVING.search(name) or (numbered and (numbered.group(2) != "001" or f"{numbered.group(1)}_002.png" in frames_all))):
            stats["moving"] += 1
            continue
        sheet_name, info = frames_all[name]
        detail = name.replace("_001.png", "_color_001.png")
        if a.sheet and (sheet_name != a.sheet or (detail in frames_all and frames_all[detail][0] != a.sheet)):
            if forced:
                sys.exit(f"The base square is on {sheet_name}, so --sheet must be that sheet.")
            stats["other_sheet"] = stats.get("other_sheet", 0) + 1
            continue
        if sheet_name not in sheets:
            sheets[sheet_name] = read_sheet(sheet_name)
        img = cut_sprite(sheets[sheet_name], info)
        if detail in frames_all and not forced:
            detail_sheet, detail_info = frames_all[detail]
            if detail_sheet not in sheets:
                sheets[detail_sheet] = read_sheet(detail_sheet)
            img = merge_layers(img, cut_sprite(sheets[detail_sheet], detail_info))
            stats["two_layer"] += 1
        if not tintable(img) and not forced:
            stats["not_tintable"] += 1
            continue
        al, lu = shrink(img)
        candidates.append(dict(name=name, id=211 if forced else min(ids), w=img.shape[1] / UHD_PER_UNIT, h=img.shape[0] / UHD_PER_UNIT, a=al, l=lu, forced=forced))
    print(stats, "usable still objects:", len(candidates))

    # Greedy diversity pick: the fitter gains nothing from fifty nearly identical blobs.
    candidates.sort(key=lambda c: (not c["forced"], c["id"]))
    keep = []
    for c in candidates:
        if len(keep) >= a.max:
            break
        sig = c["a"] * c["l"]
        if c["forced"] or all(np.abs(sig - k["a"] * k["l"]).mean() > a.min_distance for k in keep):
            keep.append(c)
    print("kept", len(keep), "by distance; base square:", [k["name"] for k in keep if k["forced"]])

    np.savez_compressed(a.out + ".npz", alpha=np.stack([k["a"] for k in keep]), lum=np.stack([k["l"] for k in keep]),
                        ids=np.array([k["id"] for k in keep]), units=np.array([[k["w"], k["h"]] for k in keep], np.float32),
                        names=np.array([k["name"] for k in keep]))

    cols = 22
    rows = int(np.ceil(len(keep) / cols))
    grid = np.full((rows * SPRITE, cols * SPRITE, 3), 40, np.float32)
    for i, k in enumerate(keep):
        y, x = i // cols * SPRITE, i % cols * SPRITE
        grid[y:y + SPRITE, x:x + SPRITE] = 40 * (1 - k["a"][..., None]) + (k["a"] * k["l"] * 255)[..., None]
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{grid.shape[1]}x{grid.shape[0]}", "-i", "-",
                    "-frames:v", "1", a.out + "_sheet.png"], input=grid.astype(np.uint8).tobytes(), check=True)


if __name__ == "__main__":
    main()
