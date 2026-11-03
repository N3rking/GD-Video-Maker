"""Measure in the real game which GD objects are usable, instead of guessing from names and art.

1. python object_audit.py make                      writes audit_level.json and data/audit.npz
   python build_level.py audit_level.json --level "OBJECT AUDIT"
2. python object_audit.py record [--seconds 75]     open the level in GD first, then press play during the countdown
3. python object_audit.py analyze audit_capture.mp4 writes data/library.npz from the objects that pass, plus data/audit_report.png

An object passes only if it stays still, takes the tint and looks like its sprite.
"""
import argparse
import json
import plistlib
import subprocess
import time

import numpy as np

from build_level import BASE, BG, GROUND, color_trigger, hsv_string, obj, rect, spawn, toggle, TOGGLE_TRIGGER
from build_library import MOVING, RES, SKIP, UHD_PER_UNIT, cut_sprite, merge_layers, read_sheet, shrink, tintable

SHEETS = ["GJ_GameSheet", "GJ_GameSheet02", "PixelSheet_01"]
COLS, ROWS, CELL = 20, 17, 12.0  # the area right of the player that's on screen on any window shape
LEFT, BOTTOM = 40.0, 10.0
SHOW, GAP = 3.0, 0.5  # seconds each page shows, then blank
CYAN = np.array([0, 220, 255], np.float32)
BACKGROUND = np.array([60, 60, 60], np.float32)  # grey, so black art can't hide
RED = (255, 40, 40)


def candidates():
    """Every mapped sprite that could work. Saturated art can't be tinted, and gameplay objects would change the level."""
    frame_map = json.load(open("data/frame_map.json"))
    frames_all, sheets = {}, {}
    for s in SHEETS:
        with open(f"{RES}\\{s}-uhd.plist", "rb") as f:
            for name, info in plistlib.load(f)["frames"].items():
                frames_all[name] = (s, info)

    def art(name):
        s, info = frames_all[name]
        if s not in sheets:
            sheets[s] = read_sheet(s)
        return cut_sprite(sheets[s], info)

    out, seen = [], set()
    for name, ids in frame_map.items():
        if name not in frames_all or (SKIP.search(name) or MOVING.search(name)) and 211 not in ids:
            continue
        img = art(name)
        sheet_names = {frames_all[name][0]}
        detail = name.replace("_001.png", "_color_001.png")
        if detail in frames_all:
            img = merge_layers(img, art(detail))
            sheet_names.add(frames_all[detail][0])
        if not tintable(img) and 211 not in ids:
            continue
        object_id = 211 if 211 in ids else min(ids)
        if object_id in seen:
            continue
        seen.add(object_id)
        al, lu = shrink(img)
        out.append(dict(name=name, id=object_id, sheet=sheet_names.pop() if len(sheet_names) == 1 else "mixed",
                        w=img.shape[1] / UHD_PER_UNIT, h=img.shape[0] / UHD_PER_UNIT, a=al, l=lu))
    return out


def cell_center(row, col):
    return LEFT + (col + 0.5) * CELL, BOTTOM + (ROWS - row - 0.5) * CELL


