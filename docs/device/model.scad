// =============================================================================
// lares-sense Sensor Unit — Parametric 3D CAD Assembly (OpenSCAD)
// =============================================================================
// Single 3D mechanical source of truth for the physical unit.
// Fact provenance & coordinate frames: docs/device/layout.md (S1-S8, M1-M4).
//
// CAD Coordinate System (Desk Pose — bottom plate on Z=0, lid on top):
//   +X  SD/mount wall (0)  ->  Ports wall (CASE_L)     [= Radar +Z, up when mounted]
//   +Y  Power wall (0)     ->  GPIO wall (CASE_W)      [= Radar +X, right from behind]
//   +Z  Bottom plate (0)   ->  Lid top (CASE_H)        [= Radar +Y, boresight out lid]
//   Right-handed check: (+X_radar = +Y_cad) x (+Y_radar = +Z_cad) = (+Z_radar = +X_cad).
//
// Dimension provenance tags:
//   [KNOWN] = vendor datasheet / official mechanical drawing
//   [STD]   = industry standard component dimension
//   [EST]   = estimated from photos (docs/device/photos/); caliper-verify to upgrade
//
// Modes (override via CLI: openscad -D 'view_mode="exploded"' ...):
//   "assembled"      Closed unit in desk pose (default)
//   "exploded"       Exploded vertical assembly showing every piece + guide rods
//   "open_lid"       Lid lifted beside tray (matches macro photos 115251920 / 115314449)
//   "mounted"        Corner-mounted pose: ports up, lid facing room, tilted 15 deg down
//   "cutaway"        Section cutaway along the power wall to reveal internal stack
//   "stl_assembly"   Full solid assembly for interactive 3D STL viewer (GitHub)
//   "stl_enclosure"  3D-printable enclosure tray + lid laid flat side-by-side
// =============================================================================

view_mode   = "assembled";
show_axes   = false;
show_fov    = false;
show_buckle = true;
explode_gap = 24.0;   // vertical separation between exploded layers (mm)

$fn = 36;

// -----------------------------------------------------------------------------
// 1. MASTER PARAMETERS (mm)
// -----------------------------------------------------------------------------

// Enclosure outer envelope [EST from Pi 3B + photo proportions]
CASE_L   = 94.0;      // X: SD/mount wall (0) to ports wall (94)
CASE_W   = 65.0;      // Y: Power wall (0) to GPIO wall (65)
CASE_H   = 35.0;      // Z: Bottom exterior (0) to lid exterior (35)
WALL_T   = 3.0;       // [EST] Side wall thickness
BOT_T    = 2.5;       // [EST] Bottom plate thickness
LID_T    = 2.5;       // [EST] Removable screwed lid thickness (S8)
CASE_R   = 3.0;       // [EST] Outer vertical corner fillet radius
BOSS_D   = 6.4;       // [EST] Corner screw boss diameter
SCREW_D  = 2.5;       // [STD] M2.5 corner lid screws

// Raspberry Pi 3 Model B [KNOWN: official RPi 3B mechanical drawing]
PI_L     = 85.0;
PI_W     = 56.0;
PI_T     = 1.4;       // [STD] PCB thickness
PI_R     = 3.0;       // Corner radius
PI_X0    = 6.0;       // [EST] Pi SD-edge inset from X=0 (SD wall)
PI_Y0    = 4.5;       // [EST] Pi power-edge inset from Y=0 (power wall)
PI_Z0    = BOT_T + 2.0; // [EST] Pi bottom rests on 2mm tray ribs

// Anteral uRAD IWR-AWR Raspberry Pi Adapter (blue PCB) [EST + adapter doc]
ADP_L    = 58.0;      // [EST] X length (SD end to just before USB/RJ45 jacks)
ADP_W    = 55.0;      // [EST] Y width (spans GPIO header toward power wall)
ADP_T    = 1.6;       // [STD] PCB thickness
ADP_X0   = PI_X0 + 2.0;
ADP_Y0   = PI_Y0 + 0.5;
GPIO_H   = 11.0;      // [STD] 2x20 header + female socket stack height
ADP_Z0   = PI_Z0 + PI_T + GPIO_H; // 16.9 mm

