"""Rebuild video frames from a limited number of rotated rectangles.

Usage: python fit_shapes.py <video> [--shapes 100] [--replace 20] [--fps 30] [--out clip_shapes]
Writes <out>.json (for build_level.py), <out>_sheet.png and <out>_preview.mp4.
"""
import argparse
import json
import os
import subprocess
import time

import numpy as np

W = int(os.environ.get("GD_FIT_SIZE", 96))  # fitting resolution; higher allows finer detail but runs slower


def read_frames(path, fps):
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-vf", f"fps={fps},scale={W}:{W}:flags=area",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, W, W, 3).astype(np.float32)


def write_image(img, path):
    h, w = img.shape[:2]
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                    "-i", "-", "-frames:v", "1", path], input=img.astype(np.uint8).tobytes(), check=True)


def write_video(frames, fps, path):
    h, w = frames[0].shape[:2]
    data = b"".join(f.astype(np.uint8).tobytes() for f in frames)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps),
                    "-i", "-", "-pix_fmt", "yuv420p", path], input=data, check=True)


def region(g):
    """Bounding box of shape g=(cx,cy,w,h,deg) plus its boolean mask inside that box."""
    cx, cy, w, h, deg = g
    a = np.deg2rad(deg)
    c, s = np.cos(a), np.sin(a)
    rx = (abs(w * c) + abs(h * s)) / 2
    ry = (abs(w * s) + abs(h * c)) / 2
    x0, x1 = max(0, int(cx - rx)), min(W, int(np.ceil(cx + rx)) + 1)
    y0, y1 = max(0, int(cy - ry)), min(W, int(np.ceil(cy + ry)) + 1)
    if x0 >= x1 or y0 >= y1:
        return 0, 0, 0, 0, None
    dx = np.arange(x0, x1, dtype=np.float32)[None, :] + 0.5 - cx
    dy = np.arange(y0, y1, dtype=np.float32)[:, None] + 0.5 - cy
    mask = (np.abs(dx * c + dy * s) <= w / 2) & (np.abs(dy * c - dx * s) <= h / 2)
    return x0, x1, y0, y1, mask


def gain(g, target, canvas):
    """Squared-error drop from painting g on top of canvas in its best flat color."""
    x0, x1, y0, y1, m = region(g)
    if m is None or not m.any():
        return -1.0, None
    t = target[y0:y1, x0:x1][m]
    col = t.mean(0)
    before = ((canvas[y0:y1, x0:x1][m] - t) ** 2).sum()
    return float(before - ((col - t) ** 2).sum()), col


def random_shape(rng):
    size = W * 0.5 * rng.random() ** 2 + 2
    return (rng.uniform(0, W), rng.uniform(0, W), size * rng.uniform(0.3, 1.5), size * rng.uniform(0.3, 1.5), rng.uniform(0, 180))


def mutate(g, rng):
    cx, cy, w, h, d = g
    return (cx + rng.normal(0, 2.5), cy + rng.normal(0, 2.5), max(1.5, w + rng.normal(0, 2.5)),
            max(1.5, h + rng.normal(0, 2.5)), (d + rng.normal(0, 12)) % 180)


def best_shape(target, canvas, rng, tries=120, climbs=80):
    best, best_gain = None, -1.0
    for _ in range(tries):
        g = random_shape(rng)
        v, _c = gain(g, target, canvas)
        if v > best_gain:
            best, best_gain = g, v
    for _ in range(climbs):
        g = mutate(best, rng)
        v, _c = gain(g, target, canvas)
        if v > best_gain:
            best, best_gain = g, v
    return best


def recolor(geoms, target):
    """Exact best colors for fixed geometry: each shape takes the mean of the pixels where it ends up on top."""
    top = np.zeros((W, W), np.int32)
    for i, g in enumerate(geoms):
        x0, x1, y0, y1, m = region(g)
        if m is not None:
            top[y0:y1, x0:x1][m] = i
    counts = np.bincount(top.ravel(), minlength=len(geoms))
    colors = np.zeros((len(geoms), 3), np.float32)
    for ch in range(3):
        colors[:, ch] = np.bincount(top.ravel(), target[..., ch].ravel(), minlength=len(geoms)) / np.maximum(counts, 1)
    return colors, colors[top], counts


