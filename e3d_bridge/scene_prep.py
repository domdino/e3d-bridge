"""Build export-ready temporary meshes. Never touches the user's data.

For every exportable object we create a NEW mesh datablock from the
evaluated (modifiers applied) object, triangulate it, and bake the
object's world transform into the vertices. Those temp meshes are
removed in the pipeline's finally block, success or not.
"""

import bmesh
import bpy
from mathutils import Matrix, Vector

# Object types that are, or become, a mesh.
EXPORTABLE_TYPES = {"MESH", "CURVE", "SURFACE", "FONT", "META"}

# How deep collection instances may nest (guards against pathological
# setups; Blender itself forbids true cycles).
MAX_INSTANCE_DEPTH = 8


class PreppedObject:
    def __init__(self, name, mesh, materials):
        self.name = name          # original object name
        self.mesh = mesh          # temporary, triangulated, world-space mesh
        self.materials = materials  # list of bpy Materials (slot order), may be empty
        self.tag = None           # 'PROPS' / 'GIMBAL' / None, see tag_for_object()
        self.protected = False    # keep extra decimation quality (tagged/detail)


def _collection_ancestry(scene):
    """Map each object name -> every collection name above it (any depth)."""
    result = {}

    def walk(coll, trail):
        trail = trail + [coll.name]
        for ob in coll.objects:
            result.setdefault(ob.name, set()).update(trail)
        for child in coll.children:
            walk(child, trail)

    walk(scene.collection, [])
    return result


def tag_for_names(names, tag_keywords):
    """Return the first tag whose keywords match any of `names`
    (case-insensitive substring). For a plain object that is its name
    plus its collection ancestry; for an instanced object it also
    includes every instancer empty and collection along the chain.

    tag_keywords: ordered list of (TAG, [keyword, ...]).
    """
    haystacks = [n.lower() for n in names if n]
    for tag, keywords in tag_keywords:
        for kw in keywords:
            kw = kw.strip().lower()
            if kw and any(kw in h for h in haystacks):
                return tag
    return None


class ExportItem:
    """One thing to export: an object plus the world matrix to bake in
    (identity chain for plain objects; instancer-composed for objects
    reached through collection instances) and the name chain used for
    part-rule matching."""

    def __init__(self, ob, matrix, name, names_chain):
        self.ob = ob
        self.matrix = matrix
        self.name = name
        self.names_chain = names_chain


def _expand(ob, matrix, name_chain, match_chain, out, skipped, depth=0):
    """name_chain: instancer empties only (prefixes the export name, so a
    plain scene object keeps its bare name). match_chain: everything a
    part rule may match against (collections, instancers)."""
    if ob.hide_render or ob.hide_viewport:
        skipped.append((ob.name, "hidden or disabled"))
        return
    if ob.type in EXPORTABLE_TYPES:
        name = ("%s_%s" % (name_chain[-1], ob.name)) if name_chain else ob.name
        out.append(ExportItem(ob, matrix, name, list(match_chain) + [ob.name]))
    elif (ob.type == "EMPTY" and ob.instance_type == "COLLECTION"
          and ob.instance_collection is not None):
        if depth >= MAX_INSTANCE_DEPTH:
            skipped.append((ob.name, "collection instances nested deeper than %d"
                            % MAX_INSTANCE_DEPTH))
            return
        coll = ob.instance_collection
        base = matrix @ Matrix.Translation(-Vector(coll.instance_offset))
        # NOTE: an object that is BOTH in the scene and inside an
        # instanced collection would export twice; unusual, unhandled.
        for inner in coll.all_objects:
            _expand(inner, base @ inner.matrix_world,
                    list(name_chain) + [ob.name],
                    list(match_chain) + [ob.name, coll.name],
                    out, skipped, depth + 1)


def collect_items(context, scope):
    """Pick everything to export as ExportItems.

    scope: 'AUTO'    - selection if anything is selected, else whole scene
           'SELECTED' - selection only
           'SCENE'   - whole scene
    Hidden objects are skipped. EMPTY objects that instance a collection
    (linked assets - the standard production setup) are expanded into
    their contents with composed transforms, recursively.
    """
    if scope == "SELECTED" or (scope == "AUTO" and context.selected_objects):
        pool = list(context.selected_objects)
    else:
        # view_layer.objects (not scene.objects): collections excluded
        # from the view layer are deliberately out of the artist's scene
        # and must not leak into the export.
        pool = list(context.view_layer.objects)

    ancestry = _collection_ancestry(context.scene)
    items, skipped = [], []
    for ob in pool:
        if ob.type not in EXPORTABLE_TYPES and not (
            ob.type == "EMPTY" and ob.instance_type == "COLLECTION"
        ):
            continue
        try:
            if not ob.visible_get():
                # covers collection-level eye/hide state, not just the
                # per-object flags _expand checks
                skipped.append((ob.name, "hidden (collection or viewport)"))
                continue
        except RuntimeError:
            pass  # object not in the view layer (selected via API): keep
        match_chain = sorted(ancestry.get(ob.name, ()))
        _expand(ob, ob.matrix_world, [], match_chain, items, skipped)
    return items, skipped