def make(_args):
    items = candidates()
    per_page = COLS * ROWS
    pages = int(np.ceil(len(items) / per_page))  # the layer test page gets added below
    tint = tuple(float(v) for v in CYAN)
    objs = [color_trigger(5, BG, *map(int, BACKGROUND)), color_trigger(5, GROUND, *map(int, BACKGROUND)), color_trigger(5, BASE, 255, 0, 0)]

    # Always-on corner markers let the analysis find the grid in the recording at any window size.
    corners = [(LEFT - 6, BOTTOM - 6), (LEFT + COLS * CELL + 6, BOTTOM - 6), (LEFT - 6, BOTTOM + ROWS * CELL + 6), (LEFT + COLS * CELL + 6, BOTTOM + ROWS * CELL + 6)]
    for x, y in corners:
        objs.append(rect(x, y, 0.2, 0.2, 0, tint, 0))

    placed = []
    for i, it in enumerate(items):
        page, cell = divmod(i, per_page)
        row, col = divmod(cell, COLS)
        x, y = cell_center(row, col)
        fit = (CELL - 2) / max(it["w"], it["h"])
        objs.append(rect(x, y, fit, fit, 0, tint, 0, str(page + 1), it["id"]))
        placed.append((page, row, col))

    # Extra final page: does layer order hold between sheets? Each pair stacks two solid objects, red on top by layer number.
    solid = {}
    for it in items:
        if it["sheet"] != "mixed" and it["sheet"] not in solid and it["a"].mean() > 0.85 and it["l"].mean() > 0.8:
            solid[it["sheet"]] = it
    home = next(i["sheet"] for i in items if i["id"] == 211)
    layer_tests = []
    pages += 1
    cell = 0
    for sheet, other in solid.items():
        if sheet == home:
            continue
        for top, bottom in ((solid[home], other), (other, solid[home])):
            row, col = divmod(cell, COLS)
            x, y = cell_center(row, col)
            for it, color, z in ((bottom, tint, 1000), (top, RED, 3000)):
                fit = (CELL - 2) / max(it["w"], it["h"])
                objs.append(rect(x, y, fit, fit, 0, color, z, str(pages), it["id"]))
            layer_tests.append((sheet, pages - 1, row, col))
            cell += 1
    # Also check that very high layer numbers keep their order on one sheet, since sharp frames use thousands.
    row, col = divmod(cell, COLS)
    x, y = cell_center(row, col)
    square = solid[home]
    fit = (CELL - 2) / max(square["w"], square["h"])
    objs.append(rect(x, y, fit, fit, 0, tint, 2999, str(pages), square["id"]))
    objs.append(rect(x, y, fit, fit, 0, RED, 3000, str(pages), square["id"]))
    layer_tests.append(("high_layer", pages - 1, row, col))

    # Page 1 is on, wait, page 1 off, wait, page 2 on...
    if len(set(p for p, _r, _c in placed)) >= pages:
        raise SystemExit("Layer test page is missing.")
    show_chain = pages
    hide_chain = pages * 2
    for p in range(pages):
        objs.append(obj(_1=TOGGLE_TRIGGER, _2=5, _3=330 + p * 2, _51=p + 1, _56=0))
        x = 1000 + p * 30
        on, off = show_chain + p + 1, hide_chain + p + 1
        objs.append(toggle(x, 300, p + 1, True, on))
        objs.append(spawn(x, 330, off, SHOW, on))
        objs.append(toggle(x, 360, p + 1, False, off))
        if p + 1 < pages:
            objs.append(spawn(x, 390, on + 1, GAP, off))
    objs.append(spawn(5, 300, show_chain + 1, 4.0))  # time to settle after pressing play

    with open("audit_level.json", "w") as f:
        json.dump({"raw": objs}, f)
    np.savez_compressed("data/audit.npz", names=np.array([i["name"] for i in items]), ids=np.array([i["id"] for i in items]),
                        sheets=np.array([i["sheet"] for i in items]), units=np.array([[i["w"], i["h"]] for i in items], np.float32),
                        alpha=np.stack([i["a"] for i in items]), lum=np.stack([i["l"] for i in items]), placed=np.array(placed), pages=pages,
                        layer_tests=np.array([(s, p, r, c) for s, p, r, c in layer_tests]))
    print(f"{len(items)} objects on {pages} pages, about {pages * (SHOW + GAP) + 4:.0f} seconds to play")
    print('Next: python build_level.py audit_level.json --level "OBJECT AUDIT"')


def record(a):
    for n in range(a.countdown, 0, -1):
        print(f"Recording in {n}: switch to GD and press play...", flush=True)
        time.sleep(1)
    print("Recording the screen. Keep GD in front and don't touch anything.", flush=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "gdigrab", "-framerate", "15", "-t", str(a.seconds), "-i", "desktop",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "10", "-pix_fmt", "yuv444p", a.out], check=True)
    print(f"Saved {a.out}. Next: python object_audit.py analyze {a.out}")