// uRAD Industrial v2.0 IWR6843AoP Radar Board [KNOWN: uRAD datasheet 42x42mm]
URAD_L   = 42.0;      // [KNOWN]
URAD_W   = 42.0;      // [KNOWN]
URAD_T   = 1.6;       // [STD]
STANDOFF_H = 10.0;    // [EST from photo 115314449] Metal hex standoffs
URAD_X0  = ADP_X0 + 10.0; // [EST] Offset slightly toward SD end (S4)
URAD_Y0  = PI_Y0 + 5.5;   // [EST] Leaves room for GPIO header along +Y
URAD_Z0  = ADP_Z0 + ADP_T + STANDOFF_H; // 28.5 mm

// IWR6843AoP Chip on uRAD board [KNOWN: TI SWRS237C 15x15mm BGA/AoP]
CHIP_S   = 15.0;
CHIP_H   = 1.6;
CHIP_CX  = URAD_X0 + 16.5; // [EST from photo 115300618] Slightly toward SD end
CHIP_CY  = URAD_Y0 + 18.5; // [EST] Slightly toward power wall

// Lid Keyhole Aperture over IWR6843AoP + DIP switch/reset [EST from photos]
AP_X0    = CHIP_CX - 11.0;
AP_Y0    = CHIP_CY - 10.5;
AP_L     = 22.0;      // Main rectangular opening X
AP_W     = 21.0;      // Main rectangular opening Y
NOTCH_L  = 11.0;      // Power-wall (-Y) notch for DIP switch & reset button
NOTCH_W  = 6.0;
NOTCH_X0 = CHIP_CX - 5.0;

// MPU-9150 Breakout (GY-521 class) glued to lid inner face [EST + S2, S8]
IMU_L    = 16.0;      // X dimension (along ports end)
IMU_W    = 21.0;      // Y dimension (8-pin header along +X edge)
IMU_T    = 1.6;       // [STD] PCB thickness
IMU_X0   = CASE_L - WALL_T - IMU_L - 2.0; // Ports-end corner (S2)
IMU_Y0   = CASE_W - WALL_T - IMU_W - 2.0; // GPIO-wall corner (S2)
IMU_Z0   = CASE_H - LID_T - IMU_T;        // Glued under lid; chip faces -Z (into case)

// GoPro 2-Finger Mounts [STD GoPro finger spacing]
GP_FINGER_T   = 3.0;  // Standard GoPro tab thickness
GP_FINGER_GAP = 3.2;  // Gap between the 2 tabs
GP_FINGER_H   = 15.0; // Protrusion length
GP_FINGER_W   = 15.0; // Tab width / outer diameter
GP_HOLE_D     = 5.2;  // M5 thumbscrew clearance hole

// -----------------------------------------------------------------------------
// 2. COLOR PALETTE (matches real hardware in docs/device/photos/)
// -----------------------------------------------------------------------------
C_CASE   = [0.09, 0.45, 0.78]; // Royal blue 3D-printed PLA/PETG enclosure
C_PI_PCB = [0.13, 0.44, 0.22]; // Raspberry Pi green PCB
C_ADP    = [0.08, 0.32, 0.62]; // Anteral adapter blue PCB
C_URAD   = [0.16, 0.56, 0.26]; // uRAD Industrial bright green PCB
C_IMU    = [0.08, 0.16, 0.36]; // MPU-9150 dark navy breakout PCB
C_METAL  = [0.76, 0.78, 0.80]; // Brushed stainless / tin shield (RJ45, USB, standoffs)
C_CHIP   = [0.22, 0.24, 0.26]; // IWR6843AoP dark grey package
C_IC     = [0.14, 0.14, 0.16]; // Black plastic ICs, headers, DIP switch, GoPro buckle
C_GOLD   = [0.85, 0.68, 0.20]; // Gold-plated GPIO pins / pads
C_WHITE  = [0.92, 0.92, 0.92]; // Reset button / silkscreen accents

