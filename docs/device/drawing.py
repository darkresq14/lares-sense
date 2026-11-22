#!/usr/bin/env python3
"""Generate docs/device/drawing.svg — technical drawing of the
sensor unit, desk pose (lid up, ports toward the FRONT viewer).

Source of truth for every fact: docs/device/layout.md (fact IDs
referenced below). This script only RENDERS those facts; geometry edits
happen there first, then here. Regenerate after any PARAMS change:

    python docs/device/drawing.py

Units: mm. Params tagged per provenance:
  KNOWN = vendor datasheet    STD = industry standard    EST = estimate,
caliper-measure and replace.
"""

import math
import os

S = 2.2          # px per mm (views); mounted pictorial is NTS
CASE_L, CASE_W, CASE_H = 100.0, 68.0, 32.0        # EST (outer)
LID_T, BOT_T = 3.0, 3.0                           # EST
PI_L, PI_W, PI_T = 85.6, 56.5, 1.4                # KNOWN / KNOWN / STD
GPIO_H = 8.5                                       # STD (2x20 header)
STANDOFF = 8.0                                     # EST adapter->uRAD gap
URAD = 42.0                                        # KNOWN (uRAD datasheet)
URAD_T, ADP_T = 1.6, 1.6                          # STD PCB
APERTURE = 30.0                                    # EST lid window
IMU_L, IMU_W = 25.0, 15.0                         # EST (GY-521 class)

HOLE = "⊙"   # axis toward viewer
INK, DIM, GHOST, ACC = "#1a1a2e", "#5a5a6e", "#9a9aa8", "#b3541e"


def esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class Svg:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.out = []

    def add(self, s):
        self.out.append(s)

    def line(self, x1, y1, x2, y2, color=INK, w=1.2, dash=None):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" '
                 f'y2="{y2:.1f}" stroke="{color}" stroke-width="{w}"{d}/>')

    def rect(self, x, y, w, h, color=INK, sw=1.4, dash=None, fill="none"):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" '
                 f'height="{h:.1f}" fill="{fill}" stroke="{color}" '
                 f'stroke-width="{sw}"{d}/>')

    def text(self, x, y, t, size=10, color=INK, anchor="start", italic=False):
        st = ' font-style="italic"' if italic else ""
        self.add(f'<text x="{x:.1f}" y="{y:.1f}" font-family="Segoe UI,'
                 f'Helvetica,sans-serif" font-size="{size}" fill="{color}" '
                 f'text-anchor="{anchor}"{st}>{esc(t)}</text>')

    def circle(self, x, y, r, color=INK, sw=1.4, fill="none"):
        self.add(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{fill}" '
                 f'stroke="{color}" stroke-width="{sw}"/>')

    def arrow(self, x1, y1, x2, y2, color=ACC, w=1.6, head=6):
        ang = math.atan2(y2 - y1, x2 - x1)
        self.line(x1, y1, x2, y2, color, w)
        for da in (2.6, -2.6):
            self.line(x2, y2, x2 - head * math.cos(ang - da),
                      y2 - head * math.sin(ang - da), color, w)

    def axis_out(self, x, y, lx, ly, label):
        """⊙ marker with center dot (axis toward viewer) + label."""
        self.circle(x, y, 5, ACC, 1.4)
        self.circle(x, y, 1.7, ACC, 0.0, ACC)
        self.text(lx, ly, label, 10, ACC)

    def dim_h(self, x1, x2, y, label):
        """Horizontal dimension on explicit y; label centered above."""
        self.line(x1, y, x2, y, DIM, 0.8)
        self.arrow(x1, y, x1 + 10, y, DIM, 0.8, 5)
        self.arrow(x2, y, x2 - 10, y, DIM, 0.8, 5)
        self.text((x1 + x2) / 2, y - 6, label, 9, DIM, "middle")

    def dim_v(self, y1, y2, x, label, side="right", label_y=None):
        """Vertical dimension on explicit x; label beside the line."""
        self.line(x, y1, x, y2, DIM, 0.8)
        self.arrow(x, y1, x, y1 + 10, DIM, 0.8, 5)
        self.arrow(x, y2, x, y2 - 10, DIM, 0.8, 5)
        ly = (y1 + y2) / 2 if label_y is None else label_y
        if side == "right":
            self.text(x + 6, ly, label, 9, DIM)
        else:
            self.text(x - 6, ly, label, 9, DIM, "end")

    def save(self, path):
        body = "\n".join(self.out)
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" '
               f'viewBox="0 0 {self.w} {self.h}" '
               f'font-family="Segoe UI,Helvetica,sans-serif">\n'
               f'<rect width="{self.w}" height="{self.h}" fill="white"/>\n'
               f'{body}\n</svg>\n')
        with open(path, "w", encoding="utf-8") as f:
            f.write(svg)


