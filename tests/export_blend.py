"""Export any .blend through E3D Bridge, headless. Usage:

    blender --background --python tests/export_blend.py -- \
        "/path/to/file.blend" [output_dir] [--decimate TRIANGLES]

Opens the file read-only (never saves), exports the whole scene with
default settings, prints the summary and the report. Output defaults
to '<blendname>_e3d/' next to the .blend. --decimate sets a total
triangle budget for the export (e.g. --decimate 500000).
"""

import os
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bpy  # noqa: E402

from e3d_bridge import export_pipeline  # noqa: E402


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    decimate = 0
    if "--decimate" in argv:
        i = argv.index("--decimate")
        decimate = int(argv[i + 1])
        del argv[i:i + 2]
    match = []
    if "--match" in argv:
        # Exact scene-object names (comma-separated) to export instead of
        # the whole scene - e.g. the instancer empties of one asset:
        #   --match "coll_robot_01,coll_antenna_01"
        i = argv.index("--match")
        match = [m.strip() for m in argv[i + 1].split(",") if m.strip()]
        del argv[i:i + 2]
    if not argv:
        print("usage: blender --background --python tests/export_blend.py -- "
              "file.blend [out_dir] [--decimate TRIANGLES]")
        sys.exit(2)
    blend = os.path.abspath(argv[0])
    if not os.path.isfile(blend):
        print("no such file:", blend)
        sys.exit(2)

    print("opening:", blend)
    bpy.ops.wm.open_mainfile(filepath=blend, load_ui=False)

    settings = export_pipeline.ExportSettings()
    settings.scope = "SCENE"
    settings.decimate_target = decimate
    if match:
        in_layer = {o.name for o in bpy.context.view_layer.objects}
        in_scene = {o.name for o in bpy.context.scene.objects}
        missing = [m for m in match if m not in in_scene]
        if missing:
            print("--match names not found in scene:", ", ".join(missing))
            sys.exit(2)
        excluded = [m for m in match if m not in in_layer]
        if excluded:
            print("--match names excluded from the view layer, skipping:",
                  ", ".join(excluded))
        found = 0
        for ob in bpy.context.view_layer.objects:
            hit = ob.name in match
            ob.select_set(hit)
            found += hit
        if not found:
            print("--match selected nothing exportable")
            sys.exit(2)
        settings.scope = "SELECTED"
        print("matched %d scene objects" % found)
    if len(argv) > 1:
        settings.output_dir = os.path.abspath(argv[1])

    result = export_pipeline.run(bpy.context, settings)
    print("\n=== EXPORT SUMMARY ===")
    for key in ("out_dir", "obj", "triangles_before_decimate", "triangles",
                "objects", "materials", "passthrough",
                "skipped_materials", "textures", "warnings", "report"):
        print("%s: %s" % (key, result[key]))
    print("\n=== REPORT ===")
    with open(result["report"], "r", encoding="utf-8") as f:
        print(f.read())


main()
