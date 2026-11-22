#!/usr/bin/env python3
"""Build a multi-part, PBR-colored glTF 2.0 binary (docs/device/device.glb)
directly from docs/device/model.scad (single source of truth).

Features of the generated GLB:
  - 7 independently movable component nodes (exact piece stack)
  - Physically-based (PBR) materials matching real hardware photos
  - Right-handed glTF Y-up meters, centered on the enclosure envelope
  - Embedded glTF 2.0 keyframe animation clip ("Explode", 0.0s -> 1.0s)
    so it works out of the box in <model-viewer>, Blender, Sketchfab, AND
    our custom Three.js interactive slider viewer (docs/device/viewer.html)
  - Rich node.extras metadata (BOM #, specs, layout.md fact refs, explodeOffset)

Zero external Python dependencies required (standard library only + OpenSCAD CLI).

Usage:
    python docs/device/export-glb.py
"""

from __future__ import annotations

import concurrent.futures
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCAD_FILE = HERE / "model.scad"
GLB_OUT = HERE / "device.glb"

# Enclosure center in CAD mm (matches model.scad CASE_L/W/H)
CAD_CENTER_MM = (94.0 / 2.0, 65.0 / 2.0, 35.0 / 2.0)
MM_TO_M = 0.001

# PBR material library (linear RGBA, metallicFactor, roughnessFactor)
MATERIALS: dict[str, dict] = {
    "blue_pla": {
        "name": "RoyalBlue_PLA",
        "baseColorFactor": [0.045, 0.245, 0.620, 1.0],
        "metallicFactor": 0.0,
        "roughnessFactor": 0.48,
    },
    "pi_pcb": {
        "name": "RaspberryPi_GreenPCB",
        "baseColorFactor": [0.040, 0.210, 0.065, 1.0],
        "metallicFactor": 0.05,
        "roughnessFactor": 0.36,
    },
    "adp_pcb": {
        "name": "Anteral_BluePCB",
        "baseColorFactor": [0.025, 0.130, 0.420, 1.0],
        "metallicFactor": 0.05,
        "roughnessFactor": 0.35,
    },
    "urad_pcb": {
        "name": "uRAD_BrightGreenPCB",
        "baseColorFactor": [0.055, 0.310, 0.085, 1.0],
        "metallicFactor": 0.05,
        "roughnessFactor": 0.34,
    },
    "imu_pcb": {
        "name": "MPU9150_NavyPCB",
        "baseColorFactor": [0.020, 0.055, 0.200, 1.0],
        "metallicFactor": 0.05,
        "roughnessFactor": 0.36,
    },
    "metal": {
        "name": "BrushedStainless",
        "baseColorFactor": [0.78, 0.80, 0.82, 1.0],
        "metallicFactor": 0.88,
        "roughnessFactor": 0.28,
    },
    "gold": {
        "name": "GoldPins",
        "baseColorFactor": [0.92, 0.70, 0.18, 1.0],
        "metallicFactor": 0.92,
        "roughnessFactor": 0.24,
    },
    "chip_dark": {
        "name": "IWR6843AoP_Package",
        "baseColorFactor": [0.075, 0.082, 0.092, 1.0],
        "metallicFactor": 0.15,
        "roughnessFactor": 0.42,
    },
    "chip_patch": {
        "name": "AoP_AntennaPatch",
        "baseColorFactor": [0.16, 0.175, 0.195, 1.0],
        "metallicFactor": 0.35,
        "roughnessFactor": 0.35,
    },
    "black_ic": {
        "name": "MatteBlack_PolymerIC",
        "baseColorFactor": [0.032, 0.034, 0.038, 1.0],
        "metallicFactor": 0.04,
        "roughnessFactor": 0.55,
    },
    "white_btn": {
        "name": "WhiteTactileSwitch",
        "baseColorFactor": [0.86, 0.86, 0.86, 1.0],
        "metallicFactor": 0.0,
        "roughnessFactor": 0.40,
    },
}

