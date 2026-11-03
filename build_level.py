"""Write fitted shapes into one named level of a Geometry Dash save.

Usage: python build_level.py clip_shapes.json --level "SHAPES TEST"
Creates the level if it doesn't exist. Run it from your own terminal with GD closed. The save is backed up first.
"""
import argparse
import base64
import colorsys
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import time

BLOCK = 211  # 30x30 solid square that takes a color channel
COLOR_TRIGGER, TOGGLE_TRIGGER, SPAWN_TRIGGER = 899, 1049, 1268
BG, GROUND, BASE = 1000, 1001, 1  # BASE channel is pure red so HSV can shift it to any color
PIC = 210.0  # picture size in GD units
X0, Y0 = 60.0, 10.0  # picture's bottom-left corner, just right of where the player spawns
DEFAULT_HEADER = b"kA13,0,kA15,0,kA16,0,kA14,,kA6,0,kA7,0,kA25,0,kA17,0,kA18,0,kS39,0,kA2,0,kA3,0,kA8,0,kA4,0,kA9,0,kA10,0,kA11,0"


def num(v):
    return f"{v:.3f}".rstrip("0").rstrip(".")


def obj(**p):
    return ",".join(f"{k[1:]},{v}" for k, v in p.items())


def hsv_string(r, g, b):
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    deg = h * 360
    deg = deg - 360 if deg > 180 else deg
    return f"{num(deg)}a{num(s)}a{num(v)}a0a0"


def rect(x, y, sx, sy, deg, rgb, z, groups=None, object_id=BLOCK, layer=9):
    hsv = hsv_string(*rgb)
    p = dict(_1=object_id, _2=num(x), _3=num(y), _6=num(deg), _128=num(sx), _129=num(sy), _21=BASE, _24=layer, _25=z, _41=1, _43=hsv)
    if object_id != BLOCK:  # some objects draw through their detail channel, so both get the same tint
        p.update(_22=BASE, _42=1, _44=hsv)
    if groups:
        p["_57"] = groups
    return obj(**p)


def color_trigger(x, channel, r, g, b):
    return obj(_1=COLOR_TRIGGER, _2=x, _3=300, _7=r, _8=g, _9=b, _10=0, _23=channel)


def toggle(x, y, group, on, chain):
    return obj(_1=TOGGLE_TRIGGER, _2=x, _3=y, _51=group, _56=int(on), _57=chain, _62=1, _87=1)


def spawn(x, y, target, delay, chain=None):
    p = dict(_1=SPAWN_TRIGGER, _2=x, _3=y, _51=target, _63=num(delay))
    if chain:
        p.update(_57=chain, _62=1, _87=1)
    return obj(**p)


def persistent_objects(data):
    """Objects stay on after their frame appears; a keyframe switches on its new set and clears the previous ones."""
    frames, size, fps = data["frames"], data["size"], data["fps"]
    n = len(frames)
    u = PIC / size
    objs = [color_trigger(5, BG, 0, 0, 0), color_trigger(5, GROUND, 0, 0, 0), color_trigger(5, BASE, 255, 0, 0)]
    showing = []  # groups currently on since the last keyframe
    z = 0
    for i, frame in enumerate(frames):
        group = i + 1
        chain = n + i + 1
        if frame["reset"]:
            z = 0  # layer order only has to hold within one keyframe's set of objects
        for sid, cx, cy, w, h, deg, r, g, b in frame["new"]:
            unit_w, unit_h = data["units"][sid]
            objs.append(rect(X0 + cx * u, Y0 + (size - cy) * u, w * u / unit_w, h * u / unit_h, deg, (r, g, b), z, str(group), data["ids"][sid]))
            z += 1
        x = 1000 + i * 30
        if frame["new"]:
            objs.append(toggle(x, 330, group, True, chain))
        # New objects go on before old ones go off, so a keyframe never flashes the background.
        if frame["reset"]:
            objs.extend(toggle(x, 300 + 30 * k, old, False, chain) for k, old in enumerate(showing, 2))
            showing = []
        if frame["new"]:
            showing.append(group)
        if i + 1 < n:
            objs.append(spawn(x, 360, chain + 1, 1 / fps, chain))
    side = PIC
    for bx, by in ((-side / 2, side / 2), (side * 1.5, side / 2), (side / 2, -side / 2), (side / 2, side * 1.5)):
        objs.append(rect(X0 + bx, Y0 + by, side / 30 * (3 if bx == side / 2 else 1), side / 30, 0, (0, 0, 0), 0, None, BLOCK, 11))
    for i in range(n):
        objs.append(obj(_1=TOGGLE_TRIGGER, _2=5, _3=330 + i * 2, _51=i + 1, _56=0))
    objs.append(spawn(5, 300, n + 1, 0.5))
    return objs