// -----------------------------------------------------------------------------
// 3. HELPER PRIMITIVES
// -----------------------------------------------------------------------------
module rounded_box(l, w, h, r) {
    hull() {
        for (x = [r, l - r], y = [r, w - r])
            translate([x, y, 0]) cylinder(r = r, h = h);
    }
}

module gopro_two_fingers() {
    // Centered in Y (2 fingers straddling Y=0), protruding along -X from X=0
    for (sy = [-1, 1]) {
        translate([0, sy * (GP_FINGER_GAP + GP_FINGER_T) / 2 - GP_FINGER_T / 2, 0])
        difference() {
            hull() {
                translate([-2, 0, -GP_FINGER_W / 2])
                    cube([2, GP_FINGER_T, GP_FINGER_W]);
                translate([-GP_FINGER_H + GP_FINGER_W / 2, 0, 0])
                    rotate([-90, 0, 0])
                    cylinder(d = GP_FINGER_W, h = GP_FINGER_T);
            }
            translate([-GP_FINGER_H + GP_FINGER_W / 2, -1, 0])
                rotate([-90, 0, 0])
                cylinder(d = GP_HOLE_D, h = GP_FINGER_T + 2);
        }
    }
}

// -----------------------------------------------------------------------------
// 4. COMPONENT MODULES (exact physical pieces)
// -----------------------------------------------------------------------------

// --- PIECE 1: Blue 3D-Printed Enclosure Tray ---
module enclosure_tray() {
    tray_h = CASE_H - LID_T;
    color(C_CASE)
    union() {
        difference() {
            // Outer shell
            rounded_box(CASE_L, CASE_W, tray_h, CASE_R);

            // Inner cavity
            translate([WALL_T, WALL_T, BOT_T])
                rounded_box(CASE_L - 2 * WALL_T, CASE_W - 2 * WALL_T, tray_h, 1.5);

            // Open Ports Wall (+X): full cutout between corner bosses for RJ45 + USBx4
            translate([CASE_L - WALL_T - 1, WALL_T + 3.0, BOT_T + 1.5])
                cube([WALL_T + 2, CASE_W - 2 * WALL_T - 6.0, tray_h]);

            // Power Wall (-Y) port cutout: micro-USB power, HDMI, 3.5mm audio
            translate([PI_X0 + 4.0, -1, PI_Z0 - 0.5])
                cube([56.0, WALL_T + 2, 10.5]);

            // MicroSD slot window on SD Wall (-X)
            translate([-1, PI_Y0 + PI_W / 2 - 8.0, BOT_T])
                cube([WALL_T + 2, 16.0, 5.0]);
        }

        // 4 Corner M2.5 screw bosses inside tray
        for (bx = [WALL_T + 1.5, CASE_L - WALL_T - 1.5],
             by = [WALL_T + 1.5, CASE_W - WALL_T - 1.5]) {
            difference() {
                translate([bx, by, BOT_T])
                    cylinder(d = BOSS_D, h = tray_h - BOT_T);
                translate([bx, by, tray_h - 10])
                    cylinder(d = SCREW_D - 0.3, h = 11);
            }
        }

        // Low internal support ribs under Pi
        for (rx = [PI_X0 + 8, PI_X0 + PI_L - 10])
            translate([rx, WALL_T + 1, BOT_T])
                cube([3.0, CASE_W - 2 * WALL_T - 2, PI_Z0 - BOT_T]);

        // Primary GoPro 2-finger mount on SD/mount wall (-X) [Fact S6]
        translate([0, CASE_W / 2, tray_h / 2])
            gopro_two_fingers();

        // Secondary GoPro 2-finger mount on Power wall (-Y) [Fact S6]
        translate([CASE_L * 0.48, 0, tray_h * 0.45])
            rotate([0, 0, 90])
            gopro_two_fingers();
    }
}

