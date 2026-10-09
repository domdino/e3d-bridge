"""Custom OBJ + MTL writer.

We do not use bpy.ops.wm.obj_export because we need full control over
MTL keys, texture paths, and group structure, and we write glossiness
(inverted roughness) into map_Ns, which the built-in exporter will not.

Everything E3D-specific about the MTL lives in E3D_MTL below, so an
Element update that changes parsing means editing one dict.
"""

from . import coords

# One `o` block per Blender object, `usemtl` per material slot.
# Phase 0 spike 7 tells us whether E3D groups by o / g / usemtl; we
# write both `o` and `g` with the same name so either works.

E3D_MTL = {
    # constant keys we may emit per material
    "diffuse": "Kd",
    "specular": "Ks",
    "gloss_exponent": "Ns",
    "opacity": "d",
    "emission": "Ke",
    "illum": "illum 2",
    # map key order in the file (stable output for tests/diffs)
    "map_order": ["map_Kd", "map_Ks", "map_Ns", "map_d", "map_Ke", "map_bump"],
}


def write_mtl(filepath, materials):
    """materials: list of dicts {name, constants, maps} where maps is
    {mtl_key: relative_path}."""
    lines = ["# E3D Bridge material library", ""]
    for mat in materials:
        lines.append("newmtl %s" % mat["name"])
        c = mat["constants"]
        kd = c.get("Kd", (0.8, 0.8, 0.8))
        lines.append("Kd %.6f %.6f %.6f" % tuple(kd))
        if "Ks" in c:
            lines.append("Ks %.6f %.6f %.6f" % tuple(c["Ks"]))
        if "Ns" in c:
            lines.append("Ns %.6f" % c["Ns"])
        if "d" in c:
            lines.append("d %.6f" % c["d"])
        if "Ke" in c:
            lines.append("Ke %.6f %.6f %.6f" % tuple(c["Ke"]))
        lines.append(E3D_MTL["illum"])
        for key in E3D_MTL["map_order"]:
            if key in mat["maps"]:
                lines.append("%s %s" % (key, mat["maps"][key]))
        lines.append("")
    with open(filepath, "w", encoding="ascii") as f:
        f.write("\n".join(lines))


def write_obj(filepath, mtl_filename, prepped, slot_material_names, scale, report):
    """Write the OBJ.

    prepped: list of PreppedObject (world-space, triangulated meshes).
    slot_material_names: {id(prepped_object): [mtl name per slot]} -
        every mesh face's material_index resolves through this.
    """
    out = []
    out.append("# E3D Bridge OBJ export")
    out.append("mtllib %s" % mtl_filename)

    v_count = 0
    vt_count = 0
    vn_count = 0
    ext_min = [float("inf")] * 3
    ext_max = [float("-inf")] * 3

    for po in prepped:
        mesh = po.mesh
        obj_name = po.export_name
        out.append("o %s" % obj_name)
        out.append("g %s" % obj_name)

        # vertices (%.4f: at scale 100 that is 1 micrometer - plenty,
        # and it keeps multi-million-triangle OBJs markedly smaller)
        for vert in mesh.vertices:
            x, y, z = coords.vert_to_obj(vert.co, scale)
            out.append("v %.4f %.4f %.4f" % (x, y, z))
            if x < ext_min[0]: ext_min[0] = x
            if x > ext_max[0]: ext_max[0] = x
            if y < ext_min[1]: ext_min[1] = y
            if y > ext_max[1]: ext_max[1] = y
            if z < ext_min[2]: ext_min[2] = z
            if z > ext_max[2]: ext_max[2] = z

        # UVs and normals are per-loop; dedupe rounded values.
        uv_layer = _render_uv_layer(mesh)
        has_uv = uv_layer is not None

        vt_index = {}
        loop_vt = [0] * len(mesh.loops)
        if has_uv:
            uvs = uv_layer.uv
            for li in range(len(mesh.loops)):
                u, v = uvs[li].vector
                key = (round(u, 6), round(v, 6))
                idx = vt_index.get(key)
                if idx is None:
                    idx = len(vt_index) + 1 + vt_count
                    vt_index[key] = idx
                    out.append("vt %.6f %.6f" % key)
                loop_vt[li] = idx

        vn_index = {}
        loop_vn = [0] * len(mesh.loops)
        normals = mesh.corner_normals
        for li in range(len(mesh.loops)):
            nx, ny, nz = coords.normal_to_obj(normals[li].vector)
            key = (round(nx, 4), round(ny, 4), round(nz, 4))
            idx = vn_index.get(key)
            if idx is None:
                idx = len(vn_index) + 1 + vn_count
                vn_index[key] = idx
                out.append("vn %.4f %.4f %.4f" % key)
            loop_vn[li] = idx

        # faces grouped by material slot; the pipeline guarantees at
        # least one entry per object (a default material if needed)
        mat_names = slot_material_names[id(po)]
        polys = sorted(mesh.polygons, key=lambda p: p.material_index)
        current_mtl = None
        for poly in polys:
            slot = poly.material_index if poly.material_index < len(mat_names) else 0
            name = mat_names[slot]
            if name != current_mtl:
                out.append("usemtl %s" % name)
                current_mtl = name
            face = ["f"]
            for li in poly.loop_indices:
                vi = mesh.loops[li].vertex_index + 1 + v_count
                if has_uv:
                    face.append("%d/%d/%d" % (vi, loop_vt[li], loop_vn[li]))
                else:
                    face.append("%d//%d" % (vi, loop_vn[li]))
            out.append(" ".join(face))

        v_count += len(mesh.vertices)
        vt_count += len(vt_index)
        vn_count += len(vn_index)

        report.objects.append(
            (po.name, "%d tris, %d verts%s" % (
                len(mesh.polygons), len(mesh.vertices),
                "" if has_uv else ", no UVs"))
        )

    if v_count:
        size = [ext_max[i] - ext_min[i] for i in range(3)]
        report.notes.append(
            "scene extent: %.0f x %.0f x %.0f units" % tuple(size)
        )
        if max(size) > 50000:
            report.warn(
                "the export spans %.1f km - distant environment objects "
                "(sky domes, clouds, backdrops) make viewers frame the "
                "model invisibly small and can look like an empty import. "
                "Consider exporting just the hero asset (selection scope, "
                "or --match on the CLI)." % (max(size) / 100000.0)
            )

    with open(filepath, "w", encoding="ascii") as f:
        f.write("\n".join(out))


def _render_uv_layer(mesh):
    for layer in mesh.uv_layers:
        if layer.active_render:
            return layer
    return mesh.uv_layers[0] if len(mesh.uv_layers) else None