def level_objects(data):
    if data.get("format") == "persistent":
        return persistent_objects(data)
    frames, size, fps = data["frames"], data["size"], data["fps"]
    n = len(frames)
    u = PIC / size
    objs = [color_trigger(5, BG, 0, 0, 0), color_trigger(5, GROUND, 0, 0, 0), color_trigger(5, BASE, 255, 0, 0)]

    # Frame i is group i+1 and its trigger chain is group n+i+1, so the video needs 2 groups per frame.
    for i, shapes in enumerate(frames):
        for z, row in enumerate(shapes):
            if "ids" in data:  # real GD objects from fit_objects.py
                sid, cx, cy, w, h, deg, r, g, b = row
                object_id, (unit_w, unit_h) = data["ids"][sid], data["units"][sid]
            else:  # plain rectangles from fit_shapes.py
                cx, cy, w, h, deg, r, g, b = row
                object_id, unit_w, unit_h = BLOCK, 30, 30
            objs.append(rect(X0 + cx * u, Y0 + (size - cy) * u, w * u / unit_w, h * u / unit_h, deg, (r, g, b), z - 50, str(i + 1), object_id))
    # Shapes can overhang the picture edge, so black bars on top hide the overhang.
    side = PIC
    for bx, by in ((-side / 2, side / 2), (side * 1.5, side / 2), (side / 2, -side / 2), (side / 2, side * 1.5)):
        objs.append(rect(X0 + bx, Y0 + by, side / 30 * (3 if bx == side / 2 else 1), side / 30, 0, (0, 0, 0), 200))

    for i in range(n):
        objs.append(obj(_1=TOGGLE_TRIGGER, _2=5, _3=330 + i * 2, _51=i + 1, _56=0))
    for i in range(n):
        chain = n + i + 1
        x = 1000 + i * 30
        if i:
            objs.append(toggle(x, 300, i, False, chain))
        objs.append(toggle(x, 330, i + 1, True, chain))
        if i + 1 < n:
            objs.append(spawn(x, 360, chain + 1, 1 / fps, chain))
    objs.append(spawn(5, 300, n + 1, 0.5))  # small delay so the first frame isn't lost to loading
    return objs