// --- PIECE 2: Blue 3D-Printed Removable Lid + 4 Corner Screws ---
module enclosure_lid(with_screws = true) {
    color(C_CASE)
    difference() {
        rounded_box(CASE_L, CASE_W, LID_T, CASE_R);

        // Keyhole aperture over IWR6843AoP chip + DIP switch/reset notch (S1, photos)
        translate([AP_X0, AP_Y0, -1])
            cube([AP_L, AP_W, LID_T + 2]);
        translate([NOTCH_X0, AP_Y0 - NOTCH_W + 0.5, -1])
            cube([NOTCH_L, NOTCH_W + 0.5, LID_T + 2]);

        // 4 corner screw holes
        for (bx = [WALL_T + 1.5, CASE_L - WALL_T - 1.5],
             by = [WALL_T + 1.5, CASE_W - WALL_T - 1.5]) {
            translate([bx, by, -1])
                cylinder(d = SCREW_D + 0.3, h = LID_T + 2);
            translate([bx, by, LID_T - 1.2])
                cylinder(d1 = SCREW_D + 0.3, d2 = 4.6, h = 1.3);
        }
    }

    if (with_screws) {
        color(C_METAL)
        for (bx = [WALL_T + 1.5, CASE_L - WALL_T - 1.5],
             by = [WALL_T + 1.5, CASE_W - WALL_T - 1.5]) {
            translate([bx, by, LID_T - 1.1])
                cylinder(d = 4.2, h = 1.3);
        }
    }
}

// --- PIECE 3: Raspberry Pi 3 Model B ---
module raspberry_pi_3b() {
    // PCB
    color(C_PI_PCB)
    rounded_box(PI_L, PI_W, PI_T, PI_R);

    // Ports along +X edge (Ports Wall): RJ45 at power-wall side, 2x stacked USB-A
    color(C_METAL) {
        // RJ45 Ethernet jack (center Y = 10.25 from Pi power edge)
        translate([PI_L - 18.0, 10.25 - 8.0, PI_T])
            cube([21.2, 16.0, 13.5]);
        // Dual USB-A stack #1 (center Y = 29.0)
        translate([PI_L - 15.0, 29.0 - 6.75, PI_T])
            cube([17.5, 13.5, 15.5]);
        // Dual USB-A stack #2 (center Y = 47.0)
        translate([PI_L - 15.0, 47.0 - 6.75, PI_T])
            cube([17.5, 13.5, 15.5]);
    }
    // Dark port interiors for visual realism
    color(C_IC) {
        translate([PI_L + 2.3, 10.25 - 6.0, PI_T + 2.0]) cube([1.0, 12.0, 9.5]);
        for (uy = [29.0, 47.0], uz = [2.0, 9.0])
            translate([PI_L + 1.6, uy - 5.2, PI_T + uz]) cube([1.0, 10.4, 4.5]);
    }

    // Ports along -Y edge (Power Wall): micro-USB power, HDMI, 3.5mm audio
    color(C_METAL) {
        // Micro-USB power (X = 10.6)
        translate([10.6 - 3.8, -1.2, PI_T]) cube([7.6, 5.5, 3.0]);
        // Full-size HDMI (X = 32.0)
        translate([32.0 - 7.5, -1.5, PI_T]) cube([15.0, 11.5, 6.2]);
    }
    color(C_IC) {
        // 3.5mm Audio/Video jack (X = 53.5)
        translate([53.5 - 3.5, -2.0, PI_T]) cube([7.0, 14.0, 6.0]);
        translate([53.5, -2.5, PI_T + 3.0]) rotate([-90, 0, 0]) cylinder(d = 5.5, h = 2.0);
    }

    // 40-pin 2x20 GPIO Header along +Y edge (GPIO Wall)
    color(C_IC)
        translate([7.0, PI_W - 6.0, PI_T]) cube([51.0, 5.0, 2.5]);
    color(C_GOLD)
        for (i = [0 : 19], j = [0 : 1])
            translate([8.2 + i * 2.54, PI_W - 4.77 + j * 2.54, PI_T + 2.5])
                cylinder(d = 0.7, h = 6.0, $fn = 8);

