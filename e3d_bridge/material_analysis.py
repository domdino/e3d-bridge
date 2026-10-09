"""Classify each material: passthrough / bake / unsupported.

Phase 1 handles PASSTHROUGH only:
  - a single Principled BSDF with plain Image Texture nodes plugged
    straight into Base Color, Roughness, Normal (through a Normal Map
    node), Alpha, Emission Color, and Metallic, all sharing one UV map
    (or the default), OR
  - a single Emission shader with an image or constant color, OR
  - a node-less material (viewport constants only).

Everything else that ends in a shader is BAKE (Phase 2 - for now those
export flat gray so the mesh still shows up, and the report says why).
Volume-only or output-less materials are UNSUPPORTED.

MTL mapping (see docs/e3d-material-mapping.md, confirmed by Phase 0):
  Base Color   -> map_Kd / Kd
  Roughness    -> inverted -> glossiness -> map_Ns / Ns
  Normal       -> map_bump
  Alpha        -> map_d / d
  Emission     -> map_Ke / Ke
  Metallic     -> map_Ks / Ks   (E3D has no metallic slot; Phase 2 adds
                                 a smarter specular strategy)
"""

PASSTHROUGH = "PASSTHROUGH"
BAKE = "BAKE"
UNSUPPORTED = "UNSUPPORTED"

# Principled sockets we translate. Any *linked* socket outside this set
# (and outside _IGNORABLE_LINKED) forces BAKE, because the MTL cannot
# express it.
_HANDLED_INPUTS = {
    "Base Color", "Roughness", "Metallic", "Alpha", "Normal",
    "Emission Color", "Emission Strength",
}

# Linked sockets that have NO Element 3D equivalent at all. Baking
# would not capture them either (they modulate specular response, not
# color), so dropping them with a warning beats exporting flat gray.
# Real-world driver: production drone materials often connect Coat Roughness.
_IGNORABLE_LINKED = {
    "Coat Weight", "Coat Roughness", "Coat IOR", "Coat Tint", "Coat Normal",
    "Sheen Weight", "Sheen Roughness", "Sheen Tint",
    "Specular Tint", "Specular IOR Level",
    "Anisotropic", "Anisotropic Rotation", "Tangent", "IOR",
}

# Blender's OBJ importer reads roughness = 1 - sqrt(Ns / 1000).
# We write the inverse so a Blender round-trip agrees with us.
def roughness_to_ns(roughness):
    r = min(max(roughness, 0.0), 1.0)
    return (1.0 - r) ** 2 * 1000.0


def metallic_to_ks(metallic):
    # Dielectric base spec 0.5 blended toward full specular for metals.
    m = min(max(metallic, 0.0), 1.0)
    v = 0.5 * (1.0 - m) + m
    return (v, v, v)


class MapJob:
    """One texture destined for one MTL key."""

    def __init__(self, mtl_key, image, ops=(), kind="data", params=None):
        self.mtl_key = mtl_key
        self.image = image
        self.ops = tuple(ops)  # 'invert', 'alpha_to_gray', 'flip_g', 'height_to_normal'
        self.kind = kind       # 'color' (sRGB, JPG ok), 'data' (PNG), 'normal'
        self.params = params or {}  # op parameters, e.g. {'bump_scale': 1.0}


class MaterialExport:
    def __init__(self, material):
        self.material = material
        self.name = material.name if material else "default"
        self.classification = PASSTHROUGH
        self.reason = ""
        self.constants = {}    # 'Kd': (r,g,b), 'Ks': (r,g,b), 'Ns': f, 'd': f, 'Ke': (r,g,b)
        self.maps = []         # [MapJob]
        self.uv_map = None     # UV map name the textures expect, or None for render-active
        self.warnings = []

    def as_flat_gray(self, reason, classification=BAKE):
        self.classification = classification
        self.reason = reason
        self.constants = {"Kd": (0.8, 0.8, 0.8)}
        self.maps = []
        return self


def _is_identity_mapping(node):
    """A Mapping node that changes nothing: all transform inputs
    unconnected and at their defaults."""
    defaults = {"Location": (0.0, 0.0, 0.0), "Rotation": (0.0, 0.0, 0.0),
                "Scale": (1.0, 1.0, 1.0)}
    for name, want in defaults.items():
        sock = node.inputs.get(name)
        if sock is None:
            continue
        if sock.is_linked:
            return False
        got = tuple(sock.default_value)
        if any(abs(g - w) > 1e-6 for g, w in zip(got, want)):
            return False
    return True


def _resolve_uv_source(vec_socket):
    """Follow an Image Texture's Vector input back to its UV origin.
    Returns the UV map name, None for the default UV (unconnected,
    Texture Coordinate > UV, or an identity Mapping in between), or
    False for anything we cannot pass through (real Mapping transform,
    Generated/Object coords, procedural vectors)."""
    if vec_socket is None or not vec_socket.is_linked:
        return None
    link = vec_socket.links[0]
    node = link.from_node
    if node.type == "MAPPING":
        if not _is_identity_mapping(node):
            return False
        return _resolve_uv_source(node.inputs.get("Vector"))
    if node.type == "UVMAP":
        return node.uv_map or None
    if node.type == "TEX_COORD":
        return None if link.from_socket.name == "UV" else False
    return False