def read_video(path, crop=None, seconds=None, scale_w=1920):
    """crop is x,y,w,h in recording pixels, for a desktop with more than one monitor."""
    info = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
                          capture_output=True, text=True, check=True).stdout.strip().split(",")
    w, h = int(info[0]), int(info[1])
    filters = []
    if crop:
        x, y, w, h = crop
        filters.append(f"crop={w}:{h}:{x}:{y}")
    if w > scale_w:
        h = h * scale_w // w // 2 * 2
        w = scale_w
    filters.append(f"scale={w}:{h}:flags=area")
    limit = ["-t", str(seconds)] if seconds else []
    raw = subprocess.run(["ffmpeg", "-v", "error", *limit, "-i", path, "-vf", ",".join(filters), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3)


def is_cyan(img):
    r, g, b = img[..., 0].astype(np.float32), img[..., 1].astype(np.float32), img[..., 2].astype(np.float32)
    return (b > 90) & (g > 70) & (r < 0.45 * b)


def find_grid(frames):
    """Finds the corner markers: the only cyan that's there on every frame, including the blank gaps."""
    # Markers show during gaps too, so the frames with the least cyan are the gaps.
    amounts = np.array([is_cyan(f).sum() for f in frames])
    settled = amounts > 0
    if not settled.any():
        raise SystemExit("No cyan found in the recording. Was the level playing and GD visible?")
    floor = np.percentile(amounts[settled], 5)
    gap_frames = frames[settled & (amounts <= floor * 1.3)]
    if not len(gap_frames):
        raise SystemExit("Couldn't find a blank moment between pages in the recording.")
    # The game's background has a pattern, so each pixel's blank color comes from the gaps themselves.
    blank = np.median(gap_frames[np.linspace(0, len(gap_frames) - 1, min(30, len(gap_frames))).astype(int)], axis=0).astype(np.float32)
    always = is_cyan(blank.astype(np.uint8))
    ys, xs = np.where(always)
    if not len(xs):
        raise SystemExit("Couldn't find the corner markers. Make sure the whole grid is on screen.")
    return (xs.min(), xs.max() + 1, ys.min(), ys.max() + 1), blank


def find_pages(frames, box, pages):
    """Between pages the grid is grey, so strong color inside the markers means a page is showing. Grey or dim overlays don't count."""
    x0, x1, y0, y1 = box
    px_x, px_y = (x1 - x0) / (COLS * CELL + 18), (y1 - y0) / (ROWS * CELL + 18)
    ix0, ix1, iy0, iy1 = int(x0 + 9 * px_x), int(x1 - 9 * px_x), int(y0 + 9 * px_y), int(y1 - 9 * px_y)
    amounts = np.array([(np.ptp(f[iy0:iy1, ix0:ix1], axis=-1) > 120).mean() for f in frames])
    runs = []
    # The final page only has a few cells, so this can't be relative to the busy pages.
    on = amounts > np.percentile(amounts, 2) + 0.003
    start = None
    for i, v in enumerate(on):
        if v and start is None:
            start = i
        if not v and start is not None:
            if i - start >= 8:
                runs.append((start, i))
            start = None
    if start is not None and len(on) - start >= 8:
        runs.append((start, len(on)))
    if len(runs) != pages:
        raise SystemExit(f"Found {len(runs)} pages in the recording but expected {pages}. Record for longer and start the level during the countdown.")
    return runs


def resample(mask, size):
    h, w = size
    ys = (np.arange(h) * mask.shape[0] // h)
    xs = (np.arange(w) * mask.shape[1] // w)
    return mask[np.ix_(ys, xs)]


def uncolored_share(median, bg):
    """Share of the drawn pixels that didn't take the tint. Tint fading into the background still counts as colored."""
    drawn = np.abs(median - bg).sum(-1) > 45
    # How much tint the red channel implies is here, then what blue should be with that much tint.
    tint = 1 - median[..., 0] / np.maximum(bg[..., 0], 1)
    blue = bg[..., 2] * (1 - tint) + CYAN[2] * tint
    colored = (tint > 0) & (median[..., 2] >= 0.6 * blue)
    return (drawn & ~colored).sum() / max(1, drawn.sum())


def judge(cell_frames, sprite_alpha, sprite_lum, width, height, bg):
    """Returns (moves, wrong_look, empty) for one object's cell. The sprite is drawn as the level placed it."""
    median = np.median(cell_frames, axis=0).astype(np.float32)
    h, w = median.shape[:2]
    # Fit the sprite to the cell the same way the level scaled it.
    fit = (CELL - 2) / max(width, height)
    size_x, size_y = max(1, int(width * fit / CELL * w)), max(1, int(height * fit / CELL * h))
    px, py = (w - size_x) // 2, (h - size_y) // 2
    expected_a = np.zeros((h, w), np.float32)
    expected_l = np.ones((h, w), np.float32)
    expected_a[py:py + size_y, px:px + size_x] = resample(sprite_alpha, (size_y, size_x))
    expected_l[py:py + size_y, px:px + size_x] = resample(sprite_lum, (size_y, size_x))
    expected = bg * (1 - expected_a[..., None]) + CYAN * (expected_a * expected_l)[..., None]

    foreground = np.abs(median - bg).sum(-1) > 45
    moving = np.abs(cell_frames.astype(np.float32) - median).sum(-1)
    moved_area = (moving > 70).mean(axis=(1, 2)).max()
    empty = foreground.mean() < 0.002
    # Score only where the object is or should be, so a thin bar counts as much as a big block.
    area = foreground | (expected_a > 0.1)
    wrong = float(np.abs(median - expected)[area].mean()) / 255 if area.any() else 1.0
    return moved_area > 0.02, wrong > 0.2 or uncolored_share(median, bg) > 0.1, empty, wrong, moved_area


def analyze_frames(frames, audit):
    pages = int(audit["pages"])
    box, blank = find_grid(frames)
    runs = find_pages(frames, box, pages)
    x0, x1, y0, y1 = box
    # The box runs between the outer edges of the corner markers, which sit 6 units outside the grid.
    px_x = (x1 - x0) / (COLS * CELL + 12 + 6)
    px_y = (y1 - y0) / (ROWS * CELL + 12 + 6)
    results = []
    steady = []
    for a, b in runs:
        # Screen capture sometimes drops a black frame, which would look like every object moving at once.
        margin = max(3, (b - a) // 6)
        keep = np.arange(a + margin, b - margin)
        grid = frames[keep, y0:y1, x0:x1]
        median = np.median(grid[:: max(1, len(grid) // 9)], axis=0)
        steady.append(keep[[(np.abs(g.astype(np.float32) - median).sum(-1) > 70).mean() < 0.2 for g in grid]])
    for i, (page, row, col) in enumerate(audit["placed"].tolist()):
        cy0 = int(y0 + (9 + row * CELL) * px_y)
        cy1 = int(y0 + (9 + (row + 1) * CELL) * px_y)
        cx0 = int(x0 + (9 + col * CELL) * px_x)
        cx1 = int(x0 + (9 + (col + 1) * CELL) * px_x)
        cell = frames[steady[page], cy0:cy1, cx0:cx1]
        w, h = audit["units"][i]
        results.append(judge(cell, audit["alpha"][i], audit["lum"][i], float(w), float(h), blank[cy0:cy1, cx0:cx1]))
    return results, (x0, x1, y0, y1), runs


def hidden_cells(placed, box, rects):
    """Overlays and text drawn over the game can't be told apart from the object, so those cells get no verdict."""
    x0, x1, y0, y1 = box
    px_x, px_y = (x1 - x0) / (COLS * CELL + 18), (y1 - y0) / (ROWS * CELL + 18)
    hidden = np.zeros(len(placed), bool)
    for rx0, ry0, rx1, ry1 in rects:
        left = x0 + (9 + placed[:, 2] * CELL) * px_x
        top = y0 + (9 + placed[:, 1] * CELL) * px_y
        hidden |= (left < rx1) & (left + CELL * px_x > rx0) & (top < ry1) & (top + CELL * px_y > ry0)
    return hidden


def analyze(a):
    audit = np.load("data/audit.npz")
    frames = read_video(a.video, a.crop, a.seconds)
    results, box, runs = analyze_frames(frames, audit)
    print(f"grid box {tuple(int(v) for v in box)}, page frames {runs}")
    moves = np.array([r[0] for r in results])
    wrong = np.array([r[1] for r in results])
    empty = np.array([r[2] for r in results])
    forced = audit["ids"] == 211
    untested = hidden_cells(audit["placed"], box, a.ignore)
    passed = (~moves & ~wrong & ~empty & ~untested) | forced
    print(f"{len(results)} objects: {passed.sum()} passed, {(moves & ~untested).sum()} move, {(wrong & ~untested).sum()} look wrong or ignore the tint, "
          f"{(empty & ~untested).sum()} show nothing, {untested.sum()} hidden by overlays so left out")
    if not forced.any():
        raise SystemExit("The base square (object 211) is missing from the audit.")
    sheets = audit["sheets"]
    print("passed by sheet:", {str(s): int((passed & (sheets == s)).sum()) for s in np.unique(sheets)})

    # A sheet only joins the library if layer order held against the base square's sheet in both directions.
    home = str(sheets[forced][0])
    x0, x1, y0, y1 = box
    px_x, px_y = (x1 - x0) / (COLS * CELL + 18), (y1 - y0) / (ROWS * CELL + 18)
    votes = {}
    for sheet, page, row, col in audit["layer_tests"].tolist():
        s, e = runs[int(page)]
        cy, cx = int(y0 + (9 + (int(row) + 0.5) * CELL) * px_y), int(x0 + (9 + (int(col) + 0.5) * CELL) * px_x)
        r, _g, b = np.median(frames[s + 3:e - 3, cy - 2:cy + 2, cx - 2:cx + 2].reshape(-1, 3), axis=0)
        votes.setdefault(sheet, []).append(bool(r > b))
    print("layer order tests (True means correct):", votes)
    if not all(votes.get("high_layer", [False])):
        print("WARNING: high layer numbers lost their order, which would scramble sharp mode. Send me this message.")
    allowed = {home} | {s for s, v in votes.items() if s != "high_layer" and len(v) == 2 and all(v)}
    print("sheets the library may use:", sorted(allowed))
    usable = passed & np.isin(sheets, sorted(allowed))
    if not usable[forced].any():
        raise SystemExit("The base square didn't make it into the library.")
    np.savez_compressed("data/library.npz", alpha=audit["alpha"][usable], lum=audit["lum"][usable], ids=audit["ids"][usable],
                        units=audit["units"][usable], names=audit["names"][usable])
    json.dump({"passed": audit["names"][passed].tolist(), "moves": audit["names"][moves].tolist(), "wrong": audit["names"][wrong].tolist(), "empty": audit["names"][empty].tolist(), "untested": audit["names"][untested].tolist()},
              open("data/audit_results.json", "w"), indent=0)
    print(f"Library written with {usable.sum()} objects. fit_sharp.py will use them automatically.")

    # Report: red over objects that failed, so the verdicts can be checked by eye.
    shots = []
    for p, (s, e) in enumerate(runs):
        shot = frames[(s + e) // 2, y0:y1, x0:x1].copy()
        px_x, px_y = (x1 - x0) / (COLS * CELL + 18), (y1 - y0) / (ROWS * CELL + 18)
        for i, (page, row, col) in enumerate(audit["placed"].tolist()):
            if page == p and not passed[i]:
                cy, cx = int((9 + (row + 0.5) * CELL) * px_y), int((9 + (col + 0.5) * CELL) * px_x)
                shot[max(0, cy - 3):cy + 3, max(0, cx - 3):cx + 3] = [255, 0, 0]
        shots.append(shot)
    height = min(s.shape[0] for s in shots)
    sheet = np.vstack([np.hstack([s[:height] for s in shots[i:i + 3]]) for i in range(0, len(shots) - len(shots) % 3, 3)]) if len(shots) >= 3 else np.hstack(shots)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{sheet.shape[1]}x{sheet.shape[0]}", "-i", "-",
                    "-frames:v", "1", "data/audit_report.png"], input=sheet.tobytes(), check=True)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("make").set_defaults(fn=make)
    r = sub.add_parser("record")
    r.add_argument("--seconds", type=int, default=75)
    r.add_argument("--countdown", type=int, default=8)
    r.add_argument("--out", default="audit_capture.mp4")
    r.set_defaults(fn=record)
    an = sub.add_parser("analyze")
    an.add_argument("video")
    an.add_argument("--crop", type=lambda v: tuple(map(int, v.split(","))), help="x,y,w,h of the monitor GD was on")
    an.add_argument("--seconds", type=int, help="only read this much of the recording")
    an.add_argument("--ignore", type=lambda v: tuple(map(int, v.split(","))), action="append", default=[], help="x0,y0,x1,y1 covered by an overlay, after cropping")
    an.set_defaults(fn=analyze)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