    // Broadcom SoC + ribbed heatsink (visible in photo 115314449)
    color(C_IC)
        translate([22.0, 21.0, PI_T]) cube([14.0, 14.0, 2.0]);
    color(C_METAL)
        translate([22.0, 21.0, PI_T + 2.0]) cube([14.0, 14.0, 4.5]);

    // MicroSD card slot on underside at SD edge (-X)
    color(C_METAL)
        translate([-1.5, PI_W / 2 - 6.0, -1.8]) cube([14.0, 12.0, 1.8]);
}

// --- PIECE 4: Blue Anteral uRAD IWR-AWR Pi Adapter PCB + Standoffs ---
module urad_pi_adapter() {
    // Blue adapter PCB
    color(C_ADP)
    rounded_box(ADP_L, ADP_W, ADP_T, 2.0);

    // 2x20 female pass-through GPIO header block underneath/along +Y edge
    color(C_IC)
        translate([5.0, ADP_W - 5.5, -8.5]) cube([51.0, 5.0, 8.5]);
    color(C_GOLD)
        for (i = [0 : 19], j = [0 : 1])
            translate([6.2 + i * 2.54, ADP_W - 4.27 + j * 2.54, ADP_T])
                cylinder(d = 0.7, h = 2.0, $fn = 8);

    // Unpopulated HMC5883L 5-pin pad row (visible on SD side in photo 115300618)
    color(C_METAL)
        for (i = [0 : 4])
            translate([4.5, 16.0 + i * 2.54, ADP_T])
                cylinder(d = 1.6, h = 0.2, $fn = 12);

    // Shrouded 2x5 10-pin connector (CONNFLY) mating uRAD underneath
    color(C_IC)
        translate([URAD_X0 - ADP_X0 + URAD_L - 10.0,
                   URAD_Y0 - ADP_Y0 + 8.0, ADP_T])
            cube([8.5, 18.0, STANDOFF_H]);

    // 4x Silver M2.5 hex standoffs supporting the uRAD board
    color(C_METAL)
    for (sx = [3.5, URAD_L - 3.5], sy = [3.5, URAD_W - 3.5]) {
        translate([URAD_X0 - ADP_X0 + sx, URAD_Y0 - ADP_Y0 + sy, ADP_T])
            cylinder(d = 4.6, h = STANDOFF_H, $fn = 6);
    }
}

// --- PIECE 5: Green uRAD Industrial v2.0 IWR6843AoP Radar Board ---
module urad_radar_board() {
    // 42x42 mm green PCB [KNOWN]
    color(C_URAD)
    difference() {
        rounded_box(URAD_L, URAD_W, URAD_T, 1.5);
        for (sx = [3.5, URAD_L - 3.5], sy = [3.5, URAD_W - 3.5])
            translate([sx, sy, -1]) cylinder(d = 2.6, h = URAD_T + 2);
    }

    // 4x Silver M2.5 socket-head mounting screws
    color(C_METAL)
    for (sx = [3.5, URAD_L - 3.5], sy = [3.5, URAD_W - 3.5])
        translate([sx, sy, URAD_T]) cylinder(d = 4.4, h = 2.0);

    // TI IWR6843AoP Antenna-on-Package chip (boresight exits +Z top face) [S1, S7]
    chip_lx = CHIP_CX - URAD_X0;
    chip_ly = CHIP_CY - URAD_Y0;
    color(C_CHIP)
        translate([chip_lx - CHIP_S / 2, chip_ly - CHIP_S / 2, URAD_T])
            cube([CHIP_S, CHIP_S, CHIP_H]);
    // Subtle lighter antenna patch frame on top of AoP package
    color([0.34, 0.36, 0.38])
        translate([chip_lx - 5.5, chip_ly - 5.5, URAD_T + CHIP_H])
            cube([11.0, 11.0, 0.15]);

