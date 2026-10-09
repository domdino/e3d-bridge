"""N-panel tab "E3D Bridge": one big Export button, collapsed Advanced
settings. Defaults must produce a good result with zero settings changed.
"""

import bpy

from . import export_pipeline


class E3DPartRule(bpy.types.PropertyGroup):
    """One user-editable rule: which parts to isolate and/or keep sharp."""
    label: bpy.props.StringProperty(
        name="Label", default="PART",
        description="Prefix for matched parts in the OBJ (e.g. WHEELS__...) "
                    "and suffix for their cloned materials",
    )
    keywords: bpy.props.StringProperty(
        name="Keywords", default="",
        description="Comma-separated name fragments; matches object OR "
                    "collection names, case-insensitive",
    )
    separate: bpy.props.BoolProperty(
        name="Own Group", default=True,
        description="Give matched parts their own prefix, placement and "
                    "materials so they can be isolated in Element 3D",
    )
    protect: bpy.props.BoolProperty(
        name="Keep Quality", default=True,
        description="Matched parts keep the Detail Quality Boost x more "
                    "triangles when decimating",
    )


class E3D_UL_part_rules(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "label", text="", emboss=False)
        row.prop(item, "keywords", text="")
        row.prop(item, "separate", text="", icon="OUTLINER_COLLECTION")
        row.prop(item, "protect", text="", icon="MOD_DECIM")


class E3D_OT_rule_add(bpy.types.Operator):
    bl_idname = "e3d.rule_add"
    bl_label = "Add Part Rule"
    bl_description = "Add a keyword rule for isolating or protecting parts"

    def execute(self, context):
        props = context.scene.e3d_bridge
        rule = props.part_rules.add()
        rule.label = "PART"
        props.active_rule = len(props.part_rules) - 1
        return {"FINISHED"}


class E3D_OT_rule_remove(bpy.types.Operator):
    bl_idname = "e3d.rule_remove"
    bl_label = "Remove Part Rule"
    bl_description = "Remove the selected rule"

    def execute(self, context):
        props = context.scene.e3d_bridge
        if 0 <= props.active_rule < len(props.part_rules):
            props.part_rules.remove(props.active_rule)
            props.active_rule = min(props.active_rule, len(props.part_rules) - 1)
        return {"FINISHED"}


class E3D_OT_rules_load_defaults(bpy.types.Operator):
    bl_idname = "e3d.rules_load_defaults"
    bl_label = "Load Default Rules"
    bl_description = "Replace the list with the built-in PROPS / GIMBAL / DETAIL rules"

    def execute(self, context):
        props = context.scene.e3d_bridge
        props.part_rules.clear()
        for r in export_pipeline.DEFAULT_RULES:
            rule = props.part_rules.add()
            rule.label = r["label"]
            rule.keywords = ",".join(r["keywords"])
            rule.separate = r["separate"]
            rule.protect = r["protect"]
        props.active_rule = 0
        return {"FINISHED"}


