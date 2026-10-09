"""Headless test runner. From the repo root:

    blender --background --python tests/run.py

Builds the sample .blends, exports each through the real pipeline,
validates the output with validate_output.py, and asserts per-sample
expectations. Exits non-zero on any failure.
"""

import os
import shutil
import sys
import traceback

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)
for p in (ROOT, TESTS_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import bpy  # noqa: E402

import make_samples  # noqa: E402
import validate_output  # noqa: E402

import e3d_bridge  # noqa: E402
from e3d_bridge import export_pipeline  # noqa: E402

# Register the add-on (panel, operators, properties): catches UI
# registration bugs headless, before a human ever installs the zip.
e3d_bridge.register()

OUT_ROOT = os.path.join(TESTS_DIR, "out")


def export_current_scene(name, **overrides):
    settings = export_pipeline.ExportSettings()
    settings.scope = "SCENE"
    settings.output_dir = os.path.join(OUT_ROOT, name)
    for key, val in overrides.items():
        setattr(settings, key, val)
    return export_pipeline.run(bpy.context, settings), settings


def check(cond, msg, failures):
    if not cond:
        failures.append(msg)


def run_case(name, builder, expect, overrides=None):
    """Build sample -> export -> validate -> assertions. Returns [] on pass."""
    failures = []
    builder()
    out_dir = os.path.join(OUT_ROOT, name)
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)

    try:
        summary, settings = export_current_scene(name, **(overrides or {}))
    except Exception:
        return ["export raised:\n" + traceback.format_exc()]

    errs = validate_output.validate(out_dir, max_texture_size=settings.max_texture_size)
    failures.extend("validate: " + e for e in errs)

    st = validate_output.stats(out_dir)
    if "objects" in expect:
        check(st["objects"] == expect["objects"],
              "expected %d objects, got %d" % (expect["objects"], st["objects"]), failures)
    if "min_faces" in expect:
        check(st["faces"] >= expect["min_faces"],
              "expected >= %d faces, got %d" % (expect["min_faces"], st["faces"]), failures)
    if "max_faces" in expect:
        check(st["faces"] <= expect["max_faces"],
              "expected <= %d faces, got %d" % (expect["max_faces"], st["faces"]), failures)
    if "faces" in expect:
        check(st["faces"] == expect["faces"],
              "expected %d faces, got %d" % (expect["faces"], st["faces"]), failures)
    if "has_vt" in expect:
        check(st["has_vt"] == expect["has_vt"],
              "expected has_vt=%s" % expect["has_vt"], failures)
    if "object_names" in expect:
        for wanted in expect["object_names"]:
            check(wanted in st["object_names"],
                  "expected object '%s' in OBJ, got %s" % (wanted, st["object_names"]),
                  failures)
    if "faces_ratio_gt" in expect:
        big, small_o, factor = expect["faces_ratio_gt"]
        fb = st["faces_by_object"].get(big, 0)
        fs = st["faces_by_object"].get(small_o, 1)
        check(fb > fs * factor,
              "expected %s (%d faces) > %gx %s (%d faces)"
              % (big, fb, factor, small_o, fs), failures)
    if "last_objects_tagged" in expect:
        tail = st["object_names"][-expect["last_objects_tagged"]:]
        check(all("__" in n and n.split("__", 1)[0].isupper() for n in tail),
              "expected last %d objects tagged (LABEL__ prefix), got %s"
              % (expect["last_objects_tagged"], st["object_names"]), failures)
    if "materials" in expect:
        check(len(st["materials"]) == expect["materials"],
              "expected %d materials used, got %d (%s)"
              % (expect["materials"], len(st["materials"]), sorted(st["materials"])),
              failures)
    if "passthrough" in expect:
        check(summary["passthrough"] == expect["passthrough"],
              "expected %d passthrough materials, got %d"
              % (expect["passthrough"], summary["passthrough"]), failures)
    if "skipped_materials" in expect:
        check(summary["skipped_materials"] == expect["skipped_materials"],
              "expected %d skipped materials, got %d"
              % (expect["skipped_materials"], summary["skipped_materials"]), failures)
    if "textures" in expect:
        check(summary["textures"] == expect["textures"],
              "expected %d textures, got %d"
              % (expect["textures"], summary["textures"]), failures)
    return failures


CASES = [
    # (sample name, expectations)
    ("cube_imagetex", dict(objects=1, faces=12, has_vt=True,
                           passthrough=1, skipped_materials=0, textures=3)),
    ("suzanne_procedural", dict(objects=1, min_faces=400,
                                passthrough=0, skipped_materials=1, textures=0)),
    ("multimat", dict(objects=1, faces=12, materials=4,
                      passthrough=3, skipped_materials=1, textures=1)),
    ("coat_and_mapping", dict(objects=1, faces=12, has_vt=True,
                              passthrough=1, skipped_materials=0, textures=1)),
    ("real_mapping_bakes", dict(objects=1, faces=12,
                                passthrough=0, skipped_materials=1, textures=0)),
    ("bump_height", dict(objects=1, faces=12, passthrough=1,
                         skipped_materials=0, textures=2)),
    ("heavy_decimate", dict(objects=1, min_faces=800, max_faces=2600),
     dict(decimate_target=2000)),
    ("props_gimbal_tags", dict(objects=3, materials=3, passthrough=1,
                               object_names=["body", "PROPS__part_new_001",
                                             "GIMBAL__mesh_gimbal_01"],
                               last_objects_tagged=2)),
    ("protected_decimate", dict(objects=2, min_faces=1200, max_faces=2600,
                                faces_ratio_gt=("GIMBAL__mesh_gimbal_hd",
                                                "plain_cube", 2.5)),
     dict(decimate_target=2000)),
    ("collection_instances", dict(objects=4, min_faces=100, materials=1)),
    ("excluded_collection", dict(objects=1, faces=12,
                                 object_names=["kept_cube"])),
    ("custom_rule", dict(objects=2, materials=2, passthrough=1,
                         object_names=["body", "WHEELS__wheel_back_L"],
                         last_objects_tagged=1),
     dict(part_rules=[{"label": "Wheels", "keywords": ["wheel"],
                       "separate": True, "protect": False}])),
    ("modifiers", dict(objects=1, min_faces=96)),
    ("curve_and_text", dict(objects=2, min_faces=1)),
    ("hidden_objects", dict(objects=1, faces=12)),
    ("no_uvs", dict(objects=1, faces=12, has_vt=False, passthrough=1)),
]


def main():
    os.makedirs(OUT_ROOT, exist_ok=True)
    results = []
    for case in CASES:
        name, expect = case[0], case[1]
        overrides = case[2] if len(case) > 2 else None
        builder = make_samples.BUILDERS[name]
        failures = run_case(name, builder, expect, overrides)
        results.append((name, failures))
        status = "PASS" if not failures else "FAIL"
        print("[%s] %s" % (status, name))
        for f in failures:
            print("       - %s" % f)

    failed = [n for n, f in results if f]
    print()
    print("%d/%d cases passed" % (len(results) - len(failed), len(results)))
    if failed:
        print("failed:", ", ".join(failed))
        sys.exit(1)


main()
