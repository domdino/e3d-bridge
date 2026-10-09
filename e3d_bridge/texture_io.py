"""Texture output: resize, convert, sanitize filenames, write images.

No Pillow. Everything goes through bpy Image datablocks: we work on a
copy of the user's image (never the original), scale it with
Image.scale(), run pixel ops with numpy, and save as PNG or JPG.

Deduplication: the same source image with the same ops/format/size is
written once, no matter how many materials reference it.
"""

import os
import re

import bpy
import numpy as np


def sanitize(name, fallback="unnamed"):
    """Reduce a name to [A-Za-z0-9_]. Never empty."""
    s = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_")
    return s or fallback


class UniqueNames:
    def __init__(self):
        self._used = set()

    def get(self, name, fallback="unnamed"):
        base = sanitize(name, fallback)
        candidate = base
        i = 1
        while candidate.lower() in self._used:
            i += 1
            candidate = "%s_%03d" % (base, i)
        self._used.add(candidate.lower())
        return candidate


class TextureRegistry:
    """Collects MapJobs, writes each unique (image, ops, format, size)
    once into <out_dir>/textures/, returns MTL-relative paths."""

    def __init__(self, out_dir, settings, report):
        self.out_dir = out_dir
        self.settings = settings
        self.report = report
        self.names = UniqueNames()
        self._written = {}  # key -> relative path or None (failed)

    def resolve(self, job):
        """Return the MTL-relative texture path for a MapJob, writing
        the file on first use. Returns None if the image is unusable."""
        image = job.image
        if image is None:
            self.report.warn("a texture node has no image assigned; map dropped")
            return None

        ops = list(job.ops)
        if job.kind == "normal" and self.settings.flip_normal_green:
            ops.append("flip_g")

        use_jpg = job.kind == "color" and not self.settings.png_everything
        fmt = "JPEG" if use_jpg else "PNG"
        max_size = self.settings.max_texture_size

        params = tuple(sorted(job.params.items()))
        key = (image.name_full, tuple(ops), params, fmt, max_size)
        if key in self._written:
            return self._written[key]

        rel = self._write(image, tuple(ops), job.params, fmt, max_size)
        self._written[key] = rel
        return rel

    # ------------------------------------------------------------------

    def _write(self, image, ops, params, fmt, max_size):
        suffix = "_" + "_".join(ops) if ops else ""
        base = self.names.get(os.path.splitext(image.name)[0] + suffix, "texture")

        # Fast path: usable file already on disk, small enough, no pixel
        # ops -> byte copy, no recompression. A PNG on disk is always
        # kept as PNG (lossless beats re-encoding to JPG).
        if not ops:
            src, src_fmt = self._existing_file(image, fmt, max_size)
            if src:
                ext = ".jpg" if src_fmt == "JPEG" else ".png"
                rel = "textures/" + base + ext
                abs_path = os.path.join(self.out_dir, "textures", base + ext)
                os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                with open(src, "rb") as fi, open(abs_path, "wb") as fo:
                    fo.write(fi.read())
                self.report.textures.append((rel, "copied from %s" % os.path.basename(src)))
                return rel

        ext = ".jpg" if fmt == "JPEG" else ".png"
        rel = "textures/" + base + ext
        abs_path = os.path.join(self.out_dir, "textures", base + ext)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)

        if not image.has_data:
            try:
                image.update()
            except RuntimeError:
                pass
        if not image.has_data or image.size[0] == 0:
            self.report.warn(
                "image '%s' has no pixel data (missing file?); map dropped" % image.name
            )
            return None

        img = image.copy()
        try:
            w, h = img.size
            scale = min(1.0, max_size / max(w, h))
            if scale < 1.0:
                w = max(1, int(round(w * scale)))
                h = max(1, int(round(h * scale)))
                img.scale(w, h)

            if ops:
                self._apply_ops(img, ops, params)

            img.filepath_raw = abs_path
            img.file_format = fmt
            if fmt == "JPEG":
                img.save(filepath=abs_path, quality=90)
            else:
                img.save(filepath=abs_path)
            detail = "%dx%d %s" % (w, h, fmt)
            if ops:
                detail += " ops=" + ",".join(ops)
            self.report.textures.append((rel, detail))
            return rel
        finally:
            bpy.data.images.remove(img)

    def _existing_file(self, image, fmt, max_size):
        """Return (path, format) of a directly copyable source file, or
        (None, None). PNG sources always qualify; JPEG only when the
        target format is JPEG (never silently downgrade data maps)."""
        if image.packed_file is not None or image.source != "FILE":
            return None, None
        allowed = {"PNG"} | ({"JPEG"} if fmt == "JPEG" else set())
        if image.file_format not in allowed:
            return None, None
        if image.size[0] == 0 or max(image.size) > max_size:
            return None, None
        path = bpy.path.abspath(image.filepath, library=image.library)
        if not os.path.isfile(path):
            return None, None
        return path, image.file_format

    @staticmethod
    def _apply_ops(img, ops, params=None):
        params = params or {}
        w, h = img.size
        px = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(px)
        px = px.reshape(-1, 4)
        for op in ops:
            if op == "invert":
                px[:, 0:3] = 1.0 - px[:, 0:3]
            elif op == "flip_g":
                px[:, 1] = 1.0 - px[:, 1]
            elif op == "alpha_to_gray":
                a = px[:, 3].copy()
                px[:, 0] = a
                px[:, 1] = a
                px[:, 2] = a
                px[:, 3] = 1.0
            elif op == "height_to_normal":
                # Height field -> tangent-space normal map (OpenGL green).
                # Blender stores pixel rows bottom-up, which matches the
                # OpenGL +Y-up convention, so no green flip here.
                height = px[:, 0].reshape(h, w).astype(np.float64)
                scale = 5.0 * float(params.get("bump_scale", 1.0))
                dy, dx = np.gradient(height)
                nx = -dx * scale * w / 1024.0
                ny = -dy * scale * h / 1024.0
                nz = np.ones_like(nx)
                length = np.sqrt(nx * nx + ny * ny + nz * nz)
                px[:, 0] = (nx / length * 0.5 + 0.5).reshape(-1)
                px[:, 1] = (ny / length * 0.5 + 0.5).reshape(-1)
                px[:, 2] = (nz / length * 0.5 + 0.5).reshape(-1)
                px[:, 3] = 1.0
        img.pixels.foreach_set(px.reshape(-1))
