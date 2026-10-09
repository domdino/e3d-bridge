#!/usr/bin/env python3
"""Generate the Phase 0 probe kit: hand-built OBJ/MTL/PNG files that
answer the discovery-spike questions in docs/e3d-material-mapping.md
when imported into Element 3D.

Pure Python (struct + zlib), no Blender, no Pillow. Run:

    python3 tests/make_probe_kit.py

Output: e3d_probe_kit/ at the repo root.
"""

import math
import os
import struct
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT = os.path.join(ROOT, "e3d_probe_kit")


# ----------------------------------------------------------------------
# Minimal PNG writer (streaming, so the 8K image does not eat RAM)
# ----------------------------------------------------------------------

def _chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def write_png(path, w, h, row_fn, bit_depth=8, color_type=2):
    """color_type: 0=gray, 2=RGB, 6=RGBA. row_fn(y) -> raw row bytes."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    comp = zlib.compressobj(9)
    idat = bytearray()
    for y in range(h):
        idat += comp.compress(b"\x00" + row_fn(y))
    idat += comp.flush()
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, bit_depth,
                                            color_type, 0, 0, 0)))
        f.write(_chunk(b"IDAT", bytes(idat)))
        f.write(_chunk(b"IEND", b""))
    return path


def solid_png(path, w, h, rgb):
    row = bytes(rgb) * w
    return write_png(path, w, h, lambda y: row, color_type=2)


def checker_png(path, w, h, rgb_a, rgb_b, cell=64):
    def row(y):
        out = bytearray()
        band = (y // cell) % 2
        for x in range(w):
            out += bytes(rgb_a if ((x // cell) % 2) == band else rgb_b)
        return bytes(out)
    return write_png(path, w, h, row, color_type=2)


def normal_stripes_png(path, w, h):
    """Tangent-space normal map: smooth horizontal ridges. Green varies
    with a sine down the image; if E3D needs the green flipped, the
    ridge lighting will look lit from below."""
    def row(y):
        g = int(128 + 100 * math.sin(2 * math.pi * y / 64.0))
        return bytes((128, g, 255)) * w
    return write_png(path, w, h, row, color_type=2)


def hgradient_png(path, w, h, left=0, right=255):
    row = bytes(
        v for x in range(w)
        for v in ((left + (right - left) * x // max(1, w - 1)),) * 3
    )
    return write_png(path, w, h, lambda y: row, color_type=2)


def gradient16_png(path, w, h):
    """16-bit grayscale ramp for the bit-depth spike."""
    row = b"".join(struct.pack(">H", x * 65535 // max(1, w - 1)) for x in range(w))
    return write_png(path, w, h, lambda y: row, bit_depth=16, color_type=0)


# ----------------------------------------------------------------------
# Minimal OBJ helpers (already in OBJ space: -Z forward, Y up)
# ----------------------------------------------------------------------

def cube_faces(size):
    """Axis-aligned cube centered at origin. Returns (verts, per-face
    quads keyed by axis name)."""
    s = size / 2.0
    v = [(-s, -s, -s), (s, -s, -s), (s, s, -s), (-s, s, -s),
         (-s, -s, s), (s, -s, s), (s, s, s), (-s, s, s)]
    faces = {
        "pz": (5, 6, 7, 8), "nz": (2, 1, 4, 3),
        "px": (2, 3, 7, 6), "nx": (1, 5, 8, 4),
        "py": (4, 8, 7, 3), "ny": (1, 2, 6, 5),
    }
    return v, faces


CUBE_UV = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]


def write_textured_cube_obj(path, mtl_name, material, size=100.0):
    verts, faces = cube_faces(size)
    lines = ["mtllib %s" % mtl_name]
    for v in verts:
        lines.append("v %.4f %.4f %.4f" % v)
    for uv in CUBE_UV:
        lines.append("vt %.1f %.1f" % uv)
    lines.append("o probe_cube")
    lines.append("g probe_cube")
    lines.append("usemtl %s" % material)
    for quad in faces.values():
        lines.append("f " + " ".join("%d/%d" % (vi, i + 1)
                                     for i, vi in enumerate(quad)))
    _write(path, lines)


def _write(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ----------------------------------------------------------------------
# Probes
# ----------------------------------------------------------------------

def probe_mtl_keys():
    """Spikes 1, 2: which MTL keys does E3D read, and the normal-map
    green convention. Every slot gets an unmistakable texture."""
    d = os.path.join(KIT, "1_mtl_keys")
    t = os.path.join(d, "textures")
    solid_png(os.path.join(t, "diffuse_RED.png"), 512, 512, (200, 30, 30))
    solid_png(os.path.join(t, "specular_GREEN.png"), 512, 512, (30, 200, 30))
    hgradient_png(os.path.join(t, "gloss_ramp.png"), 512, 512)
    normal_stripes_png(os.path.join(t, "normal_ridges.png"), 512, 512)
    hgradient_png(os.path.join(t, "opacity_ramp.png"), 512, 512)
    solid_png(os.path.join(t, "emission_BLUE.png"), 512, 512, (30, 60, 220))

    _write(os.path.join(d, "probe.mtl"), [
        "newmtl all_keys",
        "Kd 1.000000 1.000000 1.000000",
        "Ks 1.000000 1.000000 1.000000",
        "Ns 250.000000",
        "d 1.000000",
        "illum 2",
        "map_Kd textures/diffuse_RED.png",
        "map_Ks textures/specular_GREEN.png",
        "map_Ns textures/gloss_ramp.png",
        "map_d textures/opacity_ramp.png",
        "map_Ke textures/emission_BLUE.png",
        "map_bump textures/normal_ridges.png",
        "",
    ])
    write_textured_cube_obj(os.path.join(d, "probe_mtl_keys.obj"),
                            "probe.mtl", "all_keys")

    # Variant using `bump` and `norm` instead of `map_bump`, in case
    # E3D reads a different key for normals.
    _write(os.path.join(d, "probe_alt_bumpkeys.mtl"), [
        "newmtl alt_bump",
        "Kd 0.800000 0.800000 0.800000",
        "illum 2",
        "bump textures/normal_ridges.png",
        "norm textures/normal_ridges.png",
        "",
    ])
    write_textured_cube_obj(os.path.join(d, "probe_alt_bumpkeys.obj"),
                            "probe_alt_bumpkeys.mtl", "alt_bump")


def probe_axis_scale():
    """Spike 6: orientation and scale. A 100-unit cube (= 1 Blender
    meter at scale 100) with a distinct constant color per face."""
    d = os.path.join(KIT, "2_axis_scale")
    verts, faces = cube_faces(100.0)
    legend = {
        "pz": ("FRONT_RED", "1.0 0.1 0.1"),      # Blender -Y (front) -> OBJ +Z
        "nz": ("BACK_CYAN", "0.1 1.0 1.0"),
        "py": ("TOP_WHITE", "1.0 1.0 1.0"),      # Blender +Z (up) -> OBJ +Y
        "ny": ("BOTTOM_DARK", "0.15 0.15 0.15"),
        "px": ("RIGHT_GREEN", "0.1 1.0 0.1"),    # Blender +X -> OBJ +X
        "nx": ("LEFT_MAGENTA", "1.0 0.1 1.0"),
    }
    mtl = []
    for name, color in legend.values():
        mtl += ["newmtl %s" % name, "Kd %s" % color, "illum 2", ""]
    _write(os.path.join(d, "axis.mtl"), mtl)

    lines = ["mtllib axis.mtl"]
    for v in verts:
        lines.append("v %.4f %.4f %.4f" % v)
    lines.append("o axis_cube")
    lines.append("g axis_cube")
    for axis, quad in faces.items():
        lines.append("usemtl %s" % legend[axis][0])
        lines.append("f %d %d %d %d" % quad)
    _write(os.path.join(d, "probe_axis_scale.obj"), lines)


def probe_paths():
    """Spike 4: relative paths, subfolder, spaces, unicode. Three
    planes side by side; a plane only shows its color if its path
    style loads."""
    d = os.path.join(KIT, "3_paths")
    t = os.path.join(d, "textures")
    solid_png(os.path.join(t, "plain_relative.png"), 256, 256, (200, 30, 30))
    solid_png(os.path.join(t, "has space.png"), 256, 256, (30, 200, 30))
    solid_png(os.path.join(t, "tëst_ünicode.png"), 256, 256, (30, 60, 220))

    _write(os.path.join(d, "paths.mtl"), [
        "newmtl relative_subfolder_RED", "illum 2",
        "map_Kd textures/plain_relative.png", "",
        "newmtl space_GREEN", "illum 2",
        "map_Kd textures/has space.png", "",
        "newmtl unicode_BLUE", "illum 2",
        "map_Kd textures/tëst_ünicode.png", "",
    ])

    lines = ["mtllib paths.mtl"]
    mats = ["relative_subfolder_RED", "space_GREEN", "unicode_BLUE"]
    for i, m in enumerate(mats):
        x = (i - 1) * 120.0
        base = i * 4
        for dx, dy in ((-50, -50), (50, -50), (50, 50), (-50, 50)):
            lines.append("v %.1f %.1f 0.0" % (x + dx, dy))
        for uv in CUBE_UV:
            lines.append("vt %.1f %.1f" % uv)
        lines.append("o plane_%s" % m)
        lines.append("g plane_%s" % m)
        lines.append("usemtl %s" % m)
        lines.append("f " + " ".join("%d/%d" % (base + j + 1, base + j + 1)
                                     for j in range(4)))
    _write(os.path.join(d, "probe_paths.obj"), lines)


def probe_mesh_topology():
    """Spike 5: quads and ngons. A quad cube plus a hexagon ngon.
    If E3D renders these correctly, triangulation is optional (we
    triangulate anyway, but good to know)."""
    d = os.path.join(KIT, "4_mesh_topology")
    verts, faces = cube_faces(80.0)
    lines = []
    for v in verts:
        lines.append("v %.4f %.4f %.4f" % v)
    lines.append("o quad_cube")
    lines.append("g quad_cube")
    for quad in faces.values():
        lines.append("f %d %d %d %d" % quad)
    # hexagon ngon to the right
    n = len(verts)
    for i in range(6):
        a = math.pi / 3.0 * i
        lines.append("v %.4f %.4f 0.0" % (150 + 40 * math.cos(a), 40 * math.sin(a)))
    lines.append("o hex_ngon")
    lines.append("g hex_ngon")
    lines.append("f " + " ".join(str(n + i + 1) for i in range(6)))
    _write(os.path.join(d, "probe_mesh_topology.obj"), lines)


def probe_formats():
    """Spike 3: image formats and limits. An 8K PNG and a 16-bit PNG
    on two planes. (Test a TIFF and an EXR from your own files; those
    writers are not worth hand-rolling here.)"""
    d = os.path.join(KIT, "5_formats")
    t = os.path.join(d, "textures")
    print("  writing 8K PNG (a few seconds)...")
    solid_png(os.path.join(t, "big_8k_ORANGE.png"), 8192, 8192, (240, 140, 20))
    gradient16_png(os.path.join(t, "gradient_16bit.png"), 512, 512)

    _write(os.path.join(d, "formats.mtl"), [
        "newmtl eightk_ORANGE", "illum 2",
        "map_Kd textures/big_8k_ORANGE.png", "",
        "newmtl sixteenbit_RAMP", "illum 2",
        "map_Kd textures/gradient_16bit.png", "",
    ])
    lines = ["mtllib formats.mtl"]
    for i, m in enumerate(("eightk_ORANGE", "sixteenbit_RAMP")):
        x = (i * 2 - 1) * 60.0
        base = i * 4
        for dx, dy in ((-50, -50), (50, -50), (50, 50), (-50, 50)):
            lines.append("v %.1f %.1f 0.0" % (x + dx, dy))
        for uv in CUBE_UV:
            lines.append("vt %.1f %.1f" % uv)
        lines.append("o plane_%s" % m)
        lines.append("g plane_%s" % m)
        lines.append("usemtl %s" % m)
        lines.append("f " + " ".join("%d/%d" % (base + j + 1, base + j + 1)
                                     for j in range(4)))
    _write(os.path.join(d, "probe_formats.obj"), lines)


def probe_jsx():
    """Spikes 8, 9: dump the Element effect's matchName and full
    property tree. ES3 only - AE's ExtendScript predates let/arrows."""
    d = os.path.join(KIT, "6_jsx")
    script = r"""// element_probe.jsx - E3D Bridge Phase 0, spikes 8 & 9.
// In AE: make a comp, add a solid, apply Element to it, SELECT that
// layer, then File > Scripts > Run Script File... and pick this file.
// Writes element_probe_report.txt to your Desktop.
(function () {
    function dumpProps(prop, indent, out) {
        var i, child, line;
        for (i = 1; i <= prop.numProperties; i++) {
            child = prop.property(i);
            line = indent + child.name + "  [" + child.matchName + "]";
            if (child.propertyType === PropertyType.PROPERTY) {
                try {
                    line += "  value=" + child.value.toString();
                } catch (e) {
                    line += "  (no simple value)";
                }
                out.push(line);
            } else {
                out.push(line);
                dumpProps(child, indent + "  ", out);
            }
        }
    }

    var comp = app.project.activeItem;
    if (!comp || !(comp instanceof CompItem)) {
        alert("Open a comp first, select the layer with Element applied, then run this again.");
        return;
    }
    if (comp.selectedLayers.length === 0) {
        alert("Select the layer that has Element applied, then run this again.");
        return;
    }
    var layer = comp.selectedLayers[0];
    var effects = layer.property("ADBE Effect Parade");
    if (!effects || effects.numProperties === 0) {
        alert("The selected layer has no effects. Apply Element to it first.");
        return;
    }
    var out = [];
    var i, fx;
    for (i = 1; i <= effects.numProperties; i++) {
        fx = effects.property(i);
        out.push("EFFECT " + i + ": name='" + fx.name + "'  matchName='" + fx.matchName + "'");
        dumpProps(fx, "  ", out);
        out.push("");
    }
    out.push("SPIKE 9 CHECK: search this file for 'path', 'file', 'obj', 'import'.");
    out.push("If no property looks like a model file path, scriptable import is confirmed impossible.");

    var f = new File(Folder.desktop.fsName + "/element_probe_report.txt");
    f.encoding = "UTF-8";
    if (f.open("w")) {
        f.write(out.join("\r\n"));
        f.close();
        alert("Wrote " + f.fsName + "\r\nSend that file back for docs/e3d-material-mapping.md.");
    } else {
        alert("Could not write to Desktop. Output follows in the next alerts.");
        alert(out.join("\r\n").substring(0, 4000));
    }
})();
"""
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "element_probe.jsx"), "w", encoding="utf-8") as f:
        f.write(script)


def main():
    print("Building probe kit in %s" % KIT)
    probe_mtl_keys()
    print("  1_mtl_keys done")
    probe_axis_scale()
    print("  2_axis_scale done")
    probe_paths()
    print("  3_paths done")
    probe_mesh_topology()
    print("  4_mesh_topology done")
    probe_formats()
    print("  5_formats done")
    probe_jsx()
    print("  6_jsx done")
    print("Probe kit ready. Checklist: docs/e3d-material-mapping.md")


if __name__ == "__main__":
    sys.exit(main())