def fit(frames, n_shapes, n_replace, cut_rmse, seed=1):
    rng = np.random.default_rng(seed)
    full = (W / 2, W / 2, W * 1.01, W * 1.01, 0.0)  # shape 0 is always the background
    out, renders, geoms = [], [], None
    for idx, target in enumerate(frames):
        keep = None
        if geoms is not None:
            _, canvas, counts = recolor(geoms, target)
            if np.sqrt(((canvas - target) ** 2).mean()) < cut_rmse:
                # Reusing last frame's geometry keeps shapes still, which stops frame-to-frame flicker.
                drop = set(np.argsort(counts[1:])[:n_replace] + 1)
                keep = [g for i, g in enumerate(geoms) if i not in drop]
        geoms = keep if keep is not None else [full]
        _, canvas, _ = recolor(geoms, target)
        canvas = canvas.copy()
        while len(geoms) < n_shapes:
            g = best_shape(target, canvas, rng)
            _v, col = gain(g, target, canvas)
            if col is not None:
                x0, x1, y0, y1, m = region(g)
                canvas[y0:y1, x0:x1][m] = col
            geoms.append(g)
        colors, canvas, _ = recolor(geoms, target)
        out.append([[*map(float, g), *map(float, c)] for g, c in zip(geoms, colors)])
        renders.append(canvas)
        if idx % 15 == 0:
            print(f"frame {idx + 1}/{len(frames)} rmse {np.sqrt(((canvas - target) ** 2).mean()):.1f}", flush=True)
    return out, np.stack(renders)


def anmt_grid(frames, n=31):
    """What ANMT's pixel grid shows, for comparison."""
    res = []
    edges = np.linspace(0, W, n + 1).astype(int)
    for f in frames:
        out = np.zeros_like(f)
        for i in range(n):
            for j in range(n):
                out[edges[i]:edges[i + 1], edges[j]:edges[j + 1]] = f[edges[i]:edges[i + 1], edges[j]:edges[j + 1]].mean((0, 1))
        res.append(out)
    return np.stack(res)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--shapes", type=int, default=100)
    ap.add_argument("--replace", type=int, default=20)
    ap.add_argument("--fps", type=float, default=30)
    ap.add_argument("--cut-rmse", type=float, default=38)
    ap.add_argument("--out", default="clip_shapes")
    a = ap.parse_args()

    assert a.replace < a.shapes, "--replace must be smaller than --shapes"
    frames = read_frames(a.video, a.fps)
    t = time.time()
    shapes, renders = fit(frames, a.shapes, a.replace, a.cut_rmse)
    grid = anmt_grid(frames)
    rmse = lambda x: float(np.sqrt(((x - frames) ** 2).mean()))
    flicker = lambda x: float(np.abs(np.diff(x, axis=0)).mean())
    print(f"{len(frames)} frames in {time.time() - t:.0f}s")
    print(f"error (RMSE): shapes {rmse(renders):.1f} | 31x31 grid {rmse(grid):.1f}")
    print(f"frame-to-frame change: original {flicker(frames):.1f} | shapes {flicker(renders):.1f} | grid {flicker(grid):.1f}")

    with open(a.out + ".json", "w") as f:
        json.dump({"size": W, "fps": a.fps, "frames": shapes}, f)

    up = lambda x: np.kron(x, np.ones((3, 3, 1), np.float32))
    picks = np.linspace(0, len(frames) - 1, 6).astype(int)
    sheet = np.vstack([np.hstack([up(s[i]) for i in picks]) for s in (frames, renders, grid)])
    write_image(sheet, a.out + "_sheet.png")
    write_video([np.hstack([up(frames[i]), up(renders[i]), up(grid[i])]) for i in range(len(frames))], a.fps, a.out + "_preview.mp4")


if __name__ == "__main__":
    main()
