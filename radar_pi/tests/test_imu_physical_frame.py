#!/usr/bin/env python3
"""
Physical-frame regression for the IMU math (wall-capture invariance).

The anchor is the probe-verified chip geometry, not the code's assumption:

- The MPU9150 lies flat on the case lid (its PCB plane parallel to the
  lid), mounted upside-down: chip +z points INTO the case, so the radar
  boresight — out through the lid — is along chip **-z**, never along an
  in-plane axis. This is what the urad-pi desk probe measured (flat on
  desk, lid up, raw accel (0, 0, -1 g) — already recorded as the desk
  vectors in test_setup_tool_imu.py).
- ``pitch_roll`` / ``tilt_comp_heading`` assume the boresight is the
  body's **+y** (x right, y forward, z up when aimed).

``apply_axis_map`` is the seam that must bridge the two. If it only flips
signs (as it does today: x→−x, y→y, z→−z), the boresight stays on the
body's z while the math aims with +y — and every wall capture reads the
wrong, spin-dependent direction.

The invariant under test is CONTEXT.md's Wall Capture contract: with the
case bottom flush against a wall, the reported heading is that wall's
inward-normal azimuth, and it is invariant under rotation of the unit in
the wall plane ("flushness is the only contact requirement; rotation of
the unit is free"). The mounted pose must also read its true pitch
(nose-down positive, the ``elev_tilt`` sense).

Truth side: world frame (N, E, Up); a dipping field (horizontal part
along magnetic north defines heading 0). Chip axes are placed at a given
boresight + in-plane spin, gravity and the field are projected onto them,
then the production path runs: apply_axis_map → pitch_roll →
tilt_comp_heading. Spin is swept over the full circle — the sweep stands
in for the unknown in-plane mounting rotation of the chip on the lid, so
any mounting angle that satisfies the desk anchor is covered.

Run from the radar_pi/ directory:

    python3 -m unittest tests.test_imu_physical_frame -v
"""

import math
import unittest

from setup_tool.imu import apply_axis_map, pitch_roll, tilt_comp_heading, wrap_180

#: Tolerance for the exact synthetic math (deg) — matches DEG_TOL.
TOL = 0.2

_UP = (0.0, 0.0, 1.0)
_EAST = (0.0, 1.0, 0.0)


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _norm(a):
    n = math.sqrt(_dot(a, a))
    return (a[0] / n, a[1] / n, a[2] / n)


def _chip_axes(boresight, spin_deg):
    """Chip (x, y, z) axes in world coords at a pose.

    ``boresight``: world direction the lid faces (unit). The chip sits
    flat on the lid upside-down, so chip z = -boresight; the in-plane
    axes are spun by ``spin_deg`` about the boresight (grip spin + the
    unknown chip mounting rotation — same rotation group).
    """
    b = _norm(boresight)
    t = _cross(_UP, b)                # in-plane, horizontal-ish
    if _dot(t, t) < 1e-9:             # boresight vertical (desk pose)
        t = _EAST
    t = _norm(t)
    v = _cross(b, t)                  # in-plane, completes the triad
    s = math.radians(spin_deg)
    x_c = tuple(math.cos(s) * ti + math.sin(s) * vi for ti, vi in zip(t, v))
    z_c = tuple(-bi for bi in b)
    y_c = _cross(z_c, x_c)            # right-handed (x, y, z)
    return x_c, y_c, z_c


def _pose_samples(boresight, spin_deg, dip_deg=65.0):
    """Raw chip-frame accel (g) + mag (arb) for a posed unit."""
    axes = _chip_axes(boresight, spin_deg)
    accel = tuple(_dot(_UP, a) for a in axes)          # +1g on the up axis
    field = (math.cos(math.radians(dip_deg)), 0.0,
             -math.sin(math.radians(dip_deg)))          # dip down, N-ward
    mag = tuple(_dot(field, a) for a in axes)
    return accel, mag


def _measure(boresight, spin_deg, dip_deg=65.0):
    """Run the production math path on a physically posed sample."""
    accel, mag = _pose_samples(boresight, spin_deg, dip_deg)
    ax, ay, az = apply_axis_map(*accel)
    mx, my, mz = apply_axis_map(*mag)
    pitch, roll = pitch_roll(ax, ay, az)
    heading = tilt_comp_heading(pitch, roll, mx, my, mz)
    return pitch, roll, heading


def _bore(azimuth_deg, elevation_deg=0.0):
    """Boresight unit vector at a compass azimuth / elevation."""
    az, el = math.radians(azimuth_deg), math.radians(elevation_deg)
    ce = math.cos(el)
    return (math.cos(az) * ce, math.sin(az) * ce, math.sin(el))


class WallCaptureInvariance(unittest.TestCase):
    """Bottom flush on a wall: heading is the wall's normal, spin-free."""

    def test_wall_heading_is_normal_and_spin_invariant(self):
        for theta in (0.0, 90.0, 214.0, 359.0):
            for spin in range(0, 360, 15):
                for dip in (0.0, 65.0, 80.0):
                    with self.subTest(theta=theta, spin=spin, dip=dip):
                        bore = _bore(theta)          # wall inward normal
                        pitch, roll, heading = _measure(bore, spin, dip)
                        err = wrap_180(heading - theta)
                        self.assertAlmostEqual(
                            err, 0.0, delta=TOL,
                            msg=f'wall {theta:.0f}° spin {spin}° dip '
                                f'{dip:.0f}°: heading {heading:.1f}° '
                                f'(err {err:.1f}°), pitch {pitch:.1f}°, '
                                f'roll {roll:.1f}°')

    def test_wall_capture_reads_level_pitch(self):
        # The design's wall-capture pose is pitch ≈ 0, roll = the free
        # spin (~±90° in a natural grip); a non-zero pitch at the wall
        # means the boresight is on the wrong body axis.
        for spin in range(0, 360, 15):
            with self.subTest(spin=spin):
                pitch, _, _ = _measure(_bore(214.0), spin)
                self.assertAlmostEqual(pitch, 0.0, delta=TOL,
                                       msg=f'spin {spin}°: pitch {pitch:.1f}°')

    def test_mounted_down_tilt_reads_elev(self):
        # Aimed along the bisector with the grip's ~15° downward tilt:
        # pitch must read +15 (nose-down positive, the elev_tilt sense).
        for theta in (0.0, 120.0, 355.0):
            for spin in (0, 90, 210):
                with self.subTest(theta=theta, spin=spin):
                    pitch, _, _ = _measure(_bore(theta, -15.0), spin)
                    self.assertAlmostEqual(pitch, 15.0, delta=TOL,
                                           msg=f'az {theta:.0f}° spin '
                                               f'{spin}°: pitch {pitch:.1f}°')

    def test_desk_pose_boresight_up_reads_nose_up_90(self):
        # The probe anchor, reinterpreted in the tool frame: flat on the
        # desk (lid up) the boresight points at the ceiling — 90° nose-up.
        # Roll is undefined at the gimbal; only pitch is asserted.
        accel = (0.0, 0.0, -1.0)                   # recorded desk vector
        ax, ay, az = apply_axis_map(*accel)
        pitch, _ = pitch_roll(ax, ay, az)
        self.assertAlmostEqual(abs(pitch), 90.0, delta=TOL,
                               msg=f'desk pose: pitch {pitch:.1f}°')


if __name__ == '__main__':
    unittest.main()
