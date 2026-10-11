#!/usr/bin/env python3
"""
Live-replay regression: real MPU9150 samples recorded on urad-pi
(2026-10-11) pin the compass axis map.

Why this file exists: the synthetic-frame tests build a model chip and
compose it with the production map — any (model, map) pair that composes
to the same tool frame passes, so two wrong maps shipped green. Real
hardware is the only oracle that can go red on the map itself.

The recordings (probe logged with the shared axis map; fixtures below
are converted to chip registers, every Nth clean sample):

- s5a/s5b/s5c: three holds against wall A — flush ports-up, hinged
  ~18° down, flush spun ~90° — same physical aim (the wall's inward
  normal), recorded minutes before s6 with a different grip.
- s6: one slow full-turn sweep, case bottom flush on wall A.
- s7: the same sweep on wall B (the corner's other wall), re-gripped.

What the data established (docs/hardware/sensor-reference.md): the
AK8975 die is flipped 180° about x relative to the accel die, so
apply_mag_axis_map is the pure swap (x, z, y). A missed flip MIRRORS
every azimuth (h -> 180 − h) — live, aim guidance ended 180° out with
left/right inverted, and wall A sat near the mirror's fixed point
(~270°), so its captures had looked deceptively fine.

What each assertion pins:
- same-wall agreement across poses, grips and sessions — a wrong axis
  PERMUTATION breaks this by up to 177° (measured with the pre-fix
  shared map);
- wall B's absolute azimuth band — a wrong y-SIGN reads it mirrored
  ~86° away (measured 313° vs the fitted 227°).

Tolerances are wide on purpose: the installer's watch deflects single
poses by 10–20°; the group means are the stable quantity (2.0° measured
across sessions).

Run from the radar_pi/ directory:

    python3 -m unittest tests.test_imu_live_replay -v
"""

import math
import unittest

from setup_tool.imu import (apply_mag_axis_map, circular_mean,
                            leveled_field, tilt_comp_heading, wrap_180)

#: (pitch, roll, mx, my, mz) — chip registers, µT.
S5A_WALL_A_FLUSH_PORTS_UP = [
    (-1.5, -90.2, -15.0, 48.0, -9.6),
    (-1.3, -90.2, -14.7, 48.9, -8.1),
    (-1.5, -90.3, -16.2, 46.8, -7.8),
    (-1.6, -90.0, -15.6, 47.4, -7.8),
    (-1.5, -90.1, -15.3, 48.3, -8.7),
    (-1.4, -90.0, -16.2, 48.0, -9.6),
    (-1.5, -90.0, -14.1, 45.9, -9.3),
    (4.4, -89.6, -16.8, 46.8, -4.2),
]
S5B_WALL_A_HINGED_DOWN = [
    (10.1, -90.3, -18.0, 43.8, 1.8),
    (18.2, -90.7, -18.9, 42.9, 5.1),
    (16.5, -90.7, -18.3, 43.5, 2.1),
    (18.3, -91.3, -18.3, 43.5, 5.1),
    (18.1, -91.4, -19.2, 41.4, 4.8),
    (18.8, -91.6, -20.1, 41.7, 4.5),
    (18.1, -91.4, -19.2, 42.0, 6.0),
    (18.7, -91.2, -20.1, 41.1, 6.9),
]
S5C_WALL_A_FLUSH_SPUN = [
    (-0.8, -160.5, -38.4, 9.6, -1.2),
    (1.7, -179.7, -35.7, 1.5, -1.5),
    (0.6, -179.3, -36.0, 1.2, -1.2),
    (0.3, -179.7, -36.0, 1.8, -1.2),
    (0.1, -179.4, -37.5, 1.5, -0.3),
    (0.6, -179.5, -34.8, 1.2, -1.2),
    (0.0, -179.7, -36.0, 1.2, 0.0),
    (0.0, -179.5, -36.0, 1.8, -1.2),
]
S6_WALL_A_FULL_SWEEP = [
    (-7.0, -88.3, -27.0, 38.4, 11.4),
    (-0.5, -89.5, -25.8, 40.8, 10.8),
    (2.6, -53.5, 0.3, 43.5, 5.7),
    (2.0, 22.1, 34.8, 9.0, -15.0),
    (5.4, 99.8, 0.9, -21.3, -4.5),
    (0.2, 126.4, -17.4, -18.6, -1.2),
    (1.0, -146.2, -45.0, 17.4, 1.2),
    (-1.0, -98.3, -26.1, 42.3, -0.3),
]
S7_WALL_B_FULL_SWEEP = [
    (-1.1, -89.4, -10.5, 39.9, -32.1),
    (-1.3, -89.2, -9.3, 39.9, -30.9),
    (-1.1, -74.9, -1.5, 41.7, -32.1),
    (-10.7, -17.5, 18.9, 32.1, -33.9),
    (1.9, 35.7, 24.0, -3.6, -31.8),
    (7.1, 87.2, -4.8, -20.4, -30.0),
    (-6.2, -174.9, -39.3, 9.9, -27.9),
    (10.0, -116.4, -16.5, 39.3, -30.9),
]

