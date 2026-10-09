"""Orchestrates one export: prep -> analyze -> textures -> OBJ/MTL -> report.

Callable headless (tests) or from the UI operator. Temporary meshes are
always cleaned up, success or failure; the user's scene is never touched.
"""

import os
import re

import bpy

from . import material_analysis, obj_writer, report as report_mod, scene_prep, texture_io


class ExportError(RuntimeError):
    pass


# Part rules: match objects (or their collections) by name fragment.
# separate -> own `LABEL__` prefix, contiguous OBJ placement, and cloned
#             materials, so the parts can be isolated in Element 3D.
# protect  -> keep detail_boost x more triangles when decimating.
# The UI ships these as editable rows; an empty list means use these.
DEFAULT_RULES = [
    {"label": "PROPS", "keywords": ["prop", "rotor", "blade", "spinner"],
     "separate": True, "protect": True},
    {"label": "GIMBAL", "keywords": ["gimbal", "turret"],
     "separate": True, "protect": True},
    {"label": "DETAIL", "keywords": ["cam", "lens", "sensor", "antenna"],
     "separate": False, "protect": True},
]


def _clean_label(label):
    return re.sub(r"[^A-Za-z0-9]+", "", label.upper()) or "PART"


class ExportSettings:
    """Plain settings object so tests do not need the UI PropertyGroup."""

    def __init__(self):
        self.scope = "AUTO"            # AUTO / SELECTED / SCENE
        self.scale = 100.0             # OBJ units per Blender meter
        self.max_texture_size = 2048
        self.png_everything = False    # else JPG for color, PNG for data
        self.flip_normal_green = False
        self.output_dir = ""           # empty -> <blendname>_e3d next to the .blend
        self.decimate_target = 0       # total triangle budget; 0 = no decimation
        # Part rules (see DEFAULT_RULES). tag_parts gates the renaming/
        # separation side; protection applies whenever decimating.
        self.tag_parts = True
        self.part_rules = list(DEFAULT_RULES)
        self.detail_boost = 8.0

    @classmethod
    def from_props(cls, props):
        s = cls()
        s.scope = props.scope
        s.scale = props.scale
        s.max_texture_size = int(props.max_texture_size)
        s.png_everything = props.png_everything
        s.flip_normal_green = props.flip_normal_green
        s.output_dir = props.output_dir
        s.decimate_target = props.decimate_target if props.decimate else 0
        s.tag_parts = props.tag_parts
        s.detail_boost = props.detail_boost
        s.part_rules = []
        for r in props.part_rules:
            kws = [k.strip() for k in r.keywords.split(",") if k.strip()]
            if kws:
                s.part_rules.append({
                    "label": r.label, "keywords": kws,
                    "separate": r.separate, "protect": r.protect,
                })
        if not s.part_rules:
            s.part_rules = list(DEFAULT_RULES)
        return s

    def separation_rules(self):
        """Ordered (LABEL, keywords) for rules that isolate parts."""
        if not self.tag_parts:
            return []
        return [(_clean_label(r["label"]), r["keywords"])
                for r in self.part_rules if r.get("separate")]

    def protection_rules(self):
        """(tag, keywords) for rules that keep decimation quality."""
        return [("PROTECT", r["keywords"])
                for r in self.part_rules if r.get("protect")]

    def summary(self):
        return (
            "settings: scope=%s scale=%g max_tex=%d %s flip_green=%s decimate=%s"
            % (self.scope, self.scale, self.max_texture_size,
               "png_everything" if self.png_everything else "jpg_color/png_data",
               self.flip_normal_green,
               self.decimate_target if self.decimate_target > 0 else "off")
        )


def resolve_output_dir(settings):
    if settings.output_dir:
        return bpy.path.abspath(settings.output_dir)
    if not bpy.data.filepath:
        raise ExportError(
            "Save the .blend first, or choose an output folder in the panel."
        )
    blend_dir = os.path.dirname(bpy.data.filepath)
    stem = os.path.splitext(os.path.basename(bpy.data.filepath))[0]
    return os.path.join(blend_dir, texture_io.sanitize(stem, "export") + "_e3d")


def export_name():
    if bpy.data.filepath:
        stem = os.path.splitext(os.path.basename(bpy.data.filepath))[0]
    else:
        stem = bpy.context.scene.name
    return texture_io.sanitize(stem, "export")