def _image_source(socket):
    """If `socket` is fed directly by an Image Texture node, return
    (image, from_socket_name, uv_or_False). uv_or_False is the UV map
    name, None for the default UV, or False if the Vector input is
    driven by anything we cannot pass through."""
    if not socket.is_linked:
        return None
    link = socket.links[0]
    node = link.from_node
    if node.type != "TEX_IMAGE":
        return None
    uv = _resolve_uv_source(node.inputs.get("Vector"))
    return (node.image, link.from_socket.name, uv)


def analyze(material):
    """Classify one material and build its MTL translation."""
    me = MaterialExport(material)

    if material is None:
        me.constants = {"Kd": (0.8, 0.8, 0.8)}
        return me

    if not material.use_nodes:
        c = material.diffuse_color
        me.constants = {
            "Kd": (c[0], c[1], c[2]),
            "Ns": roughness_to_ns(material.roughness),
            "Ks": metallic_to_ks(material.metallic),
        }
        return me

    nodes = material.node_tree.nodes
    output = next(
        (n for n in nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output),
        None,
    ) or next((n for n in nodes if n.type == "OUTPUT_MATERIAL"), None)
    if output is None:
        return me.as_flat_gray("no Material Output node", UNSUPPORTED)

    surface = output.inputs.get("Surface")
    if surface is None or not surface.is_linked:
        if output.inputs.get("Volume") is not None and output.inputs["Volume"].is_linked:
            return me.as_flat_gray("volume shader - not representable in E3D", UNSUPPORTED)
        return me.as_flat_gray("nothing connected to Material Output", UNSUPPORTED)

    shader = surface.links[0].from_node

    if shader.type == "EMISSION":
        return _analyze_emission_shader(me, shader)
    if shader.type != "BSDF_PRINCIPLED":
        return me.as_flat_gray(
            "surface is %s, not a Principled BSDF - needs baking" % shader.type
        )

    return _analyze_principled(me, shader)


def _analyze_emission_shader(me, shader):
    color = shader.inputs.get("Color")
    strength_sock = shader.inputs.get("Strength")
    if strength_sock is not None and strength_sock.is_linked:
        return me.as_flat_gray("Emission strength is driven by nodes - needs baking")
    strength = strength_sock.default_value if strength_sock else 1.0

    src = color.is_linked and _image_source(color)
    if color.is_linked and not src:
        return me.as_flat_gray("Emission color comes from procedural nodes - needs baking")

    me.constants["Kd"] = (0.0, 0.0, 0.0)
    if src:
        image, _from, uv = src
        if uv is False:
            return me.as_flat_gray("image texture uses a Mapping/vector setup - needs baking")
        me.uv_map = uv
        me.maps.append(MapJob("map_Ke", image, kind="color"))
    else:
        c = color.default_value
        me.constants["Ke"] = (c[0] * strength, c[1] * strength, c[2] * strength)
    return me


