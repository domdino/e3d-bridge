# E3D Bridge

A Blender 4.2+ extension that exports your scene as a folder Video Copilot's **Element 3D** imports cleanly: one OBJ, one MTL, textures converted to the slots Element reads, and a `report.txt` explaining every decision.

**Found a bug or have a request? [Open an issue](https://github.com/domdino/e3d-bridge/issues)** — attaching the `report.txt` from your export folder helps a lot.

## Install

Get it from [extensions.blender.org](https://extensions.blender.org) (search "E3D Bridge"), or download it from [diskolabs.org](https://diskolabs.org/blog/blender-to-element-3d-exporter-free) and drag the zip onto a Blender window.

## Use

1. In the 3D Viewport press **N** → **E3D Bridge** tab → **Export for Element 3D**.
2. A `<yourfile>_e3d` folder appears next to your .blend.
3. In After Effects: apply Element to a solid → **Scene Setup** → **Import** → pick the OBJ.

## What it does

- Writes OBJ + MTL with the keys Element reads (diffuse, specular, glossiness, normal, opacity, illumination), with relative, sanitized texture paths
- Converts Principled BSDF image textures: roughness → glossiness, metallic → specular, bump height maps → normal maps, alpha → opacity
- Applies modifiers, converts curves and text, expands linked collection instances, skips hidden or excluded objects
- Decimates to a triangle budget, with keyword **part rules** that keep chosen parts sharp and give them their own group and materials (props, gimbals and lenses by default)
- Never modifies your scene — everything runs on temporary copies

**Known limitation:** fully procedural materials (noise, mix shaders, glass) export flat gray for now; `report.txt` names each one. Baking is planned.

## Development

Tests run headless against generated sample scenes:

```bash
blender --background --python tests/run.py
```

Export any .blend without the UI:

```bash
blender --background --python tests/export_blend.py -- file.blend [out_dir] [--decimate N]
```

Build the installable zip with Blender's own tooling:

```bash
blender --command extension build --source-dir e3d_bridge
```

## License

GPL-3.0-or-later. Made by [DISKO Labs](https://diskolabs.org).