def prep_object(ob, depsgraph, report, matrix=None, name=None):
    """Evaluated copy -> new mesh -> triangulate -> world space (or the
    given instancer-composed matrix). Returns a PreppedObject, or None
    (reason already reported)."""
    eval_ob = ob.evaluated_get(depsgraph)
    try:
        mesh = bpy.data.meshes.new_from_object(
            eval_ob, preserve_all_data_layers=True, depsgraph=depsgraph
        )
    except RuntimeError as exc:
        report.skipped.append((ob.name, "could not convert to mesh: %s" % exc))
        return None

    if mesh is None or len(mesh.polygons) == 0:
        if mesh is not None:
            bpy.data.meshes.remove(mesh)
        report.skipped.append((ob.name, "no faces after conversion"))
        return None

    # Triangulate on the copy.
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    bm.to_mesh(mesh)
    bm.free()

    # Bake the transform into the vertices (world space, or the
    # instancer-composed matrix for collection-instance contents).
    world = matrix if matrix is not None else ob.matrix_world
    mesh.transform(world)
    if world.determinant() < 0.0:
        # Negative scale mirrors the mesh and flips winding.
        mesh.flip_normals()
        report.warn("'%s' has negative scale; normals were flipped to compensate" % ob.name)

    # The evaluated mesh carries the final material list (this respects
    # geometry-nodes material assignment, not just the object's slots).
    materials = list(mesh.materials)
    if not materials:
        materials = [slot.material for slot in ob.material_slots]
    return PreppedObject(name or ob.name, mesh, materials)


# Objects at or below this triangle count are never decimated - at
# aggressive global ratios, small parts (screws, ports) would collapse
# into garbage while contributing nothing to the total.
DECIMATE_FLOOR_TRIS = 1000


def decimate_prepped(prepped, target_tris, report, detail_boost=8.0):
    """Decimate the temp meshes in place toward a total triangle budget.

    Objects flagged po.protected (props, gimbal, detail keywords) keep
    `detail_boost` times more triangles than plain objects; the plain
    bulk absorbs the difference so the total still lands near the
    budget. Uses a Decimate modifier (Collapse, triangulated output)
    on a throwaway object per mesh - still never touches user data.
    Returns (before_total, after_total)."""
    before = {id(po): len(po.mesh.polygons) for po in prepped}
    total = sum(before.values())
    if target_tris <= 0 or total <= target_tris:
        return total, total

    small = {pid for pid, n in before.items() if n <= DECIMATE_FLOOR_TRIS}
    exempt = sum(before[pid] for pid in small)
    n_prot = sum(before[id(po)] for po in prepped
                 if id(po) not in small and getattr(po, "protected", False))
    n_plain = total - exempt - n_prot
    budget = max(target_tris - exempt, int((n_plain + n_prot) * 0.01))

    # Budget split with guarantees (learned on a production CAD drone, where
    # protected parts held ~80% of all triangles and a naive boost
    # starved the body down to giant facets):
    #   - protected parts never drop below the uniform ratio
    #   - the plain body never drops below HALF the uniform ratio
    #   - the boost only spends what remains above those floors
    boost = max(1.0, detail_boost)
    workload = max(1.0, n_plain + n_prot)
    r_uniform = min(1.0, budget / workload)
    # The body floor rises toward the full uniform share as protected
    # parts dominate the triangle count: starving a small body pool
    # buys the protected pool almost nothing (that drone: the fuselage
    # is 1.6% of the scene - halving it saved 0.2% of the budget).
    plain_floor = r_uniform * max(0.5, n_prot / workload)
    ratio_prot = min(1.0, boost * r_uniform)
    if n_prot * ratio_prot + n_plain * plain_floor > budget:
        ratio_prot = max(r_uniform,
                         (budget - n_plain * plain_floor) / max(1.0, n_prot))
    ratio = (budget - n_prot * ratio_prot) / max(1.0, n_plain)
    ratio = min(1.0, max(0.003, max(plain_floor, ratio)))
    ratio_prot = min(1.0, max(0.003, max(ratio, ratio_prot)))

    for po in prepped:
        if id(po) in small:
            continue
        r = ratio_prot if getattr(po, "protected", False) else ratio
        if r < 1.0:
            po.mesh = _decimate_mesh(po.mesh, r)

    after = sum(len(po.mesh.polygons) for po in prepped)
    report.warn(
        "decimated %d -> %d triangles (body: %d tris at ratio %.3f, "
        "protected: %d tris at ratio %.3f, %d small objects kept intact)"
        % (total, after, n_plain, ratio, n_prot, ratio_prot, len(small))
    )
    return total, after


def _decimate_mesh(mesh, ratio):
    """Run a Collapse-decimate on a temp mesh, returning the new mesh
    (the old one is freed)."""
    ob = bpy.data.objects.new("_e3d_tmp_decimate", mesh)
    bpy.context.scene.collection.objects.link(ob)
    try:
        mod = ob.modifiers.new("dec", "DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = ratio
        mod.use_collapse_triangulate = True
        depsgraph = bpy.context.evaluated_depsgraph_get()
        ob_eval = ob.evaluated_get(depsgraph)
        new_mesh = bpy.data.meshes.new_from_object(
            ob_eval, preserve_all_data_layers=True, depsgraph=depsgraph
        )
    finally:
        bpy.data.objects.remove(ob)
    bpy.data.meshes.remove(mesh)
    return new_mesh
