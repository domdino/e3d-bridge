"""Build the Phase 1 test corpus programmatically, inside Blender.

Each builder resets to an empty file, constructs a small scene, and
saves a .blend under tests/samples/ (kept under 5 MB - textures are
generated datablocks, not files). Imported by tests/run.py; can also
be run alone:

    blender --background --python tests/make_samples.py
"""

import os

import bpy

SAMPLES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples")


def _reset():
    bpy.ops.wm.read_homefile(use_empty=True)


def _save(name):
    os.makedirs(SAMPLES_DIR, exist_ok=True)
    path = os.path.join(SAMPLES_DIR, name + ".blend")
    bpy.ops.wm.save_as_mainfile(filepath=path, compress=True)
    return path


def _new_image(name, color=(0.8, 0.2, 0.2, 1.0), size=256, grid=False, non_color=False):
    img = bpy.data.images.new(name, size, size)
    img.generated_type = "COLOR_GRID" if grid else "BLANK"
    img.generated_color = color
    if non_color:
        img.colorspace_settings.name = "Non-Color"
    return img


def _principled_material(name):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
    return mat, bsdf


def _tex_node(mat, image):
    node = mat.node_tree.nodes.new("ShaderNodeTexImage")
    node.image = image
    return node


def build_cube_imagetex():
    """Passthrough: image diffuse + roughness + normal map."""
    _reset()
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    mat, bsdf = _principled_material("imagetex")
    nt = mat.node_tree

    diff = _tex_node(mat, _new_image("diff_grid", grid=True))
    nt.links.new(diff.outputs["Color"], bsdf.inputs["Base Color"])

    rough = _tex_node(mat, _new_image("rough_map", (0.6, 0.6, 0.6, 1.0), non_color=True))
    nt.links.new(rough.outputs["Color"], bsdf.inputs["Roughness"])

    nrm_img = _new_image("normal_map", (0.5, 0.5, 1.0, 1.0), non_color=True)
    nrm_tex = _tex_node(mat, nrm_img)
    nrm_node = nt.nodes.new("ShaderNodeNormalMap")
    nt.links.new(nrm_tex.outputs["Color"], nrm_node.inputs["Color"])
    nt.links.new(nrm_node.outputs["Normal"], bsdf.inputs["Normal"])

    cube.data.materials.append(mat)
    return _save("cube_imagetex")