class E3DBridgeSettings(bpy.types.PropertyGroup):
    output_dir: bpy.props.StringProperty(
        name="Output Folder",
        description="Where the OBJ, MTL and textures go. "
                    "Empty = a '<blendname>_e3d' folder next to the .blend",
        subtype="DIR_PATH",
        default="",
    )
    scope: bpy.props.EnumProperty(
        name="Export",
        items=[
            ("AUTO", "Selection (or all)", "Selected objects, or the whole scene if nothing is selected"),
            ("SELECTED", "Selection only", "Only selected objects"),
            ("SCENE", "Whole scene", "Every visible mesh-like object"),
        ],
        default="AUTO",
    )
    scale: bpy.props.FloatProperty(
        name="Scale",
        description="OBJ units per Blender meter (100 matches AE's 100 units/m convention)",
        default=100.0, min=0.001, max=10000.0,
    )
    max_texture_size: bpy.props.EnumProperty(
        name="Max Texture Size",
        items=[(s, s, "") for s in ("512", "1024", "2048", "4096", "8192")],
        default="2048",
    )
    png_everything: bpy.props.BoolProperty(
        name="PNG Everything",
        description="Write every texture as PNG. Off = JPG for color maps, PNG for data maps",
        default=False,
    )
    flip_normal_green: bpy.props.BoolProperty(
        name="Flip Normal Green",
        description="Invert the green channel of normal maps (if E3D expects DirectX-style normals)",
        default=False,
    )
    decimate: bpy.props.BoolProperty(
        name="Decimate Heavy Meshes",
        description="Reduce total triangles to the target below. Small objects are never decimated",
        default=False,
    )
    decimate_target: bpy.props.IntProperty(
        name="Target Triangles",
        description="Total triangle budget for the whole export. The OBJ "
                    "runs ~80 bytes per triangle: 5M triangles ~= 400 MB, "
                    "2M ~= 160 MB",
        default=5000000, min=1000, max=20000000,
    )
    tag_parts: bpy.props.BoolProperty(
        name="Isolate Tagged Parts",
        description="Apply the part rules below: matched objects get "
                    "prefixed names, grouped placement, and their own "
                    "materials so they can be isolated in Element 3D",
        default=True,
    )
    part_rules: bpy.props.CollectionProperty(type=E3DPartRule)
    active_rule: bpy.props.IntProperty(default=0)
    detail_boost: bpy.props.FloatProperty(
        name="Detail Quality Boost",
        description="How many times more triangles protected parts keep "
                    "compared to the body when decimating",
        default=8.0, min=1.0, max=64.0,
    )
    show_advanced: bpy.props.BoolProperty(name="Advanced", default=False)


class E3D_OT_export(bpy.types.Operator):
    bl_idname = "e3d.export"
    bl_label = "Export for Element 3D"
    bl_description = "Write OBJ + MTL + textures that Element 3D imports cleanly"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = context.scene.e3d_bridge
        settings = export_pipeline.ExportSettings.from_props(props)
        try:
            result = export_pipeline.run(context, settings)
        except export_pipeline.ExportError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        msg = (
            "Exported %(objects)d objects, %(materials)d materials "
            "(%(passthrough)d passed through, %(skipped_materials)d skipped), "
            "%(textures)d textures. Report: report.txt" % result
        )
        level = "WARNING" if result["warnings"] or result["skipped_materials"] else "INFO"
        self.report({level}, msg)
        return {"FINISHED"}


class E3D_PT_panel(bpy.types.Panel):
    bl_label = "E3D Bridge"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "E3D Bridge"

    def draw(self, context):
        layout = self.layout
        props = context.scene.e3d_bridge

        col = layout.column()
        big = col.row()
        big.scale_y = 1.6
        big.operator("e3d.export", icon="EXPORT")
        col.prop(props, "output_dir")

        box = layout.box()
        row = box.row()
        row.prop(
            props, "show_advanced",
            icon="TRIA_DOWN" if props.show_advanced else "TRIA_RIGHT",
            emboss=False,
        )
        if props.show_advanced:
            box.prop(props, "scope")
            box.prop(props, "scale")
            box.prop(props, "max_texture_size")
            box.prop(props, "png_everything")
            box.prop(props, "flip_normal_green")
            row = box.row()
            row.prop(props, "decimate")
            sub = row.row()
            sub.enabled = props.decimate
            sub.prop(props, "decimate_target", text="")
            box.prop(props, "tag_parts")
            row = box.row()
            row.template_list("E3D_UL_part_rules", "", props, "part_rules",
                              props, "active_rule", rows=3)
            col = row.column(align=True)
            col.operator("e3d.rule_add", icon="ADD", text="")
            col.operator("e3d.rule_remove", icon="REMOVE", text="")
            col.operator("e3d.rules_load_defaults", icon="FILE_REFRESH", text="")
            if not props.part_rules:
                box.label(text="Empty list = defaults (PROPS, GIMBAL, DETAIL)",
                          icon="INFO")
            if props.decimate:
                box.prop(props, "detail_boost")


_classes = (
    E3DPartRule, E3D_UL_part_rules,
    E3D_OT_rule_add, E3D_OT_rule_remove, E3D_OT_rules_load_defaults,
    E3DBridgeSettings, E3D_OT_export, E3D_PT_panel,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.e3d_bridge = bpy.props.PointerProperty(type=E3DBridgeSettings)


def unregister():
    del bpy.types.Scene.e3d_bridge
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