def object_test_objects(data, top=220, cols=22):
    """The objects a fitted video uses most, in a grid, tinted cyan to expose any that ignore tinting or move by themselves."""
    uses = {}
    for shapes in data["frames"]:
        for row in (shapes["new"] if isinstance(shapes, dict) else shapes):
            uses[row[0]] = uses.get(row[0], 0) + 1
    picks = sorted(uses, key=lambda s: -uses[s])[:top]
    objs = [color_trigger(5, BG, 0, 0, 0), color_trigger(5, GROUND, 0, 0, 0), color_trigger(5, BASE, 255, 0, 0)]
    for i, sid in enumerate(picks):
        w, h = data["units"][sid]
        fit = 26 / max(w, h)
        objs.append(rect(45 + i % cols * 30, 300 - i // cols * 30, fit, fit, 0, (0, 220, 255), 0, None, data["ids"][sid]))
    covered = sum(uses[s] for s in picks) / sum(uses.values())
    print(f"Object test: top {len(picks)} of {len(uses)} objects used, covering {covered:.0%} of placements")
    return objs


def decode_save(raw):
    b = raw.translate(bytes(i ^ 11 for i in range(256))).rstrip(b"\0")
    b += b"=" * (-len(b) % 4)
    return gzip.decompress(base64.urlsafe_b64decode(b))


def encode_save(xml):
    return base64.urlsafe_b64encode(gzip.compress(xml, 6)).translate(bytes(i ^ 11 for i in range(256)))


def decode_level(s):
    s += b"=" * (-len(s) % 4)
    return gzip.decompress(base64.urlsafe_b64decode(s))


def encode_level(s):
    return base64.urlsafe_b64encode(gzip.compress(s, 6))


def level_string(header, objects):
    header = re.sub(rb",?kA22,\d+", b"", header).lstrip(b",") + b",kA22,1"  # platformer mode: the player stays put, so nothing has to scroll
    return encode_level(header + b";" + ";".join(objects).encode() + b";")


def add_level(xml, name, objects):
    """Puts a new level first in the list, where GD shows the newest level, and renumbers the rest."""
    if not re.fullmatch(r"[A-Za-z0-9 ]{1,20}", name):
        sys.exit("New level names can only use letters, numbers and spaces (20 characters at most).")
    list_start = xml.index(b"<k>LLM_01</k>")
    list_end = xml.index(b"<k>LLM_02</k>")
    opening = b"<k>_isArr</k><t />"
    insert_at = xml.index(opening, list_start) + len(opening)
    levels = xml[insert_at:list_end]
    count = len(re.findall(rb"<k>k_\d+</k><d>", levels))
    assert count == len(re.findall(rb"<k>k2</k>", levels)), "level list has an unexpected layout"
    levels = re.sub(rb"<k>k_(\d+)</k><d>", lambda m: b"<k>k_%d</k><d>" % (int(m.group(1)) + 1), levels)

    author = re.search(rb"<k>k5</k><s>(.*?)</s>", xml)
    version = re.search(rb"<k>LLM_02</k><i>(\d+)</i>", xml)
    ids = [int(v) for v in re.findall(rb"<k>k83</k><i>(\d+)</i>", levels)]
    fields = [b"<k>kCEK</k><i>4</i>", b"<k>k2</k><s>" + name.encode() + b"</s>", b"<k>k4</k><s>" + level_string(DEFAULT_HEADER, objects) + b"</s>",
              b"<k>k5</k><s>" + (author.group(1) if author else b"Player") + b"</s>", b"<k>k13</k><t />", b"<k>k21</k><i>2</i>", b"<k>k16</k><i>1</i>",
              b"<k>k83</k><i>%d</i>" % (max(ids, default=0) + 1), b"<k>k50</k><i>" + (version.group(1) if version else b"47") + b"</i>",
              b"<k>kI1</k><r>0</r><k>kI2</k><r>0</r><k>kI3</k><r>0</r><k>kI6</k><d />"]
    entry = b"<k>k_0</k><d>" + b"".join(fields) + b"</d>"
    return xml[:insert_at] + entry + levels + xml[list_end:]


def replace_level(xml, name, objects, overwrite):
    """Returns (new save XML, what happened), changing only the named level or creating it if it's missing."""
    tag = b"<k>k2</k><s>" + name.encode() + b"</s>"
    hits = [m.start() for m in re.finditer(re.escape(tag), xml)]
    if not hits:
        return add_level(xml, name, objects), f'Created new level "{name}".'
    if len(hits) > 1:
        sys.exit(f'Found {len(hits)} levels named "{name}". Rename them or pick another name so the right one gets changed.')
    start = hits[0] + len(tag)
    next_level = xml.find(b"<k>k2</k>", start)
    end_limit = next_level if next_level != -1 else len(xml)
    k4 = xml.find(b"<k>k4</k><s>", start, end_limit)
    if k4 == -1:  # created in GD but never opened, so it has no level data yet
        data = b"<k>k4</k><s>" + level_string(DEFAULT_HEADER, objects) + b"</s>"
        return xml[:start] + data + xml[start:], f'Initialized empty level "{name}".'
    data_start = k4 + len(b"<k>k4</k><s>")
    data_end = xml.index(b"</s>", data_start)

    old = decode_level(xml[data_start:data_end])
    header, _, body = old.partition(b";")
    old_count = body.count(b";")
    if old_count > 10 and not overwrite:
        # Previous runs of this tool are safe to replace; anything else may be someone's real level.
        if not body.startswith(b"1,%d," % COLOR_TRIGGER):
            sys.exit(f'"{name}" already has {old_count} objects. Refusing to overwrite it, because it may be a real level.')
    return xml[:data_start] + level_string(header, objects) + xml[data_end:], f'Replaced {old_count} old objects in "{name}".'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("shapes", help="fitted JSON file")
    ap.add_argument("--level", required=True)
    ap.add_argument("--object-test", action="store_true", help="build a level showing the objects this video uses most, instead of the video")
    ap.add_argument("--save", default=os.path.join(os.environ.get("LOCALAPPDATA", ""), "GeometryDash", "CCLocalLevels.dat"))
    ap.add_argument("--overwrite-nonempty", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="do everything except replacing the save")
    a = ap.parse_args()

    running = subprocess.run(["tasklist", "/FI", "IMAGENAME eq GeometryDash.exe"], capture_output=True, text=True).stdout
    if "GeometryDash.exe" in running and not a.dry_run:
        sys.exit("Close Geometry Dash first, or it will overwrite this change when it exits.")

    if not a.shapes:
        sys.exit("Give a fitted JSON file.")
    with open(a.shapes) as f:
        data = json.load(f)
    if "raw" in data:  # object_audit.py's level is already built
        objects = data["raw"]
        print(f"{len(objects)} objects and triggers")
    elif a.object_test:
        if "ids" not in data:
            sys.exit("--object-test needs a file from fit_objects.py.")
        objects = object_test_objects(data)
    else:
        objects = level_objects(data)
        triggers = sum(1 for o in objects if o.split(",")[1] in (str(COLOR_TRIGGER), str(TOGGLE_TRIGGER), str(SPAWN_TRIGGER)))
        print(f"{len(data['frames'])} frames: {len(objects) - triggers} objects, {triggers} triggers, {len(data['frames']) * 2} groups")

    with open(a.save, "rb") as f:
        raw = f.read()
    xml = decode_save(raw)
    new_xml, action = replace_level(xml, a.level, objects, a.overwrite_nonempty)
    encoded = encode_save(new_xml)

    # Verify before touching the real file: no existing level may go missing or change position.
    check = decode_save(encoded)
    assert check == new_xml, "save did not round-trip"
    names = rb"<k>k2</k><s>(.*?)</s>"
    before, after = re.findall(names, xml), re.findall(names, check)
    assert after == before or after == [a.level.encode()] + before, "level list changed"
    print(f"Round-trip check passed. {action}")
    if a.dry_run:
        print("Dry run: save not changed.")
        return

    backup = f"{a.save}.before-shapes-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(a.save, backup)
    tmp = a.save + ".tmp"
    with open(tmp, "wb") as f:
        f.write(encoded)
    os.replace(tmp, a.save)
    print(f"Done. Backup saved to {backup}")


if __name__ == "__main__":
    main()