def _analyze_principled(me, shader):
    # Linked sockets we cannot translate: ignorable ones are dropped
    # with a warning (E3D cannot show them anyway); the rest force a bake.
    for sock in shader.inputs:
        if sock.is_linked and sock.name not in _HANDLED_INPUTS:
            if sock.name in _IGNORABLE_LINKED:
                me.warnings.append(
                    "input '%s' has no E3D equivalent - ignored" % sock.name
                )
            else:
                return me.as_flat_gray(
                    "input '%s' is driven by nodes - needs baking" % sock.name
                )

    uv_names = set()

    def track_uv(uv):
        if uv is not None:  # None = default UV, compatible with anything
            uv_names.add(uv)

    # --- Base Color ---
    base = shader.inputs["Base Color"]
    if base.is_linked:
        src = _image_source(base)
        if not src:
            return me.as_flat_gray("Base Color comes from procedural/mix nodes - needs baking")
        image, from_sock, uv = src
        if uv is False:
            return me.as_flat_gray("Base Color texture uses a Mapping/vector setup - needs baking")
        track_uv(uv)
        me.maps.append(MapJob("map_Kd", image, kind="color"))
    else:
        c = base.default_value
        me.constants["Kd"] = (c[0], c[1], c[2])

    # --- Roughness -> glossiness ---
    rough = shader.inputs["Roughness"]
    if rough.is_linked:
        src = _image_source(rough)
        if not src:
            return me.as_flat_gray("Roughness comes from procedural nodes - needs baking")
        image, from_sock, uv = src
        if uv is False:
            return me.as_flat_gray("Roughness texture uses a Mapping/vector setup - needs baking")
        track_uv(uv)
        me.maps.append(MapJob("map_Ns", image, ops=("invert",), kind="data"))
    else:
        me.constants["Ns"] = roughness_to_ns(rough.default_value)

    # --- Metallic -> specular ---
    metal = shader.inputs["Metallic"]
    if metal.is_linked:
        src = _image_source(metal)
        if not src:
            return me.as_flat_gray("Metallic comes from procedural nodes - needs baking")
        image, from_sock, uv = src
        if uv is False:
            return me.as_flat_gray("Metallic texture uses a Mapping/vector setup - needs baking")
        track_uv(uv)
        me.maps.append(MapJob("map_Ks", image, kind="data"))
        me.warnings.append(
            "metallic map written as map_Ks; E3D is not PBR, check the result"
        )
    else:
        me.constants["Ks"] = metallic_to_ks(metal.default_value)

    # --- Alpha -> opacity ---
    alpha = shader.inputs["Alpha"]
    if alpha.is_linked:
        src = _image_source(alpha)
        if not src:
            return me.as_flat_gray("Alpha comes from procedural nodes - needs baking")
        image, from_sock, uv = src
        if uv is False:
            return me.as_flat_gray("Alpha texture uses a Mapping/vector setup - needs baking")
        track_uv(uv)
        ops = ("alpha_to_gray",) if from_sock == "Alpha" else ()
        me.maps.append(MapJob("map_d", image, ops=ops, kind="data"))
    elif alpha.default_value < 1.0:
        me.constants["d"] = alpha.default_value

    # --- Emission ---
    emit_col = shader.inputs.get("Emission Color")
    emit_str = shader.inputs.get("Emission Strength")
    if emit_str is not None and emit_str.is_linked:
        return me.as_flat_gray("Emission Strength is driven by nodes - needs baking")
    strength = emit_str.default_value if emit_str is not None else 1.0
    if emit_col is not None and emit_col.is_linked:
        src = _image_source(emit_col)
        if not src:
            return me.as_flat_gray("Emission Color comes from procedural nodes - needs baking")
        image, from_sock, uv = src
        if uv is False:
            return me.as_flat_gray("Emission texture uses a Mapping/vector setup - needs baking")
        if strength > 0.0:
            track_uv(uv)
            me.maps.append(MapJob("map_Ke", image, kind="color"))
    elif emit_col is not None and strength > 0.0:
        c = emit_col.default_value
        if c[0] > 0.0 or c[1] > 0.0 or c[2] > 0.0:
            me.constants["Ke"] = (c[0] * strength, c[1] * strength, c[2] * strength)

    # --- Normal ---
    # A normal we cannot translate NEVER sinks the whole material: the
    # diffuse/roughness/etc. still pass through and we warn that the
    # surface detail was dropped (Phase 2 baking recovers it).
    normal = shader.inputs["Normal"]
    if normal.is_linked:
        nm = normal.links[0].from_node
        dropped = None
        if nm.type == "NORMAL_MAP":
            if nm.space != "TANGENT":
                dropped = "Normal Map node is not tangent-space"
            else:
                color = nm.inputs.get("Color")
                src = color is not None and color.is_linked and _image_source(color)
                if not src:
                    dropped = "Normal Map color is not a plain image"
                elif src[2] is False:
                    dropped = "normal texture uses a Mapping/vector setup"
                else:
                    track_uv(src[2])
                    me.maps.append(MapJob("map_bump", src[0], kind="normal"))
        elif nm.type == "BUMP":
            # Height-map bump: convert the height image to a tangent
            # normal map with a Sobel filter at texture-write time.
            strength_s = nm.inputs.get("Strength")
            distance_s = nm.inputs.get("Distance")
            height_s = nm.inputs.get("Height")
            if (strength_s and strength_s.is_linked) or (distance_s and distance_s.is_linked):
                dropped = "Bump strength/distance is node-driven"
            else:
                src = height_s is not None and height_s.is_linked and _image_source(height_s)
                if not src:
                    dropped = "Bump height is not a plain image (procedural)"
                elif src[2] is False:
                    dropped = "bump height texture uses a Mapping/vector setup"
                else:
                    strength = strength_s.default_value if strength_s else 1.0
                    distance = distance_s.default_value if distance_s else 1.0
                    track_uv(src[2])
                    me.maps.append(MapJob(
                        "map_bump", src[0], ops=("height_to_normal",),
                        kind="normal",
                        params={"bump_scale": round(strength * distance, 4)},
                    ))
                    me.warnings.append(
                        "bump height map converted to a normal map (approximate)"
                    )
        else:
            dropped = "normal comes from procedural nodes"
        if dropped:
            me.warnings.append(
                "normal detail dropped (%s) - surface will render smooth "
                "until Phase 2 baking" % dropped
            )

    # --- Transmission / glass: not representable, fake with opacity ---
    trans = shader.inputs.get("Transmission Weight")
    if trans is not None and not trans.is_linked and trans.default_value > 0.0:
        if "d" not in me.constants and not any(m.mtl_key == "map_d" for m in me.maps):
            me.constants["d"] = max(0.1, 1.0 - trans.default_value)
        me.warnings.append(
            "transmission (glass) approximated with opacity %.2f - E3D cannot do refraction"
            % me.constants.get("d", 1.0)
        )

    # --- One UV map across all textures ---
    if len(uv_names) > 1:
        return me.as_flat_gray(
            "textures read from multiple UV maps (%s) - needs baking" % ", ".join(sorted(uv_names))
        )
    me.uv_map = next(iter(uv_names), None)

    return me