svg = Svg(980, 760)


# ================= TOP VIEW =================================================
# Desk pose, seen from above the LID. Front (bottom of pane) = ports wall,
# left = GPIO wall, right = power wall, back = SD/mount wall (S4, §1).
tx, ty = 30, 88
cw, cl = CASE_W * S, CASE_L * S
cx = tx + cw / 2
svg.text(tx, ty - 16, "TOP — lid up, ports at front (desk pose)", 12)
svg.rect(tx, ty, cw, cl)
# Pi (dashed), centered
pw, pl = PI_W * S, PI_L * S
svg.rect(cx - pw / 2, ty + (cl - pl) / 2, pw, pl, GHOST, 1.0, "5 4")
svg.text(75, 226, "Pi (dashed)", 8, GHOST)
# uRAD board toward SD end (back), shield offset toward SD (S4)
uw = URAD * S
ux, uy = cx - uw / 2, ty + 8
svg.rect(ux, uy, uw, uw)
sh_x = ux + uw / 2 - (uw - 0.20 * uw) / 2 - 0.1 * uw
sh_y = uy + 0.10 * uw
svg.rect(sh_x, sh_y, uw - 0.20 * uw, uw - 0.20 * uw, INK, 1.0)
svg.text(cx, sh_y + (uw - 0.2 * uw) / 2 + 4, "IWR6843AoP", 9, INK, "middle")
# lid aperture (dashed) over the shield; label inside, under the chip name
ap = APERTURE * S
svg.rect(cx - ap / 2, uy + (uw - ap) / 2, ap, ap, DIM, 0.9, "6 4")
svg.text(cx - 4, uy + uw / 2 + 22, "aperture in lid", 8, DIM, "middle")
# micro-USB at ports edge, GPIO-wall corner (S4)
svg.rect(ux + 2, uy + uw - 6, 7.2, 5, ACC, 1.2)
svg.text(ux - 4, uy + uw - 9, "µUSB", 8, ACC, "end")
# IMU on lid inner face, ports/GPIO corner (S2)
svg.rect(tx + 6, ty + cl - IMU_L * S - 4, IMU_W * S, IMU_L * S, ACC, 1.2)
svg.text(tx + 6 + IMU_W * S / 2, ty + cl - IMU_L * S / 2 - 4, "IMU", 8, ACC,
         "middle")
svg.text(tx + 6 + IMU_W * S / 2, ty + cl - IMU_L * S / 2 + 8, "(S2)", 8, ACC,
         "middle")
# mounts: GoPro on SD wall (back), fingers on power wall (right)
svg.rect(cx - 12, ty - 7, 24, 7, INK, 1.2)
svg.text(cx, ty + 6, "GoPro", 8, INK, "middle")
for fy in (ty + cl * 0.35, ty + cl * 0.6):
    svg.rect(tx + cw, fy, 7, 14, INK, 1.0)
svg.text(tx + cw + 10, ty + cl + 12, "fingers", 8, INK)
# radar frame triad (S4, §5): +x to GPIO wall (left), +z to ports (down),
# +y out of lid toward viewer
svg.arrow(188, 262, 158, 262)
svg.text(192, 266, "+x", 10, ACC)
svg.arrow(90, 258, 90, 278)
svg.text(96, 276, "+z", 10, ACC)
svg.axis_out(10, 104, 10, 122, "+y")
# dims: case W below, case L right of the case; uRAD size as note
svg.dim_h(tx, tx + cw, ty + cl + 26, f"case W {CASE_W:.0f}*")
dx_r = tx + cw + 36
svg.line(tx + cw, ty, dx_r, ty, DIM, 0.5)
svg.line(tx + cw, ty + cl, dx_r, ty + cl, DIM, 0.5)
svg.dim_v(ty, ty + cl, dx_r, f"case L {CASE_L:.0f}*")
svg.text(tx, ty + cl + 46, "uRAD board 42×42 mm (datasheet)", 9, DIM)