def build_suzanne_procedural():
    """Bake-class: Noise -> ColorRamp -> Base Color, Voronoi -> Bump."""
    _reset()
    bpy.ops.mesh.primitive_monkey_add()
    monkey = bpy.context.active_object
    mat, bsdf = _principled_material("procedural")
    nt = mat.node_tree

    noise = nt.nodes.new("ShaderNodeTexNoise")
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    nt.links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])

    voronoi = nt.nodes.new("ShaderNodeTexVoronoi")
    bump = nt.nodes.new("ShaderNodeBump")
    nt.links.new(voronoi.outputs["Distance"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    monkey.data.materials.append(mat)
    return _save("suzanne_procedural")


def build_multimat():
    """One cube, four slots: constant, image, procedural, emission."""
    _reset()
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object

    m1, b1 = _principled_material("constant_blue")
    b1.inputs["Base Color"].default_value = (0.1, 0.2, 0.9, 1.0)

    m2, b2 = _principled_material("image_red")
    tex = _tex_node(m2, _new_image("red_img", (0.9, 0.1, 0.1, 1.0)))
    m2.node_tree.links.new(tex.outputs["Color"], b2.inputs["Base Color"])

    m3, b3 = _principled_material("procedural_noise")
    noise = m3.node_tree.nodes.new("ShaderNodeTexNoise")
    m3.node_tree.links.new(noise.outputs["Color"], b3.inputs["Base Color"])

    m4 = bpy.data.materials.new("emission_shader")
    m4.use_nodes = True
    nt = m4.node_tree
    for n in list(nt.nodes):
        if n.type != "OUTPUT_MATERIAL":
            nt.nodes.remove(n)
    emit = nt.nodes.new("ShaderNodeEmission")
    emit.inputs["Color"].default_value = (0.0, 1.0, 0.5, 1.0)
    emit.inputs["Strength"].default_value = 2.0
    out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
    nt.links.new(emit.outputs["Emission"], out.inputs["Surface"])

    for m in (m1, m2, m3, m4):
        cube.data.materials.append(m)
    for i, poly in enumerate(cube.data.polygons):
        poly.material_index = i % 4
    return _save("multimat")


def build_coat_and_mapping():
    """Production-style material: image diffuse routed through TexCoord ->
    identity Mapping, plus a procedural Coat Roughness. Must still be
    passthrough (coat ignored with a warning, mapping seen through)."""
    _reset()
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    mat, bsdf = _principled_material("coat_mapping")
    nt = mat.node_tree

    texcoord = nt.nodes.new("ShaderNodeTexCoord")
    mapping = nt.nodes.new("ShaderNodeMapping")
    diff = _tex_node(mat, _new_image("mapped_diff", grid=True))
    nt.links.new(texcoord.outputs["UV"], mapping.inputs["Vector"])
    nt.links.new(mapping.outputs["Vector"], diff.inputs["Vector"])
    nt.links.new(diff.outputs["Color"], bsdf.inputs["Base Color"])

    noise = nt.nodes.new("ShaderNodeTexNoise")
    nt.links.new(noise.outputs["Fac"], bsdf.inputs["Coat Roughness"])

    cube.data.materials.append(mat)
    return _save("coat_and_mapping")


def build_real_mapping_bakes():
    """A Mapping node with a real transform must still force a bake."""
    _reset()
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    mat, bsdf = _principled_material("scaled_mapping")
    nt = mat.node_tree
    mapping = nt.nodes.new("ShaderNodeMapping")
    mapping.inputs["Scale"].default_value = (2.0, 2.0, 2.0)
    diff = _tex_node(mat, _new_image("scaled_diff", grid=True))
    nt.links.new(mapping.outputs["Vector"], diff.inputs["Vector"])
    nt.links.new(diff.outputs["Color"], bsdf.inputs["Base Color"])
    cube.data.materials.append(mat)
    return _save("real_mapping_bakes")


def build_bump_height():
    """Production-style normal chain: image height map through a Bump node.
    Must pass through with the height converted to a normal map."""
    _reset()
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    mat, bsdf = _principled_material("bump_height")
    nt = mat.node_tree

    diff = _tex_node(mat, _new_image("bh_diff", grid=True))
    nt.links.new(diff.outputs["Color"], bsdf.inputs["Base Color"])

    height = _tex_node(mat, _new_image("bh_height", (0.5, 0.5, 0.5, 1.0), non_color=True))
    bump = nt.nodes.new("ShaderNodeBump")
    nt.links.new(height.outputs["Color"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    cube.data.materials.append(mat)
    return _save("bump_height")


def build_heavy_decimate():
    """Subdivided cube (~12k tris) for the decimation path."""
    _reset()
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    sub = cube.modifiers.new("Subdivision", "SUBSURF")
    sub.levels = 5
    sub.render_levels = 5
    return _save("heavy_decimate")


def build_collection_instances():
    """Linked-asset pattern: a source collection (cube + a NESTED
    collection instance of a cone) instanced twice via empties. The
    source collections are not in the scene - only the empties are.
    Must export 4 objects with composed transforms."""
    _reset()

    def unlink_everywhere(ob):
        for c in list(ob.users_collection):
            c.objects.unlink(ob)

    inner = bpy.data.collections.new("inner_asset")
    bpy.ops.mesh.primitive_cone_add()
    cone = bpy.context.active_object
    unlink_everywhere(cone)
    inner.objects.link(cone)

    src = bpy.data.collections.new("asset_src")
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    unlink_everywhere(cube)
    src.objects.link(cube)
    nested = bpy.data.objects.new("nested_inst", None)
    nested.instance_type = "COLLECTION"
    nested.instance_collection = inner
    nested.location = (0.0, 3.0, 0.0)
    src.objects.link(nested)

    for i, x in enumerate((0.0, 6.0)):
        e = bpy.data.objects.new("asset_inst_%d" % i, None)
        e.instance_type = "COLLECTION"
        e.instance_collection = src
        e.location = (x, 0.0, 0.0)
        bpy.context.scene.collection.objects.link(e)
    return _save("collection_instances")


def build_excluded_collection():
    """A cube in a view-layer-excluded collection must NOT export."""
    _reset()
    bpy.ops.mesh.primitive_cube_add()
    bpy.context.active_object.name = "kept_cube"
    hidden_coll = bpy.data.collections.new("excluded_stuff")
    bpy.context.scene.collection.children.link(hidden_coll)
    bpy.ops.mesh.primitive_cube_add(location=(4.0, 0.0, 0.0))
    gone = bpy.context.active_object
    gone.name = "excluded_cube"
    for c in list(gone.users_collection):
        c.objects.unlink(gone)
    hidden_coll.objects.link(gone)
    bpy.context.view_layer.layer_collection.children["excluded_stuff"].exclude = True
    return _save("excluded_collection")


def build_custom_rule():
    """Body + wheel sharing one material; exported with a user-defined
    'Wheels' rule instead of the defaults."""
    _reset()
    bpy.ops.mesh.primitive_cube_add()
    body = bpy.context.active_object
    body.name = "body"
    bpy.ops.mesh.primitive_cube_add(location=(3.0, 0.0, 0.0))
    wheel = bpy.context.active_object
    wheel.name = "wheel_back_L"
    mat, bsdf = _principled_material("shared_paint")
    for ob in (body, wheel):
        ob.data.materials.append(mat)
    return _save("custom_rule")


def build_protected_decimate():
    """Two equally heavy cubes; the gimbal-named one must survive
    decimation with several times more triangles than the plain one."""
    _reset()
    for name, x in (("plain_cube", 0.0), ("mesh_gimbal_hd", 4.0)):
        bpy.ops.mesh.primitive_cube_add(location=(x, 0.0, 0.0))
        ob = bpy.context.active_object
        ob.name = name
        sub = ob.modifiers.new("Subdivision", "SUBSURF")
        sub.levels = 4
        sub.render_levels = 4
    return _save("protected_decimate")


def build_props_gimbal_tags():
    """Body + a prop (matched via its collection name) + a gimbal
    (matched via object name), all sharing ONE material. Tagged parts
    must get cloned materials and prefixed, trailing object names."""
    _reset()
    bpy.ops.mesh.primitive_cube_add()
    body = bpy.context.active_object
    body.name = "body"

    bpy.ops.mesh.primitive_cube_add(location=(3.0, 0.0, 0.0))
    blade = bpy.context.active_object
    blade.name = "part_new_001"  # neutral name: matched ONLY via its collection
    coll = bpy.data.collections.new("coll_props_placeholder")
    bpy.context.scene.collection.children.link(coll)
    for c in blade.users_collection:
        c.objects.unlink(blade)
    coll.objects.link(blade)

    bpy.ops.mesh.primitive_cube_add(location=(6.0, 0.0, 0.0))
    gimbal = bpy.context.active_object
    gimbal.name = "mesh_gimbal_01"

    mat, bsdf = _principled_material("shared_gray")
    bsdf.inputs["Base Color"].default_value = (0.5, 0.5, 0.5, 1.0)
    for ob in (body, blade, gimbal):
        ob.data.materials.append(mat)
    return _save("props_gimbal_tags")


def build_modifiers():
    """Subdivision + Mirror + Array must all be applied on export."""
    _reset()
    bpy.ops.mesh.primitive_cube_add(location=(2.0, 0.0, 0.0))
    cube = bpy.context.active_object
    sub = cube.modifiers.new("Subdivision", "SUBSURF")
    sub.levels = 1
    sub.render_levels = 1
    cube.modifiers.new("Mirror", "MIRROR")
    arr = cube.modifiers.new("Array", "ARRAY")
    arr.count = 2
    return _save("modifiers")


def build_curve_and_text():
    """Non-mesh conversion: beveled curve + text object."""
    _reset()
    bpy.ops.curve.primitive_bezier_circle_add()
    curve = bpy.context.active_object
    curve.data.bevel_depth = 0.1
    bpy.ops.object.text_add(location=(0.0, 0.0, 2.0))
    txt = bpy.context.active_object
    txt.data.extrude = 0.05
    return _save("curve_and_text")


def build_hidden_objects():
    """Hidden / disabled objects must be skipped."""
    _reset()
    bpy.ops.mesh.primitive_cube_add()
    bpy.context.active_object.name = "visible_cube"
    bpy.ops.mesh.primitive_cube_add(location=(3.0, 0.0, 0.0))
    hidden = bpy.context.active_object
    hidden.name = "hidden_cube"
    hidden.hide_render = True
    bpy.ops.mesh.primitive_cube_add(location=(6.0, 0.0, 0.0))
    disabled = bpy.context.active_object
    disabled.name = "disabled_cube"
    disabled.hide_viewport = True
    return _save("hidden_objects")


def build_no_uvs():
    """No UV map, constant material - must still export (f v//vn)."""
    _reset()
    bpy.ops.mesh.primitive_cube_add(calc_uvs=False)
    cube = bpy.context.active_object
    mat, bsdf = _principled_material("plain")
    bsdf.inputs["Base Color"].default_value = (0.2, 0.7, 0.3, 1.0)
    cube.data.materials.append(mat)
    return _save("no_uvs")


BUILDERS = {
    "cube_imagetex": build_cube_imagetex,
    "suzanne_procedural": build_suzanne_procedural,
    "multimat": build_multimat,
    "coat_and_mapping": build_coat_and_mapping,
    "real_mapping_bakes": build_real_mapping_bakes,
    "bump_height": build_bump_height,
    "heavy_decimate": build_heavy_decimate,
    "props_gimbal_tags": build_props_gimbal_tags,
    "protected_decimate": build_protected_decimate,
    "custom_rule": build_custom_rule,
    "collection_instances": build_collection_instances,
    "excluded_collection": build_excluded_collection,
    "modifiers": build_modifiers,
    "curve_and_text": build_curve_and_text,
    "hidden_objects": build_hidden_objects,
    "no_uvs": build_no_uvs,
}


def build_all():
    paths = {}
    for name, fn in BUILDERS.items():
        paths[name] = fn()
        print("built sample:", paths[name])
    return paths


if __name__ == "__main__":
    build_all()