    // Micro-USB port at +X (ports) edge, +Y (GPIO-wall) corner [Fact S4]
    color(C_METAL)
        translate([URAD_L - 6.5, URAD_W - 12.5, URAD_T])
            cube([7.5, 8.0, 2.8]);

    // SOP2 Flashing DIP switch (black) + tactile Reset button (white) near -Y edge
    color(C_IC)
        translate([12.0, 2.5, URAD_T]) cube([7.0, 4.0, 2.0]);
    color(C_WHITE)
        translate([7.5, 4.0, URAD_T]) cube([3.2, 3.2, 1.8]);

    // CP2105 USB-to-UART bridge & power regulator ICs
    color(C_IC) {
        translate([28.0, 22.0, URAD_T]) cube([4.5, 4.5, 0.9]);
        translate([11.0, 31.0, URAD_T]) cube([5.0, 5.0, 1.2]);
        translate([3.5, 9.5, URAD_T])   cube([4.8, 4.8, 1.4]);
    }
}

// --- PIECE 6: MPU-9150 IMU Breakout (GY-521 class, glued under lid) [S2, S8] ---
module mpu9150_breakout() {
    // PCB sits with top face against lid inner face; chip faces -Z (into case)
    color(C_IMU)
        cube([IMU_L, IMU_W, IMU_T]);
    // MPU-9150 QFN die facing -Z (into case) [Fact S2]
    color(C_IC)
        translate([IMU_L / 2 - 2.0, IMU_W / 2 - 2.0, -0.9])
            cube([4.0, 4.0, 0.9]);
    // 8-pin header pad row along +X edge
    color(C_GOLD)
        for (i = [0 : 7])
            translate([IMU_L - 2.0, 1.6 + i * 2.54, -0.3])
                cylinder(d = 1.3, h = IMU_T + 0.6, $fn = 10);
}

// --- 4 Colored IMU Wires (red/yel/grn/blk) from MPU-9150 to Pi GPIO pin-1 end ---
module imu_wires() {
    w_colors = [[0.85, 0.15, 0.15], [0.92, 0.78, 0.10],
                [0.15, 0.68, 0.25], [0.15, 0.15, 0.15]];
    for (i = [0 : 3]) {
        color(w_colors[i])
        hull() {
            translate([IMU_X0 + IMU_L - 2.0, IMU_Y0 + 3.0 + i * 2.54, IMU_Z0 - 1.5])
                sphere(d = 1.4, $fn = 12);
            translate([PI_X0 + 10.0 + i * 2.5, CASE_W - WALL_T - 2.5, ADP_Z0 + 3.5])
                sphere(d = 1.4, $fn = 12);
        }
    }
}

// --- PIECE 7: Black GoPro Quick-Release Buckle + Thumbscrew (on SD wall) ---
module gopro_buckle() {
    tray_h = CASE_H - LID_T;
    color(C_IC)
    translate([-GP_FINGER_H + GP_FINGER_W / 2, CASE_W / 2, tray_h / 2]) {
        // Center mating tongue between the 2 blue fingers
        translate([-10, -1.5, -7.5]) cube([14, 3.0, 15.0]);
        // Outer buckle base plate
        translate([-22, -16, -11]) cube([12, 32, 22]);
        // Thumbscrew shaft + winged knob sticking toward -Y
        translate([0, -24, 0]) rotate([-90, 0, 0]) cylinder(d = 6.0, h = 40.0);
        translate([0, -28, 0]) rotate([-90, 0, 0]) cylinder(d = 13.0, h = 10.0);
        translate([-2, -32, -13]) cube([4, 6, 26]);
    }
}

// --- Optional 3D Radar Coordinate Frame Triad (+X, +Y, +Z) ---
module arrow3d(len, r = 1.0) {
    cylinder(r = r, h = len - 5.0);
    translate([0, 0, len - 5.0]) cylinder(r1 = r * 2.4, r2 = 0, h = 5.0);
}

