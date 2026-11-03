"""Rebuild video frames from real GD objects, choosing the object, placement and layer that fit best.

Usage: python fit_objects.py <video> [--max-objects 100] [--target-rmse 14] [--fps 30] [--out clip_objects]
Needs data/library.npz from build_library.py. Writes <out>.json, <out>_sheet.png and <out>_preview.mp4.
"""
import argparse
import json
import os
import time

import numpy as np

from fit_shapes import W, anmt_grid, read_frames, write_image, write_video

lib = np.load(os.environ.get("GD_LIBRARY", "data/library.npz"))
ALPHA, LUM, IDS, UNITS, NAMES = lib["alpha"], lib["lum"], lib["ids"], lib["units"], lib["names"]
N_SPRITES, S = len(IDS), ALPHA.shape[1]
SQUARE = int(np.where(IDS == 211)[0][0])
GX = np.arange(W, dtype=np.float32) + 0.5


def sample(g):
    """g = (sprite, cx, cy, w, h, deg). Returns the bbox plus sprite alpha and luminance across it."""
    sid, cx, cy, w, h, deg = g
    a = np.deg2rad(deg)
    c, s = np.cos(a), np.sin(a)
    rx, ry = (abs(w * c) + abs(h * s)) / 2, (abs(w * s) + abs(h * c)) / 2
    x0, x1 = max(0, int(cx - rx)), min(W, int(cx + rx) + 2)
    y0, y1 = max(0, int(cy - ry)), min(W, int(cy + ry) + 2)
    if x0 >= x1 or y0 >= y1:
        return None
    dx = GX[x0:x1][None, :] - cx
    dy = GX[y0:y1][:, None] - cy
    u = ((dx * c + dy * s) / w + 0.5) * S
    v = ((dy * c - dx * s) / h + 0.5) * S
    inside = (u >= 0) & (u < S) & (v >= 0) & (v < S)
    if not inside.any():
        return None
    iu, iv = np.clip(u.astype(np.int32), 0, S - 1), np.clip(v.astype(np.int32), 0, S - 1)
    al = ALPHA[sid][iv, iu] * inside
    return x0, x1, y0, y1, al, LUM[sid][iv, iu]


weight = None  # optional per-pixel importance (W x W); fit_sharp.py sets it so edges like text count more


def detail_weight(target, strength):
    """1 on flat areas, up to 1 + strength on edges, so small high-contrast detail is worth fitting."""
    gray = target.mean(-1)
    edge = np.zeros_like(gray)
    edge[:, 1:] += np.abs(np.diff(gray, axis=1))
    edge[1:, :] += np.abs(np.diff(gray, axis=0))
    for axis in (0, 1):  # spread edges a few pixels so the strokes' insides count too
        edge = sum(np.roll(edge, k, axis) for k in (-2, -1, 0, 1, 2)) / 5
    return 1 + strength * np.clip(edge / 25, 0, 1)


def gain(g, target, canvas):
    """Squared-error drop from painting g over canvas in its best tint, and that tint."""
    r = sample(g)
    if r is None:
        return -1.0, None
    x0, x1, y0, y1, al, lu = r
    t, c = target[y0:y1, x0:x1], canvas[y0:y1, x0:x1]
    a3, b3 = al[..., None], (al * lu)[..., None]
    w3 = 1.0 if weight is None else weight[y0:y1, x0:x1, None]
    denom = (w3 * b3 * b3).sum()
    if denom < 1e-3:
        return -1.0, None
    col = np.clip((w3 * b3 * (t - (1 - a3) * c)).sum((0, 1)) / denom, 0, 255)
    new = (1 - a3) * c + b3 * col
    return float((w3 * ((c - t) ** 2 - (new - t) ** 2)).sum()), col


def paint(g, col, canvas):
    r = sample(g)
    if r is not None:
        x0, x1, y0, y1, al, lu = r
        canvas[y0:y1, x0:x1] = (1 - al[..., None]) * canvas[y0:y1, x0:x1] + (al * lu)[..., None] * col


wins = np.zeros(N_SPRITES, np.float64)  # how often each object has been the best choice so far