# ============ SECTION A-A (long axis, viewed from GPIO wall) ================
# Left = SD/mount end, right = ports end. Stack per §1; layer sizes in the
# legend under the pane (keeps the slab area clean).
sx, sy = 460, 110
sw, sh_ = CASE_L * S, CASE_H * S
svg.text(sx, sy - 32, "SECTION A–A — stack, viewed from GPIO wall", 12)
svg.rect(sx, sy, sw, sh_)
b = sy + sh_ - BOT_T * S
svg.line(sx, b, sx + sw, b)
y_pi = b - PI_T * S
svg.rect(sx + 6, y_pi, sw - 12, PI_T * S, GHOST, 1.0)
y_hdr = y_pi - GPIO_H * S
svg.rect(sx + 6, y_hdr, sw * 0.42, GPIO_H * S, GHOST, 1.0)
y_adp = y_hdr - ADP_T * S
svg.rect(sx + 6, y_adp, sw * 0.42, ADP_T * S)
y_sto = y_adp - STANDOFF * S
svg.line(sx + 12, y_adp, sx + 12, y_sto - URAD_T * S, GHOST, 1.0, "4 3")
svg.line(sx + sw - 14, y_adp, sx + sw - 14, y_sto - URAD_T * S, GHOST, 1.0,
         "4 3")
y_ur = y_sto - URAD_T * S
svg.rect(sx + 6, y_ur, sw - 12, URAD_T * S)
svg.line(sx, sy + LID_T * S, sx + sw, sy + LID_T * S, INK, 1.0, "6 4")
apx = sx + 6 + (sw - 12) * 0.30
svg.text(apx, 139, "aperture in lid (over shield)", 8, DIM)
# IMU glued under lid at ports end (S2)
imu_x = sx + sw - IMU_W * S - 14
svg.rect(imu_x, sy + LID_T * S, IMU_W * S, 8, ACC, 1.2)
svg.line(imu_x + IMU_W * S / 2, sy - 4, imu_x + IMU_W * S / 2,
         sy + LID_T * S, DIM, 0.6)
svg.text(imu_x + IMU_W * S / 2 - 4, sy - 8, "IMU (S2) under lid", 8, ACC,
         "end")
# triad: +y up (out of lid), +z right (ports end), +x toward viewer
svg.arrow(436, sy + 18, 436, sy - 6)
svg.text(436, sy - 10, "+y", 10, ACC, "middle")
svg.arrow(404, sy + 38, 428, sy + 38)
svg.text(400, sy + 42, "+z", 10, ACC, "end")
svg.axis_out(418, sy + 18, 410, sy + 12, f"+x {HOLE}")
# dims, note, legend
svg.dim_h(sx, sx + sw, sy + sh_ + 22, f"case L {CASE_L:.0f}*")
svg.dim_v(sy, sy + sh_, sx + sw + 34, f"H {CASE_H:.0f}*")
svg.text(sx, sy + sh_ + 44, "IMU chip +z points INTO the case (S2) — "
         "boresight exits the lid", 9, DIM)
svg.text(sx, sy + sh_ + 62, "stack, bottom→top: bottom 3.0* · Pi 1.4 · "
         "GPIO header 8.5 · adapter 1.6 · standoffs 8.0* · uRAD 1.6 · "
         "lid 3.0*  (mm)", 9, DIM)


# ================= FRONT VIEW (ports wall) ==================================
fx, fy = 60, 460
fw, fh = CASE_W * S, CASE_H * S
svg.text(fx, fy - 14, "FRONT — ports wall", 12)
svg.rect(fx, fy, fw, fh)
svg.line(fx, fy + LID_T * S, fx + fw, fy + LID_T * S, INK, 1.0, "6 4")
p1x = fx + fw * 0.16
svg.rect(p1x, fy + fh - 22, 26, 14)
svg.text(p1x + 13, fy + fh - 26, "RJ45", 8, INK, "middle")
for ux_ in (fx + fw * 0.42, fx + fw * 0.64):
    svg.rect(ux_, fy + fh - 22, 24, 14)
    svg.text(ux_ + 12, fy + fh - 26, "USB×2", 8, INK, "middle")
svg.text(fx + fw + 8, fy + fh + 6, "ports end = radar +z (up, mounted)",
         8, DIM)
svg.dim_h(fx, fx + fw, fy + fh + 26, f"W {CASE_W:.0f}*")
# triad: +z toward viewer, +x to GPIO wall (viewer's left), +y up
svg.arrow(282, 500, 258, 500)
svg.text(286, 504, "+x", 10, ACC)
svg.arrow(270, 496, 270, 474)
svg.text(270, 470, "+y", 10, ACC, "middle")
svg.axis_out(270, 522, 280, 526, f"+z {HOLE}")


# ================= MOUNTED PICTORIAL (NTS) ==================================
mx, my = 420, 430
svg.text(mx, my - 14, "MOUNTED — corner install (not to scale)", 12)
tilt = math.radians(15)
ox, oy = mx + 150, my + 150          # case center
ch2, cw2 = 60, 150                   # case short / long axis px


