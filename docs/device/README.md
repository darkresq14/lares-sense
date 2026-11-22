# Device Viewer & 3D CAD Assets (`docs/device/`)

Physical documentation, parametric 3D CAD, printable enclosure exports, and the embeddable interactive **Device Viewer** for the `lares-sense` sensor unit.

> **Physical ground truth stays in [`layout.md`](layout.md)** (facts `S1–S8`, `M1–M4`, coordinate frames, axis maps). Every drawing and 3D export here is a deterministic projection of [`model.scad`](model.scad), which encodes those facts.

---

## Files in this directory

| File | Role | Audience |
|------|------|----------|
| [`layout.md`](layout.md) | Single source of truth for physical facts, walls, mounts & frames | Agents & engineers |
| [`model.scad`](model.scad) | Parametric OpenSCAD 3D assembly (single 3D mechanical source) | Agents & CAD |
| [`device.glb`](device.glb) | Multi-part PBR glTF 2.0 binary (536 KB, 7 named parts + `"Explode"` clip) | Product website / 3D engines |
| [`viewer.html`](viewer.html) | Zero-build interactive Three.js **Device Viewer** (explode slider, orbit/zoom, BOM) | Product website & docs |
| [`technical-drawing.png`](technical-drawing.png) | Multi-view engineering drawing sheet (exploded + BOM + ortho + mounted) | Docs & print |
| [`assembly.stl`](assembly.stl) | Full solid 3D assembly (renders in GitHub's native 3D file viewer) | GitHub / CAD |
| [`enclosure.stl`](enclosure.stl) | 3D-printable enclosure tray + lid laid flat side-by-side | 3D printing |
| [`export-glb.py`](export-glb.py) | Builds `device.glb` directly from `model.scad` (stdlib Python + OpenSCAD) | Regeneration |
| [`render-drawing.py`](render-drawing.py) | Builds `technical-drawing.png`, `assembly.stl`, `enclosure.stl` | Regeneration |

---

## Local preview

Browsers require HTTP (not `file://`) to load ES modules and `.glb` assets:

```bash
python -m http.server 8080 -d docs/device
```

Open:
- Full engineering viewer: `http://localhost:8080/viewer.html`
- Clean website embed mode: `http://localhost:8080/viewer.html?embed=1`

---

## Using on the future product website

### Option A — Drop-in `<iframe>` (zero build setup)

Copy `viewer.html` and `device.glb` into your website's static assets folder (e.g. `public/device/`), then embed:

```html
<iframe
  src="/device/viewer.html?embed=1"
  style="width: 100%; height: 540px; border: 0; border-radius: 16px;"
  loading="lazy"
  title="lares-sense Interactive 3D Device Viewer">
</iframe>
```

Supported query parameters on `viewer.html`:

| Parameter | Example | Effect |
|-----------|---------|--------|
| `embed=1` | `?embed=1` | Hides top brand header and right BOM sidebar for clean hero/section embedding |
| `explode=N` | `?explode=40` | Initial explode percentage (`0`–`100`, default `0` = assembled) |
| `model=URL` | `?model=/cdn/device.glb` | Load `device.glb` from a custom path or CDN |

### Option B — Load `device.glb` directly in Three.js, React Three Fiber, or `<model-viewer>`

`device.glb` is a standards-compliant **glTF 2.0 binary** in **right-handed Y-up meters**, centered on the enclosure envelope (`94 × 65 × 35* mm`), with:

- **7 independently movable child nodes** under `lares_sense_assembly`:
  1. `enclosure_lid` — Blue 3D-printed lid + 4× M2.5 screws
  2. `mpu9150_imu` — MPU-9150 breakout (glued inside lid, chip `+z` into case)
  3. `urad_board` — Green 42×42 mm uRAD Industrial v2.0 (TI IWR6843AoP)
  4. `urad_adapter` — Blue Anteral uRAD IWR-AWR Pi adapter + 4× standoffs
  5. `raspberry_pi` — Raspberry Pi 3 Model B
  6. `enclosure_tray` — Blue 3D-printed enclosure tray + 2× GoPro finger mounts
  7. `gopro_buckle` — Black GoPro quick-release buckle + M5 thumbscrew
- **Per-node `extras` metadata** (`node.userData` in Three.js):
  ```json
  {
    "bom": 3,
    "title": "uRAD Industrial v2.0 (IWR6843AoP)",
    "subtitle": "60 GHz mmWave Antenna-on-Package radar board",
    "specs": "42×42×1.6 mm [KNOWN] · TI IWR6843AoP (15×15 mm) · ...",
    "facts": "S1, S4, S5, S7",
    "explodeOffset": [0.0, 0.074, 0.0]
  }
  ```
- **Built-in `"Explode"` keyframe animation** (`0.0 s` assembled → `1.0 s` exploded) so `<model-viewer>` or any glTF player can scrub it natively with zero custom node math.

---

## Regenerating assets after CAD edits

Whenever you update dimensions in [`model.scad`](model.scad) (e.g., replacing `[EST]` estimates with caliper measurements), regenerate all derived outputs with OpenSCAD installed:

```bash
python docs/device/export-glb.py        # updates device.glb
python docs/device/render-drawing.py    # updates technical-drawing.png, assembly.stl, enclosure.stl
```
