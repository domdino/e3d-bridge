"""Space conversions.

Blender world space is right-handed, Z up, -Y forward.
OBJ target space (matching Blender's own OBJ exporter defaults and what
Element 3D expects): -Z forward, Y up.

That conversion is the pure rotation (x, y, z) -> (x, z, -y), so face
winding is preserved and normals convert with the same formula.

Scale: `scale` OBJ units per Blender meter. Default 100, matching
Blender's built-in After Effects exporter (1 m = 100 AE units). Phase 0
spike 6 confirms or adjusts this.
"""


def vert_to_obj(co, scale):
    """Blender world-space vertex -> OBJ space, scaled."""
    return (co[0] * scale, co[2] * scale, -co[1] * scale)


def normal_to_obj(n):
    """Blender world-space normal -> OBJ space (no scale)."""
    return (n[0], n[2], -n[1])