def rot2(px, py):
    """Case-plane coords -> screen. +px = out of the lid (into the room,
    tilted 15° down); +py = toward the ports end (up, leaning)."""
    c, s = math.cos(tilt), math.sin(tilt)
    return ox - px * c + py * s, oy + px * s - py * c


corners = [rot2(-ch2 / 2, cw2 / 2), rot2(ch2 / 2, cw2 / 2),
           rot2(ch2 / 2, -cw2 / 2), rot2(-ch2 / 2, -cw2 / 2)]
for i in range(4):
    x1, y1 = corners[i]
    x2, y2 = corners[(i + 1) % 4]
    svg.line(x1, y1, x2, y2)
# lid face = +px edge (corners[1]..[2]); ports face = +py edge ([0]..[1])
lid_mid = ((corners[1][0] + corners[2][0]) / 2, (corners[1][1] + corners[2][1]) / 2)
svg.text(lid_mid[0] - 34, 530, "lid → room", 9, INK, "end")
tp = ((corners[0][0] + corners[1][0]) / 2, (corners[0][1] + corners[1][1]) / 2)
svg.text(tp[0], tp[1] - 14, "ports ≈ ceiling", 9, DIM, "middle")
# grip + pole below (mount end = SD end = -py, M1)
gx, gy = rot2(0, -cw2 / 2 - 6)
svg.line(gx, gy, gx - 14, gy + 30, INK, 3)
svg.line(gx - 14, gy + 30, gx - 76, gy + 74, INK, 4)
svg.text(gx - 18, gy + 18, "grip", 8, INK, "end")
svg.text(gx - 110, gy + 62, "pole", 8, INK)
# boresight + FOV into the room, 15° down (M2, M3): starts at lid face
bx, by = rot2(ch2 / 2 + 2, 0)
dx, dy = -math.cos(tilt), math.sin(tilt)
svg.arrow(bx, by, bx + dx * 118, by + dy * 118)
svg.text(bx + dx * 122 - 4, by + dy * 122 + 18, "+y boresight", 9, ACC, "end")
for spread in (-30, 30):
    a = tilt + math.radians(spread)
    svg.line(bx, by, bx - math.cos(a) * 100, by + math.sin(a) * 100,
             DIM, 0.7, "4 4")
svg.text(bx - 140, by + 112, "±60° FOV", 8, DIM, "end")
# +z toward ports end, inside the case, along its long axis
z1 = rot2(0, cw2 / 2 - 45)
z2 = rot2(0, cw2 / 2 - 15)
svg.arrow(z1[0], z1[1], z2[0], z2[1])
svg.text(z1[0] - 8, z1[1] - 2, "+z", 10, ACC, "end")
# +x toward viewer (GPIO-wall side — the wall without the barrel jack)
# +x toward viewer (GPIO-wall side — the wall without the barrel jack):
# marker at the case, explanation exiled to the top-right margin
svg.axis_out(bx - 24, by - 32, 0, 0, "")
svg.text(bx - 32, by - 20, "+x", 10, ACC, "end")
svg.text(655, 448, f"{HOLE} = +x toward viewer (no-barrel-jack wall)",
         9, ACC)


# ================= TITLE BLOCK ==============================================
bx_, by_ = 640, 470
svg.rect(bx_, by_, 320, 220, INK, 1.4)
svg.text(bx_ + 14, by_ + 26, "lares-sense sensor unit", 14)
svg.text(bx_ + 14, by_ + 44, "technical drawing — desk pose", 10, DIM)
rows = [
    ("source of truth", "docs/device/layout.md (facts S1–S8, M1–M4)"),
    ("frames", "radar: +Y boresight, +X→GPIO-wall side, +Z→ports end"),
    ("vendor docs", "uRAD Industrial manual p.4–5; TI frame — see"),
    ("", "docs/research/device-drawing-tool.md addendum"),
    ("generated by", "docs/device/drawing.py (edit PARAMS there)"),
    ("scale", "views 1:~4.5 (2.2 px/mm); mounted NTS"),
    ("note", "* = EST — caliper-measure, update PARAMS, regenerate"),
    ("date", "2026-11-08 (photo + vendor-doc audit)"),
]
yy = by_ + 68
for k, v in rows:
    svg.text(bx_ + 14, yy, k, 9, DIM)
    svg.text(bx_ + 92, yy, v, 9, INK)
    yy += 19

svg.text(30, 745, "Radar frame per uRAD manual: for an observer behind the "
         "radar, +X right, +Z up, +Y front. Facing the unit from the room, "
         "+X is on your LEFT.", 9, DIM)

out = os.path.join(os.path.dirname(__file__), "drawing.svg")
svg.save(out)
print(f"wrote {out}")
