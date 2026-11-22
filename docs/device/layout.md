# Device layout & frames — the physical ground truth

The **single source of truth for the unit's physical geometry**: board
stack, case walls, mounts, coordinate frames, axis maps. Two wrong
axis maps shipped on `fix/setup-tool-compass-imu` because agent and
owner held different mental models — this page makes the model shared.

**Read it before touching anything orientation-related** (`imu.py`,
`overlays.js`, `sensorPosition`, point-cloud consumers). Axes come
from the tagged facts below, never from code — code has been wrong in
both directions — and code comments / tests that encode geometry cite
the fact IDs (S1…S8, M1…M4). Upgrade a tag only with new evidence,
recorded inline.

Status tags:

| Tag | Meaning |
|-----|---------|
| **VERIFIED** | Pinned by live probe, vendor doc, or photo audit; evidence cited |
| **USER-CONFIRMED** | Stated by the device owner against the physical unit |
| **ASSUMED** | Plausible but unproven — must not gate math; probe listed |

Sources cited inline as: `[branch]` = `fix/setup-tool-compass-imu`
commits 09fe2fa→c7d8a6d→8a468cc + live probes 2026-10-11; `[photos]` =
photo audit 2026-11-08 (§8 index, gitignored); `[uRAD]` = uRAD
Industrial User Manual p.4–5 + datasheet + IWR-AWR Pi adapter doc
(URLs + quotes in `docs/research/device-drawing-tool.md` addendum);
`[TI]` = TI 3D People Tracking guide / People Counting implementation
guide / L-SDK CLI reference (same addendum).

3D CAD & technical drawings (all facts stay here):

- **Parametric 3D CAD source**: [`model.scad`](model.scad)
  (OpenSCAD — exact piece-by-piece geometry, agent-readable dimensions &
  modes: `assembled`, `exploded`, `open_lid`, `mounted`, `stl_enclosure`).
- **Engineering drawing sheet**: [`technical-drawing.png`](technical-drawing.png)
  (exploded assembly + BOM, assembled 3D + radar frame, open-lid inspection,
  orthographic views, mounted pose) — regenerate with
  `python docs/device/render-drawing.py`.
- **Interactive 3D web viewer**: [`viewer.html`](viewer.html) +
  [`device.glb`](device.glb) (multi-part PBR glTF 2.0 with explode slider,
  orbit/zoom, BOM inspector, dynamic IMU wires, mounted pose & `?embed=1`
  iframe mode) — regenerate GLB with `python docs/device/export-glb.py`.
- **3D STL exports**: [`assembly.stl`](assembly.stl) (full
  assembly, interactive 3D viewer on GitHub) and
  [`enclosure.stl`](enclosure.stl) (extrudable/3D-printable tray
  + lid).
- **2D schematic (legacy)**: [`drawing.svg`](drawing.svg)
  (`python docs/device/drawing.py`).

## 1. The stack (inside the case)

Desk pose: case lying flat, **lid up**, ports facing you.

```
                 boresight (radar beam) exits here
                            ▲
  ┌─────────────────────────┴───────────────────────────┐
  │ LID — flat screwed panel (4 corner screws, no       │
  │ hinge). Rectangular aperture over the radar chip.   │
  │ MPU-9150 breakout (GY-521 style, "MPU-9150"         │
  │ silkscreen) glued to the lid's INNER face, at the   │
  │ PORTS-END corner, chip facing INTO the case.        │
  │ 4 wires (red/yel/grn/blk) → Pi GPIO pin-1 end       │
  │ (SD-card side) via the adapter's pass-through.      │
  ├──────────────────────────────────────────────────────┤
  │   uRAD "by Anteral" board (green, 42×42 mm) —       │
  │   IWR6843AoP shield slightly offset toward the      │
  │   SD end. Screwed on standoffs onto…                │
  ├──────────────────────────────────────────────────────┤
  │   …Anteral uRAD IWR-AWR Pi adapter (blue PCB) —     │
  │   keys onto the Pi GPIO header; uRAD↔Pi interconnect│
  │   runs underneath the stack (not visible assembled).│
  │   Its "HMC588…/MPU…" silkscreens = unpopulated pads.│
  ├────────────── Pi GPIO header / standoffs ───────────┤
  │   Raspberry Pi — flat in the tray, ports row out    │
  │   one short wall (RJ45 + stacked USB-A).            │
  ├──────────────────────────────────────────────────────┤
  │ BOTTOM — screwed floor plate (4 screws, microSD     │
  │ window). The flat face pressed flush against walls  │
  │ during Wall Captures.                               │
  └──────────────────────────────────────────────────────┘
```

