"""Self-check for build_level.py: python test_build.py <any save copy>"""
import re
import sys

from build_level import decode_level, decode_save, encode_level, encode_save, hsv_string, level_objects, replace_level

# Colors: red base with HSV must reproduce each RGB.
assert hsv_string(255, 0, 0) == "0a1a1a0a0"
assert hsv_string(0, 0, 255) == "-120a1a1a0a0"
assert hsv_string(0, 0, 0).endswith("a0a0a0a0")

data = {"size": 96, "fps": 30, "frames": [[[48, 48, 97, 97, 0, 10, 20, 30]], [[48, 48, 97, 97, 0, 40, 50, 60]]]}
objs = level_objects(data)
chain = [o for o in objs if ",57,3" in o or ",57,4" in o]
assert len(chain) == 4, chain  # frame 1: on + spawn; frame 2: off + on

old_level = encode_level(b"kS38,x,kA13,0;1,1,2,15,3,15;")
real_level = encode_level(b"h;" + b"1,1;" * 50)
xml = (b"<dict><k>LLM_01</k><d><k>_isArr</k><t />"
       b"<k>k_0</k><d><k>k2</k><s>A</s><k>k4</k><s>" + old_level + b"</s><k>k5</k><s>me</s><k>k83</k><i>7</i></d>"
       b"<k>k_1</k><d><k>k2</k><s>REAL</s><k>k4</k><s>" + real_level + b"</s><k>k83</k><i>9</i></d>"
       b"<k>k_2</k><d><k>k2</k><s>EMPTY</s><k>k83</k><i>3</i></d>"
       b"</d><k>LLM_02</k><i>47</i></dict>")

# Replacing keeps the header and leaves other levels byte-identical.
new, action = replace_level(xml, "A", ["1,211"], False)
assert "Replaced 1" in action, action
assert decode_level(re.search(rb"<s>A</s><k>k4</k><s>(.*?)</s>", new).group(1)) == b"kS38,x,kA13,0,kA22,1;1,211;"
assert new.endswith(xml[xml.index(b"<k>k_1</k>"):]), "other levels must stay byte-identical"

# A missing level gets created first and everything else moves down one.
new, action = replace_level(xml, "NEW ONE", ["1,211"], False)
assert "Created" in action, action
assert re.findall(rb"<k>k_(\d+)</k><d>", new) == [b"0", b"1", b"2", b"3"]
assert re.findall(rb"<k>k2</k><s>(.*?)</s>", new) == [b"NEW ONE", b"A", b"REAL", b"EMPTY"]
assert b"<k>k83</k><i>10</i>" in new, "new level needs an unused id"
assert decode_level(re.search(rb"NEW ONE</s><k>k4</k><s>(.*?)</s>", new).group(1)).endswith(b",kA22,1;1,211;")
assert real_level in new and old_level in new

# A level that exists but was never opened gets initialized in place.
new, action = replace_level(xml, "EMPTY", ["1,211"], False)
assert "Initialized" in action, action
assert decode_level(re.search(rb"EMPTY</s><k>k4</k><s>(.*?)</s>", new).group(1)).endswith(b",kA22,1;1,211;")
assert new.count(b"<k>k2</k>") == 3

# Rebuilding over this tool's own output is allowed.
ours = encode_level(b"h;" + b"1,899,2,5;" + b"1,211;" * 50)
mine = xml.replace(old_level, ours)
assert "Replaced" in replace_level(mine, "A", ["1,211"], False)[1]

# The guards must refuse a real level, a duplicate name and an unsafe new name.
for name, source in (("REAL", xml), ("A", xml + b"<k>k2</k><s>A</s>"), ("bad<name", xml)):
    try:
        replace_level(source, name, ["1,211"], False)
    except SystemExit as e:
        print("refused as expected:", str(e)[:60])
    else:
        raise AssertionError(f"{name} was not refused")

if len(sys.argv) > 1:
    x = decode_save(open(sys.argv[1], "rb").read())
    assert decode_save(encode_save(x)) == x
    print("real save round-trips,", len(re.findall(rb"<k>k2</k>", x)), "levels")
print("all checks passed")
