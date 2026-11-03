"""Self-check for object_audit.py's analysis using a simulated recording with known good and bad objects."""
import numpy as np

import object_audit as oa

rng = np.random.default_rng(3)
N = 800  # more than one page's worth
S = 48
yy, xx = np.mgrid[0:S, 0:S]
alpha = np.zeros((N, S, S), np.float32)
for i in range(N):  # a mix of circles and bars, so each sprite has a recognizable shape
    if i % 2:
        alpha[i] = ((xx - 24) ** 2 + (yy - 24) ** 2 < rng.integers(10, 23) ** 2)
    else:
        alpha[i, rng.integers(4, 20):rng.integers(28, 44), 2:46] = 1
lum = np.ones((N, S, S), np.float32)
units = np.full((N, 2), 30, np.float32)
placed = np.array([(*divmod(i, oa.COLS * oa.ROWS), 0)[:1] + divmod(i % (oa.COLS * oa.ROWS), oa.COLS) for i in range(N)])
audit = dict(pages=int(placed[:, 0].max() + 1), placed=placed, alpha=alpha, lum=lum, units=units)

GOOD, MOVER, BLACK, EMPTY, EXTRA = 0, 1, 2, 3, 4
kinds = np.zeros(N, int)
kinds[10:20] = MOVER
kinds[50:60] = BLACK
kinds[100:110] = EMPTY
kinds[150:160] = EXTRA

W, H = 1600, 900
PX = 3.0  # pixels per game unit
ox, oy = 300, 100  # top-left corner of the grid in the simulated window


def cell_box(row, col):
    return int(oy + row * oa.CELL * PX), int(ox + col * oa.CELL * PX)


def draw_frame(page, tick):
    f = np.full((H, W, 3), 30, np.uint8)
    f[oy - 40:oy + int(oa.ROWS * oa.CELL * PX) + 40, ox - 40:ox + int(oa.COLS * oa.CELL * PX) + 40] = oa.BACKGROUND
    cell_px = int(oa.CELL * PX)
    for dx, dy in ((-6, -6), (oa.COLS * oa.CELL + 6, -6), (-6, oa.ROWS * oa.CELL + 6), (oa.COLS * oa.CELL + 6, oa.ROWS * oa.CELL + 6)):
        cx, cy = int(ox + dx * PX), int(oy + dy * PX)
        f[cy - 9:cy + 9, cx - 9:cx + 9] = oa.CYAN
    if page is None:
        return f
    inner = int((oa.CELL - 2) * PX)
    for i in np.where(placed[:, 0] == page)[0]:
        if kinds[i] == EMPTY:
            continue
        y, x = cell_box(placed[i, 1], placed[i, 2])
        margin = (cell_px - inner) // 2
        mask = oa.resample(alpha[i], (inner, inner)) > 0.5
        color = np.array([0, 0, 0], np.uint8) if kinds[i] == BLACK else oa.CYAN.astype(np.uint8)
        if kinds[i] == MOVER:
            mask = np.roll(mask, tick % inner, axis=1)
        if kinds[i] == EXTRA:
            mask = np.ones_like(mask)
            f[y + margin:y + margin + inner, x + margin:x + margin + inner] = [255, 255, 255]
        f[y + margin:y + margin + inner, x + margin:x + margin + inner][mask] = color if kinds[i] != EXTRA else [255, 255, 255]
    return f


frames = []
object_pages = audit["pages"]
audit["pages"] = object_pages + 1  # the real level ends with a nearly empty layer test page


def draw_layer_page():
    f = draw_frame(None, 0)
    for col in range(5):
        y, x = cell_box(0, col)
        f[y + 3:y + 33, x + 3:x + 33] = oa.CYAN  # like the real page, the bottom object shows too
        f[y + 6:y + 30, x + 6:x + 30] = [255, 40, 40]
    return f


for p in [None] * 8 + [item for page in range(object_pages) for item in [page] * 40 + [None] * 8]:
    frames.append(draw_frame(p, len(frames)))
frames += [draw_layer_page()] * 40 + [draw_frame(None, 0)] * 8
frames = np.stack(frames)

results, box, runs = oa.analyze_frames(frames, audit)
print("grid box", box, "pages found", len(runs))
assert len(runs) == audit["pages"]
moves = np.array([r[0] for r in results])
wrong = np.array([r[1] for r in results])
empty = np.array([r[2] for r in results])
bad = moves | wrong | empty
print("good objects wrongly rejected:", bad[kinds == GOOD].sum(), "of", (kinds == GOOD).sum())
print("worst look score on good objects:", max(results[i][3] for i in np.where(kinds == GOOD)[0]))
for name, kind in (("moving", MOVER), ("black", BLACK), ("empty", EMPTY), ("wrong shape", EXTRA)):
    print(f"{name} objects caught: {bad[kinds == kind].sum()} of {(kinds == kind).sum()}")
assert bad[kinds == GOOD].mean() < 0.01, "too many good objects rejected"
for kind in (MOVER, BLACK, EMPTY, EXTRA):
    assert bad[kinds == kind].all(), f"missed bad objects of kind {kind}"
print("all checks passed")