# 7 physical parts, their material sub-meshes, explode vectors (in CAD mm), and BOM metadata.
# CAD mm -> glTF meters transform: (x_cad, y_cad, z_cad) -> (x_cad, z_cad, -y_cad) * 0.001
PARTS: list[dict] = [
    {
        "id": "enclosure_lid",
        "bom": 1,
        "title": "Blue 3D-Printed Lid + 4× M2.5 Screws",
        "subtitle": "Removable flat lid with keyhole radar aperture",
        "specs": "94×65×2.5* mm · PLA/PETG · keyhole over AoP + DIP/reset notch · 4× M2.5 screws",
        "facts": "S1, S8",
        "materials": ["blue_pla", "metal"],
        "explode_cad_mm": [0.0, 0.0, 128.0],
    },
    {
        "id": "mpu9150_imu",
        "bom": 2,
        "title": "MPU-9150 9-DOF IMU Breakout",
        "subtitle": "GY-521 class breakout glued to lid inner face",
        "specs": "16×21×1.6* mm · chip +z faces INTO case · AK8975 mag flipped 180° about x · 4 wires → Pi GPIO",
        "facts": "S2, S3, S8",
        "materials": ["imu_pcb", "black_ic", "gold"],
        "explode_cad_mm": [0.0, 0.0, 102.0],
    },
    {
        "id": "urad_board",
        "bom": 3,
        "title": "uRAD Industrial v2.0 (IWR6843AoP)",
        "subtitle": "60 GHz mmWave Antenna-on-Package radar board",
        "specs": "42×42×1.6 mm [KNOWN] · TI IWR6843AoP (15×15 mm) · µUSB at ports/GPIO corner · SOP2 DIP + reset",
        "facts": "S1, S4, S5, S7",
        "materials": ["urad_pcb", "metal", "chip_dark", "chip_patch", "black_ic", "white_btn"],
        "explode_cad_mm": [0.0, 0.0, 74.0],
    },
    {
        "id": "urad_adapter",
        "bom": 4,
        "title": "Anteral uRAD IWR-AWR Pi Adapter",
        "subtitle": "Blue bridge PCB + 4× M2.5 hex standoffs",
        "specs": "58×55×1.6* mm · 2×20 GPIO pass-through + 2×5 CONNFLY interconnect · unpopulated HMC5883L pads",
        "facts": "S4, S5",
        "materials": ["adp_pcb", "black_ic", "gold", "metal"],
        "explode_cad_mm": [0.0, 0.0, 48.0],
    },
    {
        "id": "raspberry_pi",
        "bom": 5,
        "title": "Raspberry Pi 3 Model B",
        "subtitle": "Host compute, UART reader, WebSocket fanout",
        "specs": "85×56×1.4 mm [KNOWN] · RJ45 + 4× USB-A on ports wall · HDMI/µUSB/audio on power wall · 40-pin GPIO",
        "facts": "S4, S6",
        "materials": ["pi_pcb", "metal", "black_ic", "gold"],
        "explode_cad_mm": [0.0, 0.0, 22.0],
    },
    {
        "id": "enclosure_tray",
        "bom": 6,
        "title": "Blue 3D-Printed Enclosure Tray",
        "subtitle": "Main protective shell with 2× GoPro mounts",
        "specs": "94×65×32.5* mm · 4× corner screw bosses · SD-wall + power-wall GoPro fingers · open ports wall",
        "facts": "S6, M1",
        "materials": ["blue_pla"],
        "explode_cad_mm": [0.0, 0.0, 0.0],
    },
    {
        "id": "gopro_buckle",
        "bom": 7,
        "title": "GoPro Quick-Release Buckle + Thumbscrew",
        "subtitle": "Primary SD-wall mount for corner pole grip",
        "specs": "Black polymer buckle + M5 winged thumbscrew · mates to SD-wall blue 2-finger tab",
        "facts": "S6, M1, M3, M4",
        "materials": ["black_ic"],
        "explode_cad_mm": [-26.0, 0.0, 0.0],
    },
]


def find_openscad() -> str:
    for name in ("openscad.com", "openscad"):
        found = shutil.which(name)
        if found:
            return found
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "OpenSCAD" / "openscad-2021.01" / "openscad.com",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "OpenSCAD" / "openscad.com",
        Path(r"C:\Program Files\OpenSCAD\openscad.com"),
        Path(r"C:\Program Files\OpenSCAD\openscad.exe"),
        Path("/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD"),
        Path("/usr/bin/openscad"),
    ]
    for p in candidates:
        if p.is_file():
            return str(p)
    sys.exit("ERROR: OpenSCAD CLI not found.")