#: Wall B's fitted azimuth under the die-flip map (live fit, 2026-10-11).
#: A mirrored map (missing flip) reads this wall ~86° away (313°).
WALL_B_AZIMUTH = 227.0

#: Wall A's sweep azimuth, fitted the same way — the reference all
#: same-wall groups must return to.
WALL_A_AZIMUTH = 270.0


def _headings(samples):
    """Per-sample tilt-compensated headings through the production map."""
    out = []
    for pitch, roll, mx, my, mz in samples:
        tx, ty, tz = apply_mag_axis_map(mx, my, mz)
        out.append(tilt_comp_heading(pitch, roll, tx, ty, tz))
    return out


def _swept_capture_heading(samples):
    """The capture estimator: vector mean of the leveled field."""
    hxs, hys = [], []
    for pitch, roll, mx, my, mz in samples:
        tx, ty, tz = apply_mag_axis_map(mx, my, mz)
        hx, hy = leveled_field(pitch, roll, tx, ty, tz)
        hxs.append(hx)
        hys.append(hy)
    return math.degrees(math.atan2(-sum(hxs) / len(hxs),
                                   sum(hys) / len(hys))) % 360.0


class LiveReplay(unittest.TestCase):
    """Real-hardware pins for the compass axis map."""

    def test_wall_a_readings_agree_across_poses_and_sessions(self):
        # Same wall, three poses + a full sweep, two grips, ~30 min apart:
        # every group must read the wall's inward-normal azimuth. A wrong
        # axis permutation scatters these by up to 177° (measured).
        sweep = circular_mean(_headings(S6_WALL_A_FULL_SWEEP))
        for name, group in (('flush ports-up', S5A_WALL_A_FLUSH_PORTS_UP),
                            ('hinged down', S5B_WALL_A_HINGED_DOWN),
                            ('flush spun 90', S5C_WALL_A_FLUSH_SPUN)):
            with self.subTest(group=name):
                mean = circular_mean(_headings(group))
                err = wrap_180(mean - sweep)
                self.assertLess(abs(err), 20.0,
                                f'{name}: {mean:.1f}° vs sweep {sweep:.1f}° '
                                f'(err {err:.1f}°)')
        combined = circular_mean(_headings(S5A_WALL_A_FLUSH_PORTS_UP
                                           + S5B_WALL_A_HINGED_DOWN
                                           + S5C_WALL_A_FLUSH_SPUN))
        err = wrap_180(combined - sweep)
        self.assertLess(abs(err), 8.0,
                        f'all wall-A holds {combined:.1f}° vs sweep '
                        f'{sweep:.1f}° (err {err:.1f}°) — measured 2.0°')

    def test_sweep_capture_estimator_matches_live_means(self):
        # The wizard's capture path (vector mean of the leveled field over
        # the sweep) must land where the per-sample headings' circular mean
        # does — and on wall A's azimuth.
        sweep = _swept_capture_heading(S6_WALL_A_FULL_SWEEP)
        live = circular_mean(_headings(S6_WALL_A_FULL_SWEEP))
        self.assertLess(abs(wrap_180(sweep - live)), 5.0,
                        f'swept capture {sweep:.1f}° vs live mean '
                        f'{live:.1f}°')

    def test_wall_b_azimuth_anti_mirror_pin(self):
        # The y-sign oracle. Wall B sits ~43° from wall A; under the
        # correct (die-flip) map its sweep reads ~227°. A mirrored map —
        # the missing die flip, either the old y-flip or the original
        # shared map — reads ~313° (measured), 86° out. Wall A cannot
        # police this: it sits near the mirror's fixed point (~270°),
        # which is exactly how the bug shipped green twice.
        mean = circular_mean(_headings(S7_WALL_B_FULL_SWEEP))
        err = wrap_180(mean - WALL_B_AZIMUTH)
        self.assertLess(abs(err), 8.0,
                        f'wall B reads {mean:.1f}° (err {err:.1f}° from '
                        f'{WALL_B_AZIMUTH}°) — a mirrored map reads ~313°')

    def test_wall_a_azimuth_band(self):
        # Coarse absolute pin for wall A (the fitted 270° band); mainly a
        # tripwire for future map edits that shift everything.
        mean = circular_mean(_headings(S6_WALL_A_FULL_SWEEP))
        self.assertLess(abs(wrap_180(mean - WALL_A_AZIMUTH)), 10.0,
                        f'wall A sweep reads {mean:.1f}°')


if __name__ == '__main__':
    unittest.main()