def run(context, settings):
    """Run the full export. Returns a summary dict."""
    report = report_mod.Report()
    out_dir = resolve_output_dir(settings)
    os.makedirs(out_dir, exist_ok=True)
    name = export_name()

    obj_path = os.path.join(out_dir, name + ".obj")
    mtl_path = os.path.join(out_dir, name + ".mtl")
    report_path = os.path.join(out_dir, "report.txt")

    items, skipped = scene_prep.collect_items(context, settings.scope)
    report.skipped.extend(skipped)
    if not items:
        raise ExportError("Nothing to export (no visible mesh-like objects in scope).")

    depsgraph = context.evaluated_depsgraph_get()
    sep_rules = settings.separation_rules()
    prot_rules = settings.protection_rules()

    prepped = []
    temp_meshes = []
    try:
        for item in items:
            po = scene_prep.prep_object(
                item.ob, depsgraph, report, matrix=item.matrix, name=item.name
            )
            if po is not None:
                po.tag = scene_prep.tag_for_names(item.names_chain, sep_rules)
                po.protected = bool(
                    scene_prep.tag_for_names(item.names_chain, prot_rules)
                )
                prepped.append(po)
                temp_meshes.append(po.mesh)
        if not prepped:
            raise ExportError("All objects were skipped; see report.txt.")

        # Tagged parts are written last and contiguous, in rule order,
        # so Scene Setup lists each group together for one-drag selection.
        order = {None: 0}
        for i, (label, _kws) in enumerate(sep_rules):
            order.setdefault(label, i + 1)
        prepped.sort(key=lambda po: order.get(po.tag, len(order)))

        # ----- optional decimation --------------------------------------
        tris_before, tris_after = scene_prep.decimate_prepped(
            prepped, settings.decimate_target, report,
            detail_boost=settings.detail_boost,
        )
        temp_meshes = [po.mesh for po in prepped]

        # ----- materials -------------------------------------------------
        registry = texture_io.TextureRegistry(out_dir, settings, report)
        mat_names = texture_io.UniqueNames()
        analyzed = {}      # material pointer -> MaterialExport (one analysis each)
        mtl_entries = {}   # (pointer, tag) -> mtl name; tagged parts get their
                           # own MTL clone so they never share a material with
                           # the body and can be isolated in Element 3D

        def get_material_name(mat, tag):
            ptr = mat.as_pointer() if mat is not None else 0
            if ptr not in analyzed:
                analyzed[ptr] = material_analysis.analyze(mat)
            key = (ptr, tag)
            if key not in mtl_entries:
                me = analyzed[ptr]
                base = me.name + ("_" + tag if tag else "")
                mtl_entries[key] = mat_names.get(base, "material")
            return mtl_entries[key], analyzed[ptr]

        obj_names = texture_io.UniqueNames()
        slot_material_names = {}
        for po in prepped:
            prefix = (po.tag + "__") if po.tag else ""
            po.export_name = obj_names.get(prefix + po.name, "object")
            if po.tag:
                report.tagged.append((po.export_name, po.tag))
            slots = po.materials if po.materials else [None]
            names = []
            for mat in slots:
                mtl_name, me = get_material_name(mat, po.tag)
                names.append(mtl_name)
                if me.uv_map is not None:
                    layer = obj_writer._render_uv_layer(po.mesh)
                    if layer is None or layer.name != me.uv_map:
                        report.warn(
                            "'%s': material '%s' reads UV map '%s' but the OBJ "
                            "carries the render-active UV map - textures may "
                            "be misplaced (full fix is Phase 2 baking)"
                            % (po.name, me.name, me.uv_map)
                        )
            slot_material_names[id(po)] = names

        if report.tagged:
            labels = sorted({tag for _n, tag in report.tagged})
            report.notes.append(
                "Tagged parts (%s) are prefixed and listed last in the OBJ. "
                "In Element 3D's Scene Setup: box-select all parts of one "
                "prefix and assign them to an E3D Group - then hide or "
                "animate them from the effect's Group folders (rotation for "
                "spinning props, aux channels for aiming). Each tag's "
                "materials are suffixed with its label so nothing is shared "
                "with the rest of the model." % ", ".join(labels)
            )

        # ----- textures + MTL -------------------------------------------
        mtl_materials = []
        reported_ptrs = set()
        for (ptr, tag), mtl_name in mtl_entries.items():
            me = analyzed[ptr]
            maps = {}
            for job in me.maps:
                rel = registry.resolve(job)
                if rel is not None:
                    maps[job.mtl_key] = rel
            mtl_materials.append(
                {"name": mtl_name, "constants": me.constants, "maps": maps}
            )
            if ptr in reported_ptrs:
                continue  # report each source material once, not per clone
            reported_ptrs.add(ptr)
            for w in me.warnings:
                report.warn("material '%s': %s" % (me.name, w))
            if me.classification == material_analysis.PASSTHROUGH:
                detail = ("maps: " + ", ".join(sorted(maps))) if maps else "constants only"
                report.passthrough.append((me.name, detail))
            else:
                report.skipped.append(
                    (me.name, "%s (%s) - exported flat gray"
                     % (me.reason, me.classification.lower()))
                )

        obj_writer.write_mtl(mtl_path, mtl_materials)
        obj_writer.write_obj(
            obj_path, os.path.basename(mtl_path), prepped,
            slot_material_names, settings.scale, report,
        )
    except Exception as exc:
        report.error(str(exc))
        raise
    finally:
        for mesh in temp_meshes:
            try:
                bpy.data.meshes.remove(mesh)
            except Exception:
                pass
        try:
            report.write(report_path, settings.summary())
        except Exception:
            pass

    return {
        "out_dir": out_dir,
        "obj": obj_path,
        "triangles": tris_after,
        "triangles_before_decimate": tris_before,
        "objects": len(prepped),
        "materials": len(analyzed),
        "passthrough": len(report.passthrough),
        "tagged_parts": len(report.tagged),
        "skipped_materials": sum(
            1 for m in analyzed.values()
            if m.classification != material_analysis.PASSTHROUGH
        ),
        "textures": len(report.textures),
        "warnings": len(report.warnings),
        "report": report_path,
    }
