#!/usr/bin/env python3
"""Render technical drawings and 3D STL exports from docs/hardware/device-model.scad.

Outputs (all in docs/hardware/):
  - device-technical-drawing.png   Multi-view engineering assembly drawing sheet
                                   (exploded assembly + BOM, assembled 3D, open-lid
                                   internal view, orthographic views, mounted pose)
  - device-assembly.stl            Full 3D solid assembly (GitHub 3D viewer)
  - device-enclosure.stl           3D-printable enclosure tray + lid laid flat

Usage:
    python docs/hardware/render-drawing.py
    python docs/hardware/render-drawing.py --skip-stl   # fast PNG-only iteration
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
SCAD_FILE = HERE / "device-model.scad"
SHEET_OUT = HERE / "device-technical-drawing.png"
STL_ASSEMBLY_OUT = HERE / "device-assembly.stl"
STL_ENCLOSURE_OUT = HERE / "device-enclosure.stl"


def find_openscad() -> str:
    """Locate openscad CLI on PATH or standard Windows/macOS/Linux locations."""
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

    sys.exit(
        "ERROR: OpenSCAD CLI not found. Install OpenSCAD or add openscad to PATH."
    )


def run_openscad(oscad: str, out_path: Path, extra_args: list[str]) -> None:
    cmd = [oscad, "-o", str(out_path)] + extra_args + [str(SCAD_FILE)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"OpenSCAD failed for {out_path.name}:\n{res.stderr}", file=sys.stderr)
        sys.exit(res.returncode)


def crop_whitespace(img: Image.Image, pad: int = 16, bg_thresh: int = 248) -> Image.Image:
    """Trim uniform near-white border around an OpenSCAD Tomorrow-scheme render."""
    rgb = img.convert("RGB")
    w, h = rgb.size
    px = rgb.load()
    min_x, min_y, max_x, max_y = w, h, 0, 0
    # Sample every 2px for speed
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            r, g, b = px[x, y]
            if r < bg_thresh or g < bg_thresh or b < bg_thresh:
                if x < min_x:
                    min_x = x
                if y < min_y:
                    min_y = y
                if x > max_x:
                    max_x = x
                if y > max_y:
                    max_y = y
    if max_x <= min_x or max_y <= min_y:
        return rgb
    x0 = max(0, min_x - pad)
    y0 = max(0, min_y - pad)
    x1 = min(w, max_x + pad)
    y1 = min(h, max_y + pad)
    return rgb.crop((x0, y0, x1, y1))


def fit_into(img: Image.Image, box_w: int, box_h: int) -> Image.Image:
    """Scale image to fit inside (box_w, box_h) preserving aspect ratio."""
    scale = min(box_w / img.width, box_h / img.height)
    nw = max(1, int(img.width * scale))
    nh = max(1, int(img.height * scale))
    return img.resize((nw, nh), Image.Resampling.LANCZOS)


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        ["C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
        if bold
        else ["C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    )
    for c in candidates:
        if os.path.isfile(c):
            return ImageFont.truetype(c, size)
    return ImageFont.load_default()


def draw_callout(
    draw: ImageDraw.ImageDraw,
    num: str,
    target_xy: tuple[int, int],
    bubble_xy: tuple[int, int],
    label: str,
    font_num,
    font_lbl,
    align: str = "left",
) -> None:
    """Draw an engineering BOM callout bubble with leader line and label."""
    tx, ty = target_xy
    bx, by = bubble_xy
    r = 16
    ink = (26, 26, 46)
    acc = (179, 84, 30)

    # Leader line + dot at target
    draw.line([(tx, ty), (bx, by)], fill=ink, width=2)
    draw.ellipse([(tx - 4, ty - 4), (tx + 4, ty + 4)], fill=acc, outline=ink, width=1)

    # Numbered circle
    draw.ellipse([(bx - r, by - r), (bx + r, by + r)], fill=(255, 255, 255), outline=acc, width=3)
    bbox = draw.textbbox((0, 0), num, font=font_num)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((bx - tw / 2, by - th / 2 - 2), num, fill=acc, font=font_num)

    # Label text beside bubble
    if align == "left":
        draw.text((bx + r + 8, by - 10), label, fill=ink, font=font_lbl)
    else:
        lb = draw.textbbox((0, 0), label, font=font_lbl)
        lw = lb[2] - lb[0]
        draw.text((bx - r - 8 - lw, by - 10), label, fill=ink, font=font_lbl)


def draw_dim_h(draw: ImageDraw.ImageDraw, x1: int, x2: int, y: int, text: str, font) -> None:
    c = (75, 80, 95)
    draw.line([(x1, y), (x2, y)], fill=c, width=2)
    draw.line([(x1, y - 7), (x1, y + 7)], fill=c, width=2)
    draw.line([(x2, y - 7), (x2, y + 7)], fill=c, width=2)
    bb = draw.textbbox((0, 0), text, font=font)
    tw = bb[2] - bb[0]
    draw.text(((x1 + x2 - tw) / 2, y - 22), text, fill=c, font=font)


def draw_dim_v(draw: ImageDraw.ImageDraw, x: int, y1: int, y2: int, text: str, font) -> None:
    c = (75, 80, 95)
    draw.line([(x, y1), (x, y2)], fill=c, width=2)
    draw.line([(x - 7, y1), (x + 7, y1)], fill=c, width=2)
    draw.line([(x - 7, y2), (x + 7, y2)], fill=c, width=2)
    draw.text((x + 8, (y1 + y2) / 2 - 9), text, fill=c, font=font)


def compose_sheet(views: dict[str, Path]) -> None:
    """Compose a 3000x2000px ANSI-D-style engineering assembly drawing sheet."""
    W, H = 3000, 2000
    BG = (255, 255, 255)
    INK = (26, 26, 46)
    MUTED = (90, 95, 110)
    BORDER = (40, 46, 66)
    PANEL_BG = (250, 251, 253)
    ACC = (179, 84, 30)

    sheet = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(sheet)

    f_title = load_font(34, bold=True)
    f_h2 = load_font(22, bold=True)
    f_body = load_font(18, bold=False)
    f_body_b = load_font(18, bold=True)
    f_small = load_font(15, bold=False)
    f_num = load_font(18, bold=True)

    # Outer engineering sheet frame
    M = 28
    draw.rectangle([(M, M), (W - M, H - M)], outline=BORDER, width=4)
    draw.rectangle([(M + 8, M + 8), (W - M - 8, H - M - 8)], outline=BORDER, width=1)

    # Layout grid inside inner frame (x: 44..2956, y: 44..1956)
    # Left column (x: 44..1280): Panel A = Exploded 3D Assembly (y: 44..1480)
    #                            Panel D = Orthographic Views (y: 1480..1956)
    # Middle column (x: 1280..2160): Panel B = Assembled 3D Isometric (y: 44..990)
    #                                Panel E = Mounted Corner-Install Pose (y: 990..1956)
    # Right column (x: 2160..2956): Panel C = Open-Lid Internal View (y: 44..990)
    #                               Panel F = BOM + Title Block (y: 990..1956)

    panels = [
        (44, 44, 1280, 1480, "A. EXPLODED ASSEMBLY VIEW — EXACT PIECE STACK (BOTTOM → LID)"),
        (1280, 44, 2160, 990, "B. ASSEMBLED ISOMETRIC — DESK POSE & RADAR FRAME"),
        (2160, 44, 2956, 990, "C. OPEN-LID INSPECTION VIEW (MATCHES MACRO PHOTOS)"),
        (44, 1480, 1280, 1956, "D. ORTHOGRAPHIC PROJECTIONS (mm · * = EST)"),
        (1280, 990, 2160, 1956, "E. MOUNTED POSE — CORNER INSTALL (~15° DOWNWARD TILT)"),
    ]

    for x0, y0, x1, y1, title in panels:
        draw.rectangle([(x0, y0), (x1, y1)], fill=PANEL_BG, outline=BORDER, width=2)
        draw.rectangle([(x0, y0), (x1, y0 + 38)], fill=(238, 242, 248), outline=BORDER, width=1)
        draw.text((x0 + 14, y0 + 7), title, fill=INK, font=f_h2)

    def paste_view(key: str, x0: int, y0: int, x1: int, y1: int, pad: int = 18):
        im = crop_whitespace(Image.open(views[key]), pad=10)
        fitted = fit_into(im, (x1 - x0) - 2 * pad, (y1 - y0) - 2 * pad)
        px = x0 + ((x1 - x0) - fitted.width) // 2
        py = y0 + ((y1 - y0) - fitted.height) // 2
        sheet.paste(fitted, (px, py))
        return (px, py, fitted.width, fitted.height)

    # Panel A: Exploded 3D assembly (shifted slightly left to leave room for callouts on right)
    ex_box = paste_view("exploded", 56, 90, 910, 1460, pad=8)
    ex_x, ex_y, ex_w, ex_h = ex_box
    # Numbered BOM callouts (1..7) aiming directly onto each piece in the exploded stack
    callouts = [
        ("1", (ex_x + int(ex_w * 0.68), ex_y + int(ex_h * 0.14)), (942, 195), "Blue 3D-Printed Lid + 4× M2.5 Screws (keyhole aperture)"),
        ("2", (ex_x + int(ex_w * 0.78), ex_y + int(ex_h * 0.25)), (942, 365), "MPU-9150 Breakout (glued inside lid, chip +z into case, S2)"),
        ("3", (ex_x + int(ex_w * 0.56), ex_y + int(ex_h * 0.37)), (942, 545), "uRAD Industrial v2.0 IWR6843AoP (42×42 mm, µUSB at GPIO corner)"),
        ("4", (ex_x + int(ex_w * 0.55), ex_y + int(ex_h * 0.54)), (942, 745), "Anteral uRAD IWR-AWR Pi Adapter (blue PCB + 4× M2.5 standoffs)"),
        ("5", (ex_x + int(ex_w * 0.68), ex_y + int(ex_h * 0.72)), (942, 955), "Raspberry Pi 3 Model B (85×56 mm, RJ45 + 4× USB + 40-pin GPIO)"),
        ("6", (ex_x + int(ex_w * 0.66), ex_y + int(ex_h * 0.87)), (942, 1165), "Blue 3D-Printed Enclosure Tray (2× GoPro finger mounts)"),
        ("7", (ex_x + int(ex_w * 0.14), ex_y + int(ex_h * 0.78)), (942, 1340), "Black GoPro Quick-Release Buckle + M5 Thumbscrew (SD wall)"),
    ]
    for num, txy, bxy, lbl in callouts:
        lines = textwrap.wrap(lbl, width=32)
        draw_callout(draw, num, txy, bxy, lines[0], f_num, f_small, align="left")
        for li, extra_line in enumerate(lines[1:], start=1):
            draw.text((bxy[0] + 24, bxy[1] - 10 + li * 20), extra_line, fill=MUTED, font=f_small)

    # Panel B: Assembled Isometric + Radar Frame legend
    paste_view("assembled", 1290, 90, 2150, 900, pad=14)
    draw.text((1302, 912), "Radar Frame:  GREEN +Y = boresight (out lid)  ·  RED +X = GPIO wall  ·  BLUE +Z = ports wall", fill=INK, font=f_small)
    draw.text((1302, 938), "Desk pose: lid up, ports (+X_cad) facing front-right; power wall (-Y_cad) front-left with blue GoPro fingers.", fill=MUTED, font=f_small)

    # Panel C: Open-Lid Internal View
    paste_view("open_lid", 2170, 90, 2946, 900, pad=14)
    draw.text((2182, 912), "Lid hinged open behind GPIO wall: MPU-9150 sits at ports/GPIO corner (S2) with 4 wires.", fill=INK, font=f_small)
    draw.text((2182, 938), "uRAD µUSB at ports/GPIO corner (S4); SOP2 DIP switch + reset under aperture notch.", fill=MUTED, font=f_small)

    # Panel D: 3 Orthographic Projections (Top, Front/Ports, Side/Power — enclosure only for clean dims)
    o_top = paste_view("ortho_top", 64, 1550, 470, 1900, pad=12)
    draw.text((o_top[0], 1525), "TOP (Lid Up)", fill=INK, font=f_body_b)
    draw_dim_h(draw, o_top[0], o_top[0] + o_top[2], o_top[1] + o_top[3] + 28, "Case L = 94.0* mm (+15 GoPro)", f_small)

    o_front = paste_view("ortho_front", 510, 1560, 840, 1895, pad=12)
    draw.text((o_front[0], 1525), "FRONT (Ports Wall)", fill=INK, font=f_body_b)
    draw_dim_h(draw, o_front[0], o_front[0] + o_front[2], o_front[1] + o_front[3] + 28, "Case W = 65.0* mm", f_small)

    o_side = paste_view("ortho_side", 870, 1560, 1215, 1895, pad=12)
    draw.text((o_side[0], 1525), "SIDE (Power Wall)", fill=INK, font=f_body_b)
    draw_dim_v(draw, o_side[0] + o_side[2] + 10, o_side[1], o_side[1] + o_side[3], "H 35*", f_small)

    # Panel E: Mounted Corner-Install Pose
    paste_view("mounted", 1295, 1040, 2145, 1875, pad=14)
    draw.text((1302, 1890), "Mounted (M1–M4): SD/mount end down on GoPro grip; ports end up (~ceiling); lid faces room,", fill=INK, font=f_small)
    draw.text((1302, 1916), "boresight (+Y) tilted ~15° down along corner bisector; pole behind boresight outside ±60° FOV.", fill=MUTED, font=f_small)

    # Panel F: BOM Table (top half) + Engineering Title Block (bottom half)
    fx0, fy0, fx1, fy1 = 2160, 990, 2956, 1956
    draw.rectangle([(fx0, fy0), (fx1, fy1)], fill=(255, 255, 255), outline=BORDER, width=2)
    draw.rectangle([(fx0, fy0), (fx1, fy0 + 38)], fill=(238, 242, 248), outline=BORDER, width=1)
    draw.text((fx0 + 14, fy0 + 7), "F. BILL OF MATERIALS (BOM) & TITLE BLOCK", fill=INK, font=f_h2)

    bom_rows = [
        ("1", "3D-Printed Enclosure Lid", "Blue PLA/PETG · 94×65×2.5* · keyhole + 4× M2.5"),
        ("2", "MPU-9150 IMU Breakout", "GY-521 class · glued inside lid · chip +z into case"),
        ("3", "uRAD Industrial v2.0", "TI IWR6843AoP · 42×42×1.6 mm [KNOWN] · µUSB"),
        ("4", "Anteral Pi Adapter", "Blue PCB · 2×20 GPIO + 2×5 CONNFLY + 4× standoffs"),
        ("5", "Raspberry Pi 3 Model B", "85×56×1.4 mm [KNOWN] · RJ45, 4× USB, HDMI, µUSB"),
        ("6", "3D-Printed Enclosure Tray", "Blue PLA/PETG · 94×65×32.5* · 2× GoPro mounts"),
        ("7", "GoPro Mount Hardware", "Black quick-release buckle + M5 thumbscrew"),
    ]
    by = fy0 + 48
    draw.rectangle([(fx0 + 12, by), (fx1 - 12, by + 30)], fill=(242, 244, 248), outline=BORDER, width=1)
    draw.text((fx0 + 22, by + 5), "#", fill=INK, font=f_body_b)
    draw.text((fx0 + 58, by + 5), "Part Name", fill=INK, font=f_body_b)
    draw.text((fx0 + 305, by + 5), "Key Specs / Provenance", fill=INK, font=f_body_b)
    by += 30
    for idx, part, spec in bom_rows:
        draw.rectangle([(fx0 + 12, by), (fx1 - 12, by + 44)], outline=(205, 210, 220), width=1)
        draw.text((fx0 + 22, by + 11), idx, fill=ACC, font=f_body_b)
        draw.text((fx0 + 58, by + 11), part, fill=INK, font=f_body_b)
        draw.text((fx0 + 305, by + 13), spec, fill=MUTED, font=f_small)
        by += 44

    # Engineering Title Block at bottom of Panel F
    ty0 = by + 24
    draw.rectangle([(fx0 + 12, ty0), (fx1 - 12, fy1 - 12)], fill=(248, 250, 253), outline=BORDER, width=3)
    draw.line([(fx0 + 12, ty0 + 82), (fx1 - 12, ty0 + 82)], fill=BORDER, width=2)
    draw.text((fx0 + 28, ty0 + 12), "LARES-SENSE SENSOR UNIT", fill=INK, font=f_title)
    draw.text((fx0 + 28, ty0 + 52), "MECHANICAL ASSEMBLY & TECHNICAL DRAWING (3D CAD)", fill=ACC, font=f_body_b)

    meta = [
        ("3D CAD Source:", "docs/hardware/device-model.scad (OpenSCAD parametric)"),
        ("3D Exports:", "device-assembly.stl (viewer) · device-enclosure.stl (printable)"),
        ("Fact Truth:", "docs/hardware/device-layout.md (facts S1–S8, M1–M4)"),
        ("Radar Frame:", "+Y boresight (out lid) · +X → GPIO wall · +Z → ports wall"),
        ("IMU Axis Map:", "accel (x,−z,−y) · mag (x,z,y) · chip +z INTO case (S2, S3)"),
        ("Units / Notes:", "Millimeters (mm) · * = photo estimate (caliper to upgrade)"),
        ("Regenerate:", "python docs/hardware/render-drawing.py"),
    ]
    my = ty0 + 96
    for k, v in meta:
        draw.text((fx0 + 24, my), k, fill=MUTED, font=f_body_b)
        draw.text((fx0 + 178, my + 1), v, fill=INK, font=f_small)
        my += 36

    sheet.save(SHEET_OUT, "PNG", optimize=True)
    print(f"Wrote {SHEET_OUT} ({W}x{H})")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-stl", action="store_true", help="Skip STL export (faster PNG iteration)")
    args = parser.parse_args()

    oscad = find_openscad()
    print(f"Using OpenSCAD: {oscad}")

    with tempfile.TemporaryDirectory(prefix="lares_cad_") as tmpdir:
        tmp = Path(tmpdir)
        views = {
            "exploded": tmp / "exploded.png",
            "assembled": tmp / "assembled.png",
            "open_lid": tmp / "open_lid.png",
            "ortho_top": tmp / "ortho_top.png",
            "ortho_front": tmp / "ortho_front.png",
            "ortho_side": tmp / "ortho_side.png",
            "mounted": tmp / "mounted.png",
        }

        render_specs = [
            ("exploded", [
                "--imgsize=1400,1800", "--colorscheme=Tomorrow", "--projection=p",
                "--autocenter", "--viewall", "--camera=0,0,0,64,0,35,440",
                "-D", 'view_mode="exploded"', "-D", "explode_gap=26", "-D", "show_buckle=true",
            ]),
            ("assembled", [
                "--imgsize=1400,1100", "--colorscheme=Tomorrow", "--projection=p",
                "--autocenter", "--viewall", "--camera=0,0,0,56,0,42,250",
                "-D", 'view_mode="assembled"', "-D", "show_axes=true", "-D", "show_buckle=true",
            ]),
            ("open_lid", [
                "--imgsize=1400,1200", "--colorscheme=Tomorrow", "--projection=o",
                "--autocenter", "--viewall", "--camera=0,0,0,0,0,0,280",
                "-D", 'view_mode="open_lid"', "-D", "show_buckle=true",
            ]),
            ("ortho_top", [
                "--imgsize=900,700", "--colorscheme=Tomorrow", "--projection=o",
                "--autocenter", "--viewall", "--camera=0,0,0,0,0,0,220",
                "-D", 'view_mode="assembled"', "-D", "show_buckle=false",
            ]),
            ("ortho_front", [
                "--imgsize=800,600", "--colorscheme=Tomorrow", "--projection=o",
                "--autocenter", "--viewall", "--camera=0,0,0,90,0,90,180",
                "-D", 'view_mode="assembled"', "-D", "show_buckle=false",
            ]),
            ("ortho_side", [
                "--imgsize=900,600", "--colorscheme=Tomorrow", "--projection=o",
                "--autocenter", "--viewall", "--camera=0,0,0,90,0,0,220",
                "-D", 'view_mode="assembled"', "-D", "show_buckle=false",
            ]),
            ("mounted", [
                "--imgsize=1200,1200", "--colorscheme=Tomorrow", "--projection=p",
                "--autocenter", "--viewall", "--camera=0,0,0,72,0,34,360",
                "-D", 'view_mode="mounted"', "-D", "show_axes=true", "-D", "show_fov=true",
            ]),
        ]

        for key, extra in render_specs:
            print(f"Rendering {key}...")
            run_openscad(oscad, views[key], extra)

        compose_sheet(views)

    if not args.skip_stl:
        print("Exporting 3D STL: device-assembly.stl ...")
        run_openscad(oscad, STL_ASSEMBLY_OUT, [
            "-D", 'view_mode="stl_assembly"', "-D", "show_buckle=true", "-D", "$fn=24",
        ])
        print(f"Wrote {STL_ASSEMBLY_OUT} ({STL_ASSEMBLY_OUT.stat().st_size:,} bytes)")

        print("Exporting 3D-printable STL: device-enclosure.stl ...")
        run_openscad(oscad, STL_ENCLOSURE_OUT, [
            "-D", 'view_mode="stl_enclosure"', "-D", "$fn=32",
        ])
        print(f"Wrote {STL_ENCLOSURE_OUT} ({STL_ENCLOSURE_OUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
