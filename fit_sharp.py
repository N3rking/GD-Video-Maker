"""Sharp mode: high-resolution fitting where objects stay on across frames, so only changes cost new objects.

Usage: python fit_sharp.py <video> [--size 288] [--first 3000] [--per-frame 300] [--target-rmse 4] [--detail 4] [--fps 30] [--out clip_sharp]
A "keyframe" draws the whole picture fresh. Frames after it only paint new objects on top of what's already showing.
Writes <out>.json (for build_level.py), <out>_sheet.png and <out>_preview.mp4.
"""
import argparse
import json
import multiprocessing
import os
import time


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--size", type=int, default=288, help="fitting resolution")
    ap.add_argument("--first", type=int, default=3000, help="most objects in a keyframe")
    ap.add_argument("--per-frame", type=int, default=300, help="most new objects in any other frame")
    ap.add_argument("--target-rmse", type=float, default=4, help="stop adding once a frame is this accurate; lower is sharper but uses more")
    ap.add_argument("--detail", type=float, default=4, help="how much more edges like text count than flat areas (0 = all the same)")
    ap.add_argument("--shades", type=int, default=0, help="colors allowed per scene; fewer means flatter areas and fewer objects (0 = unlimited)")
    ap.add_argument("--fps", type=float, default=30)
    ap.add_argument("--max-showing", type=int, default=6000, help="redraw fresh once this many objects are showing, to limit lag")
    ap.add_argument("--cut-rmse", type=float, default=30, help="a change this big between frames counts as a scene cut")
    ap.add_argument("--tries", type=int, default=120)
    ap.add_argument("--climbs", type=int, default=15)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--out", default="clip_sharp")
    return ap.parse_args()


# The size has to be set before fit_shapes and fit_objects are imported, including in worker processes.
if __name__ == "__main__":
    os.environ["GD_FIT_SIZE"] = str(parse_args().size)

import numpy as np  # noqa: E402

import fit_objects as fo  # noqa: E402
from fit_shapes import W, anmt_grid, read_frames, write_image, write_video  # noqa: E402


def make_palette(frames, shades, rng):
    """K-means over a scene's pixels: similar colors collapse into one shade, so big flat objects can cover them."""
    pixels = frames.reshape(-1, 3)
    pixels = pixels[rng.choice(len(pixels), min(len(pixels), 20000), replace=False)]
    palette = pixels[rng.choice(len(pixels), shades, replace=False)].copy()
    for _ in range(12):
        nearest = ((pixels[:, None] - palette[None]) ** 2).sum(-1).argmin(1)
        for k in range(shades):
            members = pixels[nearest == k]
            if len(members):
                palette[k] = members.mean(0)
    return palette


def snap(colors, palette):
    shape = colors.shape
    nearest = ((colors.reshape(-1, 1, 3) - palette[None]) ** 2).sum(-1).argmin(1)
    return palette[nearest].reshape(shape)


PROGRESS = os.environ.get("GD_PROGRESS_DIR")  # set by gd_video_maker.py so its window can show live progress
_last_report = [0.0]


def report(start, frame, placed, limit, err, goal, target, canvas, done, force=False):
    """Writes this chunk's current frame, object count and a target|canvas picture for the window, at most twice a second."""
    now = time.time()
    if not PROGRESS or (not force and now - _last_report[0] < 0.5):
        return
    _last_report[0] = now
    path = os.path.join(PROGRESS, str(start))
    try:
        with open(path + ".json.tmp", "w") as f:
            json.dump(dict(frame=frame, placed=placed, limit=limit, err=err, goal=goal, done=done, t=now), f)
        os.replace(path + ".json.tmp", path + ".json")
        img = np.clip(np.hstack([target, canvas]), 0, 255).astype(np.uint8)
        with open(path + ".ppm.tmp", "wb") as f:
            f.write(b"P6 %d %d 255\n" % (img.shape[1], img.shape[0]) + img.tobytes())
        os.replace(path + ".ppm.tmp", path + ".ppm")
    except OSError:  # the window may be reading the file right now; the next report replaces it
        pass


def add_objects(target, canvas, limit, goal, rng, a, scale_for, palette=None, progress=None):
    """Paints the best objects onto canvas until the frame reaches the goal or the limit."""
    placed = []
    while len(placed) < limit:
        err = np.sqrt(((canvas - target) ** 2).mean())
        if err <= goal:
            break
        if progress:
            progress(len(placed), limit, float(err))
        fo.size_scale = scale_for(len(placed))
        g, v = fo.best_object(target, canvas, rng, a.tries, a.climbs)
        if g is None or v < 50:
            break
        _v, col = fo.gain(g, target, canvas)
        if palette is not None:
            col = snap(col, palette)
        fo.paint(g, col, canvas)
        placed.append([int(g[0]), *map(float, g[1:]), *map(float, col)])
    return placed


