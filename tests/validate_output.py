#!/usr/bin/env python3
"""Validate an E3D Bridge export folder. Pure Python - runs inside
Blender's Python or any system Python 3.

Checks (per the plan):
  - OBJ parses; every face index is in range; faces are triangles
  - every `usemtl` has an MTL entry; mtllib exists
  - every map_* path is relative, exists, has no spaces / non-ASCII
  - every image is PNG or JPG and within the max size
  - report.txt exists

validate(folder) returns a list of error strings (empty = pass).
"""

import os
import re
import struct
import sys


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    w, h = struct.unpack(">II", head[16:24])
    return w, h


def jpg_size(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i < len(data) - 9:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
        else:
            seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
            i += 2 + seg_len
    return None


def parse_mtl(path):
    """Return {material_name: {key: value}} with map_* values as paths."""
    mats = {}
    current = None
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            key = parts[0]
            rest = parts[1] if len(parts) > 1 else ""
            if key == "newmtl":
                current = rest
                mats[current] = {}
            elif current is not None:
                mats[current][key] = rest
    return mats


def validate(folder, max_texture_size=8192, expect_triangles=True):
    errors = []

    objs = [f for f in os.listdir(folder) if f.endswith(".obj")]
    if len(objs) != 1:
        return ["expected exactly one .obj in %s, found %d" % (folder, len(objs))]
    obj_path = os.path.join(folder, objs[0])

    if not os.path.isfile(os.path.join(folder, "report.txt")):
        errors.append("report.txt missing")

    v = vt = vn = 0
    mtllib = None
    used_materials = set()
    objects = set()
    face_count = 0

    with open(obj_path, "r", encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            tag = parts[0]
            if tag == "v":
                v += 1
            elif tag == "vt":
                vt += 1
            elif tag == "vn":
                vn += 1
            elif tag == "mtllib":
                mtllib = line.split(None, 1)[1]
            elif tag == "usemtl":
                used_materials.add(line.split(None, 1)[1])
            elif tag == "o":
                name = line.split(None, 1)[1]
                objects.add(name)
                if not re.fullmatch(r"[A-Za-z0-9_]+", name):
                    errors.append("line %d: object name '%s' not sanitized" % (ln, name))
            elif tag == "f":
                face_count += 1
                idxs = parts[1:]
                if expect_triangles and len(idxs) != 3:
                    errors.append("line %d: face has %d verts, expected 3" % (ln, len(idxs)))
                for corner in idxs:
                    bits = corner.split("/")
                    vi = int(bits[0])
                    if vi < 1 or vi > v:
                        errors.append("line %d: vertex index %d out of range" % (ln, vi))
                    if len(bits) > 1 and bits[1]:
                        if int(bits[1]) > vt:
                            errors.append("line %d: uv index out of range" % ln)
                    if len(bits) > 2 and bits[2]:
                        if int(bits[2]) > vn:
                            errors.append("line %d: normal index out of range" % ln)

    if face_count == 0:
        errors.append("OBJ has no faces")
    if not objects:
        errors.append("OBJ has no 'o' blocks")

    if mtllib is None:
        errors.append("no mtllib line")
        return errors
    mtl_path = os.path.join(folder, mtllib)
    if not os.path.isfile(mtl_path):
        errors.append("mtllib '%s' does not exist" % mtllib)
        return errors

    mats = parse_mtl(mtl_path)
    for name in used_materials:
        if name not in mats:
            errors.append("usemtl '%s' has no MTL entry" % name)
        if not re.fullmatch(r"[A-Za-z0-9_]+", name):
            errors.append("material name '%s' not sanitized" % name)

    for name, keys in mats.items():
        for key, val in keys.items():
            if not key.startswith("map_"):
                continue
            if os.path.isabs(val) or re.match(r"^[A-Za-z]:", val):
                errors.append("%s/%s: path not relative: %s" % (name, key, val))
                continue
            if " " in val or not val.isascii():
                errors.append("%s/%s: path has spaces or non-ASCII: %s" % (name, key, val))
            tex = os.path.join(folder, val)
            if not os.path.isfile(tex):
                errors.append("%s/%s: file missing: %s" % (name, key, val))
                continue
            ext = os.path.splitext(val)[1].lower()
            if ext not in (".png", ".jpg", ".jpeg"):
                errors.append("%s/%s: not PNG/JPG: %s" % (name, key, val))
                continue
            size = png_size(tex) if ext == ".png" else jpg_size(tex)
            if size is None:
                errors.append("%s/%s: unreadable image: %s" % (name, key, val))
            elif max(size) > max_texture_size:
                errors.append("%s/%s: %dx%d exceeds max %d"
                              % (name, key, size[0], size[1], max_texture_size))

    return errors


def stats(folder):
    """Small helper for test assertions: returns dict of counts."""
    objs = [f for f in os.listdir(folder) if f.endswith(".obj")]
    result = {"objects": 0, "faces": 0, "materials": set(), "has_vt": False,
              "object_names": [], "faces_by_object": {}}
    if not objs:
        return result
    current = None
    with open(os.path.join(folder, objs[0]), "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("o "):
                result["objects"] += 1
                current = line.split(None, 1)[1].strip()
                result["object_names"].append(current)
                result["faces_by_object"][current] = 0
            elif line.startswith("f "):
                result["faces"] += 1
                if current is not None:
                    result["faces_by_object"][current] += 1
            elif line.startswith("vt "):
                result["has_vt"] = True
            elif line.startswith("usemtl "):
                result["materials"].add(line.split(None, 1)[1].strip())
    return result


if __name__ == "__main__":
    folder = sys.argv[1] if len(sys.argv) > 1 else "."
    errs = validate(folder)
    for e in errs:
        print("FAIL:", e)
    print("%s: %s" % (folder, "OK" if not errs else "%d errors" % len(errs)))
    sys.exit(1 if errs else 0)