Wall map (desk pose, ports facing you): **ports wall** (short, RJ45+USB)
ahead; **SD/mount wall** (short) behind — carries the black GoPro mount;
**GPIO wall** (long, left) — Pi header runs along it; **power wall**
(long, right) — barrel jack, HDMI, audio, + blue printed GoPro fingers.

| # | Fact | Status |
|---|------|--------|
| S1 | Two large faces: lid + bottom; boresight ⊥ both, exits the **lid** (aperture over the radar chip, component side faces the lid) | VERIFIED — desk probe `[branch]` + `[photos]` |
| S2 | MPU-9150 on lid inner face, ports-end corner, chip +z into the case | VERIFIED — desk probe `[branch]` + `[photos]` |
| S3 | AK8975 compass die flipped 180° about x vs the accel die | VERIFIED — five probe sessions `[branch]`; fixtures in `tests/test_imu_live_replay.py` |
| S4 | Stack = Pi ← (GPIO) ← blue Anteral IWR-AWR adapter ← (standoffs) ← green uRAD board. uRAD's micro-USB edge faces the ports wall; its micro-USB sits at the corner touching the **GPIO wall** | VERIFIED — `[photos]` (5× zoom, cross-checked) + adapter doc `[uRAD]` |
| S5 | ~~Which plug orientation (0°/180°)~~ **CLOSED**: board-vs-case orientation is photo-pinned (S4); the adapter doc's "two connectors" freedom is moot — see §5 result | VERIFIED — `[photos]` + `[uRAD]` |
| S6 | Mount end = Pi end opposite the ports (SD end, black GoPro mount); secondary blue fingers on the power wall | USER-CONFIRMED + `[photos]` |
| S7 | Radar boresight out the lid ⇒ antenna face toward the lid | Follows from S1 |
| S8 | Lid = removable screwed panel, no hinge; MPU-9150 is a GY-521-style wired breakout, not a bare chip | VERIFIED — `[photos]` |

## 2. Mounted pose (corner install)

Side view — standing in the room, looking along the wall. The case
stands **on end**: mount end down on the grip, ports end up.

```
                         ceiling
                            ▲
                            │   ports end (RJ45/USB wall)
                            │   points more or less at the ceiling
                        ┌───┴───┐
                        │   Pi  │        lid faces the ROOM
                        │  uRAD │ ◄──────────────────────────┐
                        │ stack │                           │
                        └───┬───┘ boresight exits the lid,  │
                            │      tilted ~15° DOWN         │
                        grip / GoPro                       │
                            │                    ±60° FOV cone
                            │                  /
                       pole (vertical,      .–´
                       in the corner)  .–´
```

Top-down (from the ceiling): boresight along the **corner bisector**,
pole behind the boresight, outside the ±60° FOV.

```
                wall A
              ╲
               ╲   Aim Assist bisector = boresight azimuth
                ╲      (two Wall Captures, spin-averaged)
                 ╲    FOV ±60°
                  ╲  ▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬►  radar looks INTO the room
                   ● pole + grip            along the bisector,
                  ╱                           ~15° down
                 ╱
              wall B
```

| # | Fact | Status |
|---|------|--------|
| M1 | Mount end down on grip; ports end up, roughly at the ceiling | VERIFIED — `[photos]` (mounted shots; barrel-jack cluster high on the wall) |
| M2 | Lid faces the room; boresight into the room along the corner bisector | VERIFIED — `[photos]` |
| M3 | ~15° downward tilt, set by the grip | USER-CONFIRMED |
| M4 | Pole behind the boresight, outside ±60° FOV | USER-CONFIRMED |

Note: desk pose (lid up) has the boresight at the **ceiling** — that
pose reads pitch −90 in the tool frame, *not* level. The old "flat desk
reads level" check was the frame bug's signature (09fe2fa).

## 3. Frame registry

| Frame | Axes | Handedness | Owner |
|-------|------|------------|-------|
| **chip-acc** (MPU6050 die) | die x, y, z | right | `Mpu9150.read()` raw |
| **chip-mag** (AK8975 die) | flipped 180° about x vs chip-acc (S3) | right | same |
| **radar / point cloud** (TI TLV, uRAD board frame) | **+Y = boresight/range** (out the lid), **+X lateral** (toward the micro-USB corner ⇒ the GPIO-wall side when mounted), **+Z = up** (toward the ports end). Azimuth φ positive from +Y toward +X; elevation θ positive up | right | `[uRAD]` manual p.4–5, `[TI]` |
| **tool / body** (`imu.py`, `overlays.js`) | x right, y = boresight, z up | ⚠ see note | IMU math only |
| **world** | N, E, Up; heading 0 = N, clockwise viewed from above | — | Aim Assist |

⚠ **Handedness**: the live chip→tool map is improper (det −1), yet the
triple x-right/y-forward/z-up reads right-handed — an unresolved
tension, live-pinned `[branch]`. Sign edits are off-limits; treat tool
x vs radar +X as independent frames until compared once in the room
preview (§5, last row).