def drop_hidden(new):
    """Removes objects that the same frame's later objects completely cover, since nobody can ever see them."""
    covered = np.zeros((W, W), bool)
    kept = []
    for row in reversed(new):
        r = fo.sample(tuple(row[:6]))
        if r is None:
            continue
        x0, x1, y0, y1, al, _lu = r
        if (al[~covered[y0:y1, x0:x1]] > 0.01).any():
            kept.append(row)
        covered[y0:y1, x0:x1] |= al > 0.99
    return kept[::-1]


def fit_chunk(job):
    """Fits one run of frames that starts on a keyframe. Chunks are independent, so each can use its own process."""
    start, frames, a = job
    rng = np.random.default_rng(start + 1)
    backdrop = (fo.SQUARE, W / 2, W / 2, W * 1.02, W * 1.02, 0.0)
    out, renders = [], []
    canvas = None
    showing = 0
    palette = make_palette(frames, a.shades, rng) if a.shades else None
    for i, target in enumerate(frames):
        # Dark or flat frames score low error while looking empty, so the goal scales with the frame's contrast.
        goal = min(a.target_rmse, max(2.0, 0.2 * float(target.std())))
        if palette is not None:
            target = snap(target, palette)
        fo.weight = fo.detail_weight(target, a.detail) if a.detail else None
        fresh = canvas is None or showing > a.max_showing
        progress = lambda n, limit, err: report(start, start + i, n, limit, err, goal, target, canvas, i)  # noqa: E731
        if fresh:
            canvas = np.zeros((W, W, 3), np.float32)
            _v, col = fo.gain(backdrop, target, canvas)
            if palette is not None:
                col = snap(col, palette)
            fo.paint(backdrop, col, canvas)
            new = [[int(backdrop[0]), *map(float, backdrop[1:]), *map(float, col)]]
            # Big shapes first, then finer and finer detail.
            new += add_objects(target, canvas, a.first - 1, goal, rng, a, lambda n: max(0.06, (1 - n / (a.first * 4)) ** 3), palette, progress)
            new = drop_hidden(new)
            showing = len(new)
        else:
            # Only what changed needs painting, and changes tend to be small.
            new = add_objects(target, canvas, a.per_frame, goal, rng, a, lambda n: float(rng.uniform(0.06, 0.45)), palette, progress)
            new = drop_hidden(new)
            showing += len(new)
        out.append({"reset": bool(fresh), "new": new})
        renders.append(np.clip(canvas, 0, 255).astype(np.uint8).copy())
        report(start, start + i, len(new), len(new), float(np.sqrt(((canvas - target) ** 2).mean())), goal, target, canvas, i + 1, force=True)
    return start, out, np.stack(renders)


def split_chunks(frames, a):
    """Starts a new chunk at each scene cut, and every 45 frames so the work spreads across processes."""
    starts = [0]
    for i in range(1, len(frames)):
        cut = np.sqrt(((frames[i] - frames[i - 1]) ** 2).mean()) > a.cut_rmse
        if cut or i - starts[-1] >= 45:
            starts.append(i)
    return [(s, frames[s:e], a) for s, e in zip(starts, starts[1:] + [len(frames)])]


def main():
    a = parse_args()
    frames = read_frames(a.video, a.fps)
    jobs = split_chunks(frames, a)
    print(f"fitting {len(frames)} frames in {len(jobs)} chunks", flush=True)
    t = time.time()
    results = []
    with multiprocessing.Pool(a.workers) as pool:
        for r in pool.imap_unordered(fit_chunk, jobs):
            results.append(r)
            print(f"chunk {len(results)}/{len(jobs)} done ({time.time() - t:.0f}s)", flush=True)
    results.sort(key=lambda r: r[0])
    out = [f for r in results for f in r[1]]
    renders = np.concatenate([r[2] for r in results]).astype(np.float32)

    total = sum(len(f["new"]) for f in out)
    keyframes = sum(f["reset"] for f in out)
    grid = anmt_grid(frames, 95)
    rmse = lambda x: float(np.sqrt(((x - frames) ** 2).mean()))
    flicker = lambda x: float(np.abs(np.diff(x, axis=0)).mean())
    print(f"{len(frames)} frames in {time.time() - t:.0f}s, {keyframes} keyframes")
    print(f"objects: {total} total, {total / len(frames):.0f} per frame on average")
    print(f"error (RMSE): sharp {rmse(renders):.1f} | 95x95 grid {rmse(grid):.1f}")
    print(f"frame-to-frame change: original {flicker(frames):.1f} | sharp {flicker(renders):.1f} | grid {flicker(grid):.1f}")

    with open(a.out + ".json", "w") as f:
        json.dump({"format": "persistent", "size": W, "fps": a.fps, "ids": fo.IDS.tolist(), "units": fo.UNITS.tolist(), "frames": out}, f)
    picks = np.linspace(0, len(frames) - 1, 5).astype(int)
    write_image(np.vstack([np.hstack([s[i] for i in picks]) for s in (frames, renders, grid)]), a.out + "_sheet.png")
    write_video([np.hstack([frames[i], renders[i], grid[i]]) for i in range(len(frames))], a.fps, a.out + "_preview.mp4")


if __name__ == "__main__":
    main()