def pick_sprite(rng, wins_cdf):
    # Half the tries explore the whole library; half favor objects that keep winning on this video.
    if wins_cdf[-1] == 0 or rng.random() < 0.5:
        return int(rng.integers(N_SPRITES))
    return int(np.searchsorted(wins_cdf, rng.random() * wins_cdf[-1], side="right"))


size_scale = 1.0  # fit_sharp.py lowers this as a frame fills in, so later objects add fine detail


def random_object(rng, error_cdf, wins_cdf):
    sid = pick_sprite(rng, wins_cdf)
    size = max(2.0, (W * 0.6 * rng.random() ** 2 + 3) * size_scale)
    # Stay near the object's natural shape so its look carries through; a stretched ring is just a smear.
    aspect = UNITS[sid][0] / UNITS[sid][1] * rng.uniform(0.7, 1.4)
    # Center candidates where the picture is currently most wrong instead of wasting tries on finished areas.
    pixel = int(np.searchsorted(error_cdf, rng.random() * error_cdf[-1]))
    cy, cx = divmod(min(pixel, W * W - 1), W)
    return (sid, cx + rng.uniform(-2, 3), cy + rng.uniform(-2, 3), size * np.sqrt(aspect), size / np.sqrt(aspect), rng.uniform(0, 360))


def mutate(g, rng):
    sid, cx, cy, w, h, d = g
    return (sid, cx + rng.normal(0, 2.5), cy + rng.normal(0, 2.5), max(2, w * rng.lognormal(0, 0.12)), max(2, h * rng.lognormal(0, 0.12)), (d + rng.normal(0, 12)) % 360)


def best_object(target, canvas, rng, tries, climbs):
    """Tries many objects, keeps the top few, then refines each one's placement."""
    scored = []
    err = ((target - canvas) ** 2).sum(-1)
    error_cdf = np.cumsum((err if weight is None else err * weight).ravel() + 1e-3)
    wins_cdf = np.cumsum(wins)
    for _ in range(tries):
        g = random_object(rng, error_cdf, wins_cdf)
        v, _c = gain(g, target, canvas)
        if v > 0:
            scored.append((v, g))
    scored.sort(key=lambda s: -s[0])
    best_v, best = -1.0, None
    for v, g in scored[:3]:
        for _ in range(climbs):
            m = mutate(g, rng)
            mv, _c = gain(m, target, canvas)
            if mv > v:
                v, g = mv, m
        if v > best_v:
            best_v, best = v, g
    if best is not None:
        wins[best[0]] += 1
    return best, best_v


def recolor(geoms, target):
    """Best tints for fixed objects, weighting each pixel by how visible the object is after later layers."""
    samples = [sample(g) for g in geoms]
    visible = np.ones((W, W), np.float32)
    weights = [None] * len(geoms)
    for i in range(len(geoms) - 1, -1, -1):
        if samples[i] is None:
            continue
        x0, x1, y0, y1, al, _lu = samples[i]
        weights[i] = visible[y0:y1, x0:x1] * al
        visible[y0:y1, x0:x1] *= 1 - al
    colors = np.zeros((len(geoms), 3), np.float32)
    visibility = np.zeros(len(geoms), np.float32)
    canvas = np.zeros((W, W, 3), np.float32)
    for i, r in enumerate(samples):
        if r is None:
            continue
        x0, x1, y0, y1, al, lu = r
        w3 = weights[i][..., None]
        visibility[i] = weights[i].sum()
        colors[i] = np.clip((w3 * target[y0:y1, x0:x1]).sum((0, 1)) / max(float((w3 * lu[..., None]).sum()), 1e-3), 0, 255)
        canvas[y0:y1, x0:x1] = (1 - al[..., None]) * canvas[y0:y1, x0:x1] + (al * lu)[..., None] * colors[i]
    return colors, canvas, visibility