`sensorPosition <z> <azimTilt> <elevTilt>` signs (TI L-SDK CLI ref):
azimTilt positive = clockwise viewed toward the ground; **elevTilt
positive = tilt toward the ground** (so `sensorPosition 2 0 15` = 2 m,
down 15°). TLVs stay in sensor frame; sensorPosition feeds the tracker,
not the point cloud `[TI]`.

## 4. Axis maps (chip → tool)

VERIFIED live on urad-pi, 2026-10-11 `[branch]`; replay fixtures in
`radar_pi/tests/test_imu_live_replay.py`:

```
accel die:   x → x,   y → −z,  z → −y        (apply_axis_map)
compass die: x → x,   y →  z,  z →  y        (apply_mag_axis_map)
```

Both maps put the boresight (chip −z) on the math's **+y**. The two dies
differ because of S3.

## 5. Radar frame vs the case — RESOLVED (was P-1)

Derivation chain (each link VERIFIED):

| Link | Evidence |
|------|----------|
| Radar frame → uRAD board: +Y out of chip top face; +Z toward the micro-USB/10-pin edge; +X toward the micro-USB **corner** ("for an observer placed behind the radar, X positive is at the right of the radar, Z positive above, Y positive at the front") | uRAD Industrial User Manual p.4–5 `[uRAD]`, matches `[TI]` |
| uRAD board → case: micro-USB edge → ports wall; micro-USB at the GPIO-wall corner of that edge; shield offset toward SD end | `[photos]` 5× zoom, cross-checked 3 shots |
| Case, mounted: ports end up, lid → room | §2 |

**Result (mounted, lid facing room):**

- **+Y / azimuth 0 = into the room** along the bisector.
- **+Z = up** (toward the ports end). Ports-up mounting ⇒ azimuth frame
  world-aligned.
- **+X = toward the GPIO-wall side of the case** — i.e. the long side
  **without** the barrel jack / blue fingers. The power wall (barrel
  jack + fingers) is the **−X** side. Facing the unit from inside the
  room, +X is on your **left**; standing behind the unit (pole side)
  looking into the room, +X is on your **right** — matching the manual's
  observer-behind wording.
- A person standing left of the boresight (viewer inside the room)
  ⇒ negative-azimuth / negative-x points.
- Older premise "TI: x = range, y lateral" (repeated in this file's
  first draft and in an earlier question) was **backwards** — both TI
  and uRAD define **Y = range/forward, X = lateral** `[TI]` `[uRAD]`.

Open micro-item (not blocking): tool-frame x sign (§3 ⚠) vs radar +X —
compare once in the room preview overlay; until then treat the IMU math
and point-cloud consumers as separate frames that happen to share names.

## 6. Optional 60-second live sanity check

The chain is photo+doc-pinned; this is belt-and-braces, not required:
aim the unit, have one person stand clearly on the room-viewer's LEFT of
the boresight. Expect their points at **negative** lateral coordinate
(TI x / azimuth). If positive → re-open §5 before touching any code.

## 7. How agents use this page

- Synthetic tests **cannot** discriminate frame models: any
  (model, map) pair composing to the same tool frame passes
  (`test_imu_physical_frame.py` header, 8a468cc). Only live fixtures
  (`test_imu_live_replay.py`) pin reality — keep that split.
- Found a geometry bug? Fix the fact table here first (with evidence),
  then the code, then add a live-replay fixture for the regression.
- New physical change (re-seat the board, new case, IMU moved)?
  Invalidate the affected tags and re-audit (photos again close most).

## 8. Photo index (gitignored — `docs/device/photos/`)

| File | Content |
|------|---------|
| `PXL_20260601_110652740.jpg` | Closed case, lid up — IMU window in lid |
| `PXL_20260602_0537*` , `PXL_20260606_*`, `PXL_20260607_*` | Old red TI EVM reference rig + GoPro on pole — **not** the lares-sense stack |
| `PXL_20261012_1458*` | Unit mounted on blue pole, lid → room; Ti EVM rig below |
| `PXL_20261108_115040534/48942` | Desk pose: port row (RJ45+USB) visible |
| `PXL_20261108_115059496/109873` | Case inverted/upright; power wall (micro-HDMI, audio, barrel jack), SD window, mounts |
| `PXL_20261108_115251920/115300618` (MACRO) | Open tray top-down: stack, IMU wiring, uRAD micro-USB at GPIO-wall corner — the money shots for §5 |
| `PXL_20261108_115314449` (MACRO) | Lid open in hand: MPU-9150 breakout + pass-through pins |
| `PXL_20261108_115448647/115455783` | Mounted on pole: grip at SD end, ports up, lid → room; view through lid window |