def cad_to_gltf_pos(x: float, y: float, z: float) -> tuple[float, float, float]:
    """Convert CAD mm (Z-up, origin at case corner) -> glTF meters (Y-up, centered)."""
    cx, cy, cz = CAD_CENTER_MM
    return (
        (x - cx) * MM_TO_M,
        (z - cz) * MM_TO_M,
        -(y - cy) * MM_TO_M,
    )


def cad_to_gltf_vec(dx: float, dy: float, dz: float) -> tuple[float, float, float]:
    """Convert CAD mm direction/offset vector -> glTF meters (Y-up)."""
    return (dx * MM_TO_M, dz * MM_TO_M, -dy * MM_TO_M)


def read_stl_triangles(stl_path: Path) -> tuple[list[float], list[float]]:
    """Read binary or ASCII STL and return flat (positions, normals) in glTF Y-up meters."""
    data = stl_path.read_bytes()
    if len(data) < 84:
        return [], []

    # Check if binary STL (80-byte header + uint32 count + count*50 bytes)
    tri_count = struct.unpack_from("<I", data, 80)[0]
    expected_len = 84 + tri_count * 50
    positions: list[float] = []
    normals: list[float] = []

    if len(data) == expected_len and tri_count > 0:
        offset = 84
        unpack_tri = struct.Struct("<12fH").unpack_from
        for _ in range(tri_count):
            nx, ny, nz, x1, y1, z1, x2, y2, z2, x3, y3, z3, _ = unpack_tri(data, offset)
            offset += 50
            p1 = cad_to_gltf_pos(x1, y1, z1)
            p2 = cad_to_gltf_pos(x2, y2, z2)
            p3 = cad_to_gltf_pos(x3, y3, z3)
            # Recompute crisp face normal in glTF Y-up space to guarantee right-handed unit length
            ux, uy, uz = p2[0] - p1[0], p2[1] - p1[1], p2[2] - p1[2]
            vx, vy, vz = p3[0] - p1[0], p3[1] - p1[1], p3[2] - p1[2]
            fnx = uy * vz - uz * vy
            fny = uz * vx - ux * vz
            fnz = ux * vy - uy * vx
            length = math.sqrt(fnx * fnx + fny * fny + fnz * fnz)
            if length > 1e-12:
                fnx /= length
                fny /= length
                fnz /= length
            else:
                fnx, fny, fnz = 0.0, 1.0, 0.0
            positions.extend(p1)
            positions.extend(p2)
            positions.extend(p3)
            normals.extend((fnx, fny, fnz, fnx, fny, fnz, fnx, fny, fnz))
        return positions, normals

    # Fallback: ASCII STL
    text = data.decode("utf-8", errors="ignore")
    verts: list[tuple[float, float, float]] = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("vertex "):
            parts = s.split()
            if len(parts) == 4:
                verts.append(cad_to_gltf_pos(float(parts[1]), float(parts[2]), float(parts[3])))
                if len(verts) == 3:
                    p1, p2, p3 = verts
                    ux, uy, uz = p2[0] - p1[0], p2[1] - p1[1], p2[2] - p1[2]
                    vx, vy, vz = p3[0] - p1[0], p3[1] - p1[1], p3[2] - p1[2]
                    fnx = uy * vz - uz * vy
                    fny = uz * vx - ux * vz
                    fnz = ux * vy - uy * vx
                    length = math.sqrt(fnx * fnx + fny * fny + fnz * fnz)
                    if length > 1e-12:
                        fnx, fny, fnz = fnx / length, fny / length, fnz / length
                    else:
                        fnx, fny, fnz = 0.0, 1.0, 0.0
                    positions.extend(p1)
                    positions.extend(p2)
                    positions.extend(p3)
                    normals.extend((fnx, fny, fnz, fnx, fny, fnz, fnx, fny, fnz))
                    verts.clear()
    return positions, normals