def fit(frames, a):
    rng = np.random.default_rng(1)
    backdrop = (SQUARE, W / 2, W / 2, W * 1.02, W * 1.02, 0.0)  # bottom layer: always covers the whole picture
    out, renders, geoms = [], [], None
    counts = []
    for idx, target in enumerate(frames):
        keep = None
        if geoms is not None:
            _c, canvas, visibility = recolor(geoms, target)
            if np.sqrt(((canvas - target) ** 2).mean()) < a.cut_rmse:
                # Reusing last frame's objects keeps them still, which stops frame-to-frame flicker.
                alive = [i for i in np.argsort(-visibility) if i == 0 or visibility[i] > 1.0]
                alive = sorted(alive[:max(1, len(alive) - a.replace)])
                keep = [geoms[i] for i in alive]
        geoms = keep if keep is not None else [backdrop]
        _c, canvas, _v = recolor(geoms, target)
        canvas = canvas.copy()
        # Dark or flat frames score low error while looking empty, so the goal scales with the frame's contrast.
        goal = min(a.target_rmse, max(2.0, a.target_contrast * float(target.std())))
        while len(geoms) < a.max_objects and np.sqrt(((canvas - target) ** 2).mean()) > goal:
            g, v = best_object(target, canvas, rng, a.tries, a.climbs)
            if g is None or v < a.min_gain:
                break
            _v, col = gain(g, target, canvas)
            paint(g, col, canvas)
            geoms.append(g)
        colors, canvas, _v = recolor(geoms, target)
        out.append([[int(g[0]), *map(float, g[1:]), *map(float, c)] for g, c in zip(geoms, colors)])
        renders.append(canvas)
        counts.append(len(geoms))
        if idx % 15 == 0:
            print(f"frame {idx + 1}/{len(frames)} objects {len(geoms)} rmse {np.sqrt(((canvas - target) ** 2).mean()):.1f}", flush=True)
    return out, np.stack(renders), counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--max-objects", type=int, default=100)
    ap.add_argument("--target-rmse", type=float, default=14, help="stop adding objects once a frame is this accurate; lower is better but uses more")
    ap.add_argument("--target-contrast", type=float, default=0.25, help="per-frame goal as a fraction of that frame's contrast")
    ap.add_argument("--replace", type=int, default=30, help="objects swapped per frame; higher follows motion, lower flickers less")
    ap.add_argument("--fps", type=float, default=30)
    ap.add_argument("--cut-rmse", type=float, default=38)
    ap.add_argument("--tries", type=int, default=600, help="candidates per object; lower is faster but less accurate")
    ap.add_argument("--climbs", type=int, default=60)
    ap.add_argument("--min-gain", type=float, default=200)
    ap.add_argument("--out", default="clip_objects")
    a = ap.parse_args()

    frames = read_frames(a.video, a.fps)
    t = time.time()
    shapes, renders, counts = fit(frames, a)
    grid = anmt_grid(frames)
    rmse = lambda x: float(np.sqrt(((x - frames) ** 2).mean()))
    flicker = lambda x: float(np.abs(np.diff(x, axis=0)).mean())
    used = np.bincount([s[0] for f in shapes for s in f], minlength=N_SPRITES)
    print(f"{len(frames)} frames in {time.time() - t:.0f}s")
    print(f"objects: {sum(counts)} total, {np.mean(counts):.0f} per frame on average, {min(counts)}-{max(counts)} range")
    print(f"error (RMSE): objects {rmse(renders):.1f} | 31x31 grid {rmse(grid):.1f}")
    print(f"frame-to-frame change: original {flicker(frames):.1f} | objects {flicker(renders):.1f} | grid {flicker(grid):.1f}")
    print(f"different object types used: {(used > 0).sum()} of {N_SPRITES}; top:", [str(NAMES[i]) for i in np.argsort(-used)[:12]])

    with open(a.out + ".json", "w") as f:
        json.dump({"size": W, "fps": a.fps, "ids": IDS.tolist(), "units": UNITS.tolist(), "frames": shapes}, f)
    up = lambda x: np.kron(x, np.ones((3, 3, 1), np.float32))
    picks = np.linspace(0, len(frames) - 1, 6).astype(int)
    write_image(np.vstack([np.hstack([up(s[i]) for i in picks]) for s in (frames, renders, grid)]), a.out + "_sheet.png")
    write_video([np.hstack([up(frames[i]), up(renders[i]), up(grid[i])]) for i in range(len(frames))], a.fps, a.out + "_preview.mp4")


if __name__ == "__main__":
    main()