module radar_axes_triad(origin) {
    translate(origin) {
        // +Y_radar (boresight) = +Z_cad (GREEN)
        color([0.1, 0.78, 0.25]) arrow3d(38, 1.1);
        // +X_radar (lateral, toward GPIO wall) = +Y_cad (RED)
        color([0.88, 0.18, 0.18]) rotate([-90, 0, 0]) arrow3d(32, 1.1);
        // +Z_radar (up when mounted, toward ports wall) = +X_cad (BLUE)
        color([0.15, 0.42, 0.92]) rotate([0, 90, 0]) arrow3d(32, 1.1);
    }
}

module radar_fov_cone(origin) {
    // Translucent +/-60 deg FOV cone along boresight (+Z_cad)
    color([0.95, 0.45, 0.10, 0.16])
    translate(origin)
        cylinder(r1 = 2, r2 = 75, h = 75, $fn = 48);
}

// -----------------------------------------------------------------------------
// 5. ASSEMBLY VIEWS
// -----------------------------------------------------------------------------

module full_assembled(lid_lift = 0, explode = 0) {
    // Layer 0: Enclosure tray (+ optional GoPro buckle)
    enclosure_tray();
    if (show_buckle) gopro_buckle();

    // Layer 1: Raspberry Pi 3B
    translate([PI_X0, PI_Y0, PI_Z0 + explode * 1])
        raspberry_pi_3b();

    // Layer 2: Blue Anteral uRAD Pi Adapter + Standoffs
    translate([ADP_X0, ADP_Y0, ADP_Z0 + explode * 2])
        urad_pi_adapter();

    // Layer 3: Green uRAD Industrial IWR6843AoP Board
    translate([URAD_X0, URAD_Y0, URAD_Z0 + explode * 3])
        urad_radar_board();

    // Layer 4: MPU-9150 Breakout (under lid)
    translate([IMU_X0, IMU_Y0, IMU_Z0 + explode * 4 + lid_lift])
        mpu9150_breakout();

    // Wires only when assembled (unexploded)
    if (explode == 0 && lid_lift == 0)
        imu_wires();

    // Layer 5: Blue 3D-Printed Lid + 4 Corner Screws
    translate([0, 0, CASE_H - LID_T + explode * 5 + lid_lift])
        enclosure_lid(with_screws = true);

    // Exploded view vertical alignment guide rods (4 corners)
    if (explode > 0) {
        color([0.45, 0.45, 0.50, 0.45])
        for (bx = [WALL_T + 1.5, CASE_L - WALL_T - 1.5],
             by = [WALL_T + 1.5, CASE_W - WALL_T - 1.5])
            translate([bx, by, CASE_H - LID_T])
                cylinder(d = 0.6, h = explode * 5, $fn = 8);
    }

    if (show_axes)
        radar_axes_triad([CHIP_CX, CHIP_CY, CASE_H + explode * 5 + lid_lift + 2]);
    if (show_fov)
        radar_fov_cone([CHIP_CX, CHIP_CY, CASE_H + explode * 5 + lid_lift + 1]);
}

module open_lid_view() {
    // Matches macro photos 115251920 & 115300618: lid flipped open behind GPIO wall
    enclosure_tray();
    if (show_buckle) gopro_buckle();
    translate([PI_X0, PI_Y0, PI_Z0]) raspberry_pi_3b();
    translate([ADP_X0, ADP_Y0, ADP_Z0]) urad_pi_adapter();
    translate([URAD_X0, URAD_Y0, URAD_Z0]) urad_radar_board();

    // Flip lid 180 deg about GPIO wall (+Y) so inner face with MPU-9150 faces UP
    lid_gap = 8.0;
    translate([0, CASE_W + lid_gap, 0]) {
        translate([0, CASE_W, LID_T])
        rotate([180, 0, 0]) {
            enclosure_lid(with_screws = false);
            translate([IMU_X0, IMU_Y0, -IMU_T])
                mpu9150_breakout();
        }
    }