def export_submesh(oscad: str, part_id: str, mat_id: str, out_stl: Path) -> tuple[str, str, Path]:
    cmd = [
        oscad,
        "-o", str(out_stl),
        "--export-format", "binstl",
        "-D", 'view_mode="export_part"',
        "-D", f'export_part="{part_id}"',
        "-D", f'export_mat="{mat_id}"',
        "-D", "$fn=24",
        str(SCAD_FILE),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        # OpenSCAD 2021.01 may not support --export-format binstl; retry without flag
        cmd_fallback = [c for c in cmd if c not in ("--export-format", "binstl")]
        res2 = subprocess.run(cmd_fallback, capture_output=True, text=True)
        if res2.returncode != 0:
            raise RuntimeError(f"OpenSCAD failed for {part_id}/{mat_id}:\n{res2.stderr}")
    return part_id, mat_id, out_stl


def build_glb(mesh_data: dict[str, dict[str, tuple[list[float], list[float]]]]) -> bytes:
    """Pack multi-part multi-material geometry + Explode animation into a valid glTF 2.0 GLB."""
    bin_blob = bytearray()
    buffer_views: list[dict] = []
    accessors: list[dict] = []

    def append_bytes(raw: bytes, target: int | None = None) -> int:
        # 4-byte align
        while len(bin_blob) % 4 != 0:
            bin_blob.append(0)
        offset = len(bin_blob)
        bin_blob.extend(raw)
        bv: dict = {"buffer": 0, "byteOffset": offset, "byteLength": len(raw)}
        if target is not None:
            bv["target"] = target
        idx = len(buffer_views)
        buffer_views.append(bv)
        return idx

    def add_vec3_accessor(floats: list[float], target: int | None = None, compute_minmax: bool = False) -> int:
        raw = struct.pack(f"<{len(floats)}f", *floats)
        bv_idx = append_bytes(raw, target=target)
        count = len(floats) // 3
        acc: dict = {
            "bufferView": bv_idx,
            "componentType": 5126,  # FLOAT
            "count": count,
            "type": "VEC3",
        }
        if compute_minmax and count > 0:
            xs = floats[0::3]
            ys = floats[1::3]
            zs = floats[2::3]
            acc["min"] = [min(xs), min(ys), min(zs)]
            acc["max"] = [max(xs), max(ys), max(zs)]
        idx = len(accessors)
        accessors.append(acc)
        return idx

    def add_scalar_accessor(floats: list[float], compute_minmax: bool = True) -> int:
        raw = struct.pack(f"<{len(floats)}f", *floats)
        bv_idx = append_bytes(raw, target=None)
        acc: dict = {
            "bufferView": bv_idx,
            "componentType": 5126,  # FLOAT
            "count": len(floats),
            "type": "SCALAR",
        }
        if compute_minmax and floats:
            acc["min"] = [min(floats)]
            acc["max"] = [max(floats)]
        idx = len(accessors)
        accessors.append(acc)
        return idx

    # Materials table
    mat_keys = list(MATERIALS.keys())
    mat_index = {k: i for i, k in enumerate(mat_keys)}
    gltf_materials = []
    for k in mat_keys:
        m = MATERIALS[k]
        gltf_materials.append({
            "name": m["name"],
            "pbrMetallicRoughness": {
                "baseColorFactor": m["baseColorFactor"],
                "metallicFactor": m["metallicFactor"],
                "roughnessFactor": m["roughnessFactor"],
            },
            "doubleSided": False,
        })

    # Build meshes and part nodes
    gltf_meshes: list[dict] = []
    gltf_nodes: list[dict] = []

    # Node 0 = Root assembly container
    root_children: list[int] = []
    gltf_nodes.append({
        "name": "lares_sense_assembly",
        "children": root_children,
        "extras": {
            "product": "lares-sense Sensor Unit",
            "cadSource": "docs/device/model.scad",
            "layoutDoc": "docs/device/layout.md",
            "caseSizeMm": [94.0, 65.0, 35.0],
        },
    })

    # Shared animation time input accessor: [0.0, 1.0] seconds
    anim_time_acc = add_scalar_accessor([0.0, 1.0], compute_minmax=True)
    anim_channels: list[dict] = []
    anim_samplers: list[dict] = []

    total_tris = 0
    for part in PARTS:
        pid = part["id"]
        submeshes = mesh_data.get(pid, {})
        primitives: list[dict] = []
        for mat_id in part["materials"]:
            pos, norm = submeshes.get(mat_id, ([], []))
            if not pos:
                continue
            total_tris += len(pos) // 9
            pos_acc = add_vec3_accessor(pos, target=34962, compute_minmax=True)   # ARRAY_BUFFER
            norm_acc = add_vec3_accessor(norm, target=34962, compute_minmax=False)
            primitives.append({
                "attributes": {"POSITION": pos_acc, "NORMAL": norm_acc},
                "material": mat_index[mat_id],
                "mode": 4,  # TRIANGLES
            })

        mesh_idx = len(gltf_meshes)
        gltf_meshes.append({"name": f"{pid}_mesh", "primitives": primitives})

        ex_gltf = list(cad_to_gltf_vec(*part["explode_cad_mm"]))
        node_idx = len(gltf_nodes)
        gltf_nodes.append({
            "name": pid,
            "mesh": mesh_idx,
            "translation": [0.0, 0.0, 0.0],
            "extras": {
                "bom": part["bom"],
                "title": part["title"],
                "subtitle": part["subtitle"],
                "specs": part["specs"],
                "facts": part["facts"],
                "explodeOffset": ex_gltf,
            },
        })
        root_children.append(node_idx)

        # Add translation keyframe channel for built-in "Explode" animation
        out_acc = add_vec3_accessor([0.0, 0.0, 0.0, ex_gltf[0], ex_gltf[1], ex_gltf[2]])
        sampler_idx = len(anim_samplers)
        anim_samplers.append({
            "input": anim_time_acc,
            "interpolation": "LINEAR",
            "output": out_acc,
        })
        anim_channels.append({
            "sampler": sampler_idx,
            "target": {"node": node_idx, "path": "translation"},
        })

    # Pad BIN buffer to 4-byte boundary
    while len(bin_blob) % 4 != 0:
        bin_blob.append(0)

    gltf_json = {
        "asset": {
            "version": "2.0",
            "generator": "lares-sense export-glb.py (from docs/device/model.scad)",
        },
        "scene": 0,
        "scenes": [{"name": "lares_sense_scene", "nodes": [0]}],
        "nodes": gltf_nodes,
        "meshes": gltf_meshes,
        "materials": gltf_materials,
        "animations": [
            {
                "name": "Explode",
                "channels": anim_channels,
                "samplers": anim_samplers,
            }
        ],
        "buffers": [{"byteLength": len(bin_blob)}],
        "bufferViews": buffer_views,
        "accessors": accessors,
    }

    json_bytes = json.dumps(gltf_json, separators=(",", ":")).encode("utf-8")
    while len(json_bytes) % 4 != 0:
        json_bytes += b" "

    total_length = 12 + 8 + len(json_bytes) + 8 + len(bin_blob)
    header = struct.pack("<III", 0x46546C67, 2, total_length)  # 'glTF', version 2
    json_chunk_hdr = struct.pack("<II", len(json_bytes), 0x4E4F534A)  # 'JSON'
    bin_chunk_hdr = struct.pack("<II", len(bin_blob), 0x004E4942)     # 'BIN\0'

    print(f"Packed {len(PARTS)} parts, {len(gltf_materials)} PBR materials, {total_tris:,} triangles")
    return header + json_chunk_hdr + json_bytes + bin_chunk_hdr + bytes(bin_blob)


def main() -> None:
    oscad = find_openscad()
    print(f"Using OpenSCAD: {oscad}")

    tasks: list[tuple[str, str]] = [
        (part["id"], mat_id) for part in PARTS for mat_id in part["materials"]
    ]
    print(f"Exporting {len(tasks)} part/material sub-meshes from {SCAD_FILE.name} in parallel...")

    mesh_data: dict[str, dict[str, tuple[list[float], list[float]]]] = {p["id"]: {} for p in PARTS}
    with tempfile.TemporaryDirectory(prefix="lares_glb_") as tmpdir:
        tmp = Path(tmpdir)
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 4)) as ex:
            futs = [
                ex.submit(export_submesh, oscad, pid, mid, tmp / f"{pid}__{mid}.stl")
                for pid, mid in tasks
            ]
            for fut in concurrent.futures.as_completed(futs):
                pid, mid, stl_path = fut.result()
                pos, norm = read_stl_triangles(stl_path)
                mesh_data[pid][mid] = (pos, norm)

    glb_bytes = build_glb(mesh_data)
    GLB_OUT.write_bytes(glb_bytes)
    print(f"Wrote {GLB_OUT} ({len(glb_bytes):,} bytes / {len(glb_bytes) / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