    // 4 colored IMU wires routed from opened lid's MPU-9150 to Pi GPIO pin-1 (SD end)
    w_colors = [[0.85, 0.15, 0.15], [0.92, 0.78, 0.10],
                [0.15, 0.68, 0.25], [0.15, 0.15, 0.15]];
    for (i = [0 : 3]) {
        y_imu = CASE_W + lid_gap + (CASE_W - (IMU_Y0 + 3.0 + i * 2.54));
        color(w_colors[i]) {
            hull() {
                translate([IMU_X0 + IMU_L - 1.5, y_imu, LID_T + IMU_T + 0.5])
                    sphere(d = 1.5, $fn = 10);
                translate([CASE_L - 8.0, CASE_W + 4.0 + i * 1.2, CASE_H - 4.0])
                    sphere(d = 1.5, $fn = 10);
            }
            hull() {
                translate([CASE_L - 8.0, CASE_W + 4.0 + i * 1.2, CASE_H - 4.0])
                    sphere(d = 1.5, $fn = 10);
                translate([PI_X0 + 8.0 + i * 2.2, CASE_W - WALL_T - 2.2, ADP_Z0 + 3.0])
                    sphere(d = 1.5, $fn = 10);
            }
        }
    }
}

module mounted_pose() {
    // Corner install (Facts M1-M4):
    //   1. rotate([0,0,180]) maps +X_cad(ports)->-X, +Y_cad(GPIO)->-Y, +Z_cad(lid)->+Z
    //   2. rotate([0, 90+15, 0]) pitches so:
    //      - Ports end (+X_cad) points UP (+Z_world), leaning 15 deg forward
    //      - SD/mount end (-X_cad) is at the BOTTOM on the GoPro grip (M1)
    //      - Lid / boresight (+Z_cad) faces the room (+X_world), tilted 15 deg DOWN (M2, M3)
    //      - GPIO wall (+Y_cad = +X_radar) faces -Y_world (observer-behind's RIGHT)
    rotate([0, 90 + 15, 0])
        rotate([0, 0, 180])
            translate([-CASE_L / 2, -CASE_W / 2, -CASE_H / 2])
                full_assembled(lid_lift = 0, explode = 0);

    // Vertical mounting pole behind the unit (outside +/-60 deg FOV, Fact M4)
    color([0.12, 0.36, 0.68])
        translate([-78, 0, -115]) cylinder(d = 22, h = 230);
    // Pole clamp + articulated magic arm connecting pole to bottom GoPro buckle
    color(C_IC) {
        translate([-78, 0, -25]) cylinder(d = 30, h = 18);
        translate([-78, 0, -16]) rotate([0, 90, 0]) cylinder(d = 10, h = 34);
        translate([-44, 0, -16]) sphere(d = 14);
        hull() {
            translate([-44, 0, -16]) sphere(d = 9);
            translate([-44, 0, -76]) sphere(d = 9);
        }
        translate([-44, 0, -76]) sphere(d = 14);
        hull() {
            translate([-44, 0, -76]) sphere(d = 9);
            translate([-16, 0, -72]) sphere(d = 9);
        }
        translate([-16, 0, -72]) cylinder(d = 10, h = 20);
    }
}

// -----------------------------------------------------------------------------
// 6. DISPATCH
// -----------------------------------------------------------------------------
if (view_mode == "assembled") {
    full_assembled(lid_lift = 0, explode = 0);
} else if (view_mode == "exploded") {
    full_assembled(lid_lift = 0, explode = explode_gap);
} else if (view_mode == "open_lid") {
    open_lid_view();
} else if (view_mode == "mounted") {
    mounted_pose();
} else if (view_mode == "cutaway") {
    difference() {
        full_assembled(lid_lift = 0, explode = 0);
        translate([-40, -40, -10]) cube([200, 40 + WALL_T + 3.0, 80]);
    }
} else if (view_mode == "stl_assembly") {
    full_assembled(lid_lift = 0, explode = 0);
} else if (view_mode == "stl_enclosure") {
    enclosure_tray();
    translate([0, CASE_W + 10, 0]) enclosure_lid(with_screws = false);
}
