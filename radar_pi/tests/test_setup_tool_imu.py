#!/usr/bin/env python3
"""
Seam tests for the IMU module, Aim Assist wizard and measured tilt
(issue #15).

The unit's lid carries an MPU9150 (accel + AK8975 behind bypass) mounted
flat on the lid, upside-down (chip +z into the case — the boresight out
the lid is chip −z, which apply_axis_map rotates onto the tool's +y);
the Setup Tool host samples it ~10 Hz in Config Stage only
(silent in Running Stage), publishes measured tilt + tilt-compensated
heading as ``imu`` events, and runs the Aim Assist wizard: two ~2 s wall
captures -> the corner bisector (wrap-around-safe, any corner angle) ->
a live ±2.5° aimed verdict riding the ``imu`` events.

Per ADR-0004's seam discipline everything is driven through public
boundaries: a fake I2C bus (the smbus2 slice the driver uses) feeds
synthetic accel/mag vectors through the real :class:`Mpu9150` driver into
the real host, and real loopback WS clients assert the events. The pure
math (tilt, tilt-compensation, bisector) is additionally exercised
directly with known vectors — including the probe-recorded desk vector
(flat desk, lid up: raw accel (0, 0, −1 g), |g| = 1.000 — the vector
that pins the axis map's z sign; see test_imu_physical_frame for the
physical-mounting invariants that keep the map honest).

Run from the radar_pi/ directory:

    python3 -m unittest tests.test_setup_tool_imu -v
"""

import json
import math
import tempfile
import threading
import time
import unittest
import urllib.request

from websockets.sync.client import connect

from setup_tool.host import SetupToolHost
from setup_tool.imu import (
    AK8975_ADDR,
    MPU_ADDR,
    Mpu9150,
    aim_error,
    apply_axis_map,
    bisector,
    pitch_roll,
    tilt_comp_heading,
    wrap_180,
)
from tests.test_setup_tool_host import FakeSerialDevice
from tests.test_setup_tool_stages import FrameFeeder

#: Host IMU tuning for tests: fast sampling + a short window so a pose
#: change settles in ~0.3 s (the real default is 10 Hz / 2 s), and a
#: short capture so sweep tests stay quick (the real default is 10 s).
TEST_IMU_INTERVAL = 0.02
TEST_IMU_WINDOW = 0.3
TEST_IMU_CAPTURE = 1.0

#: Tolerance for angle assertions through the whole pipe (deg).
DEG_TOL = 0.2


# ---- synthetic pose construction ------------------------------------------
#
# Independent construction of the test truth: build the body orientation
# from world-frame rotations, project gravity and a dipping magnetic
# field into body (radar-frame) coordinates, then invert the lid
# upside-down axis map into raw chip axes for the fake bus.

def _matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)]
            for i in range(3)]


def _transpose(a):
    return [list(r) for r in zip(*a)]


def _matvec(a, v):
    return [sum(a[i][k] * v[k] for k in range(3)) for i in range(3)]


def _rot_heading(deg):
    """Body (right, forward, up) axes in the world (east, north, up)
    frame at the given compass heading (0 = north, clockwise)."""
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [[c, s, 0], [-s, c, 0], [0, 0, 1]]


def _rot_tilt(pitch_deg, roll_deg):
    """Body axes in the heading frame — the exact rotations
    :func:`pitch_roll` / :func:`tilt_comp_heading` invert."""
    p, r = math.radians(pitch_deg), math.radians(roll_deg)
    cp, sp, cr, sr = math.cos(p), math.sin(p), math.cos(r), math.sin(r)
    return [
        [cr, 0, -sr],
        [sp * sr, cp, sp * cr],
        [cp * sr, -sp, cp * cr],
    ]


def radar_frame_pose(heading, pitch, roll, dip=65.0):
    """Radar-frame accel (g) + mag (arb units) for a posed body.

    ``dip`` is the local field's downward inclination — the tilt
    compensation must survive it (real fields dip steeply).
    """
    world_from_body = _matmul(_rot_heading(heading), _rot_tilt(pitch, roll))
    body_from_world = _transpose(world_from_body)
    accel = _matvec(body_from_world, [0.0, 0.0, 1.0])
    mag = _matvec(body_from_world,
                  [0.0, math.cos(math.radians(dip)),
                   -math.sin(math.radians(dip))])
    return accel, mag


# ---- fake I2C bus (the smbus2 slice the driver uses) -----------------------

class FakeI2CBus:
    """Scripted MPU9150 + AK8975 on one bus.

    Accelerometer and magnetometer hold whatever raw chip-frame vectors
    the test last set (see :meth:`set_pose`); the identity registers
    answer the probed values so the driver path is exercised end to end.
    """

    def __init__(self):
        self._accel_raw = [0, 0, -16384]  # int16 LSB, chip frame (flat desk)
        self._mag_raw = [0, 1000, 0]      # int16 LSB, chip frame (north-ish)
        self.mag_bias = (0.0, 0.0, 0.0)   # body-frame hard-iron (arb units)
        self.writes = []                 # (addr, reg, value)
        self.closed = False

    def set_pose(self, accel, mag):
        """Set radar-frame accel (g) + mag (arb); converts through the
        inverse of the axis maps into raw LSB pairs. The AK8975's y
        register is inverted relative to the accel die in-package
        (probe-verified live, 2026-10-11) — the driver's
        apply_mag_axis_map exists to undo exactly this."""
        ax, ay, az = apply_axis_map(*accel)      # radar -> chip (self-inverse)
        mx, my, mz = apply_axis_map(*mag)
        my = -my                                 # in-package y inversion
        mx += self.mag_bias[0]
        my += self.mag_bias[1]
        mz += self.mag_bias[2]
        self._accel_raw = [round(a * 16384) for a in (ax, ay, az)]
        self._mag_raw = [round(m * 1000) for m in (mx, my, mz)]

    # smbus2 API ----------------------------------------------------------

    def read_byte_data(self, addr, reg):
        if addr == MPU_ADDR and reg == 0x75:       # WHO_AM_I
            return 0x68
        if addr == AK8975_ADDR and reg == 0x00:    # AK8975 WIA
            return 0x48
        if addr == AK8975_ADDR and reg == 0x02:    # ST1: always ready
            return 0x01
        if addr == AK8975_ADDR and reg == 0x09:    # ST2
            return 0x00
        raise OSError(f'unhandled byte read addr={addr:#x} reg={reg:#x}')

    def read_i2c_block_data(self, addr, reg, length):
        if addr == MPU_ADDR and reg == 0x3B:
            out = []
            for v in self._accel_raw:           # big-endian pairs (H, L)
                out += [v >> 8, v & 0xFF]
            return out
        if addr == AK8975_ADDR and reg == 0x03:
            out = []
            for v in self._mag_raw:             # little-endian pairs (L, H)
                out += [v & 0xFF, v >> 8]
            return out
        raise OSError(f'unhandled block read addr={addr:#x} reg={reg:#x}')

    def write_byte_data(self, addr, reg, value):
        self.writes.append((addr, reg, value))

    def close(self):
        self.closed = True


# ---- pure math -------------------------------------------------------------

class ImuMathTests(unittest.TestCase):

    def test_desk_pose_vectors_read_nose_up_90(self):
        """The probe-recorded desk vector: unit flat on the desk (lid up),
        raw chip accel ≈ (0, 0, −1 g) — chip z into the case. This is the
        vector that pins the axis map's z sign. Under the tool frame the
        pose is 90° nose-up (the boresight points at the ceiling; roll is
        undefined at that gimbal, so only pitch is asserted). The old
        assertion here — tilt ≈ 0/0, "verified live" — encoded the frame
        bug: it made apply_axis_map self-confirming instead of testing
        the physical mounting (which test_imu_physical_frame now does)."""
        ax, ay, az = apply_axis_map(0.0, 0.0, -1.0)
        pitch, roll = pitch_roll(ax, ay, az)
        self.assertAlmostEqual(pitch, -90.0, places=6)
        self.assertAlmostEqual(math.hypot(ax, ay, az), 1.0, places=6)

    def test_pitch_roll_recover_known_tilts(self):
        for pose_pitch, pose_roll in [(15, 0), (0, -8), (20, 10),
                                      (-30, 25), (15, 90)]:
            accel, _ = radar_frame_pose(0, pose_pitch, pose_roll)
            pitch, roll = pitch_roll(*accel)
            self.assertAlmostEqual(pitch, pose_pitch, places=6,
                                   msg=f'pitch at {pose_pitch}/{pose_roll}')
            self.assertAlmostEqual(roll, pose_roll, places=6,
                                   msg=f'roll at {pose_pitch}/{pose_roll}')

    def test_heading_stays_correct_at_tilt(self):
        """Tilt-compensated heading at tilts ≥ 15°, including the wall
        capture pose (roll ±90° — where naive atan2 fails)."""
        for heading, pitch, roll in [(0, 15, 0), (100, 20, 10),
                                     (214, 0, -90), (359, 25, 85),
                                     (180, 60, -60)]:
            accel, mag = radar_frame_pose(heading, pitch, roll)
            pitch, roll = pitch_roll(*accel)
            got = tilt_comp_heading(pitch, roll, *mag)
            self.assertLess(abs(wrap_180(got - heading)), 1e-6,
                            f'heading at {heading}/{pitch}/{roll}')

    def test_naive_heading_is_wrong_at_capture_pose(self):
        # Documents the old bench script's blind spot: at the wall capture
        # pose (roll ≈ −90°) naive atan2(hx, hy) is off by ~100°.
        _, mag = radar_frame_pose(214, 0, -90)
        naive = math.degrees(math.atan2(mag[0], mag[1])) % 360
        self.assertGreater(abs(wrap_180(naive - 214)), 50)

    def test_bisector_handles_wraparound_and_any_corner(self):
        self.assertAlmostEqual(bisector(350, 80), 35.0)    # across north
        self.assertAlmostEqual(bisector(10, 100), 55.0)    # plain 90°
        self.assertAlmostEqual(bisector(300, 0), 330.0)    # 60° corner
        self.assertAlmostEqual(bisector(45, 180), 112.5)   # 135° corner
        self.assertIsNone(bisector(45, 225))               # opposite walls

    def test_aim_error_sign_and_band(self):
        self.assertAlmostEqual(aim_error(37, 35), 2.0)
        self.assertAlmostEqual(aim_error(33.9, 35), -1.1)
        # +error = heading clockwise past the target = turn LEFT.
        self.assertGreater(aim_error(40, 35), 0)
        self.assertAlmostEqual(wrap_180(-190), 170)


# ---- host seam --------------------------------------------------------------

class ImuHostTests(unittest.TestCase):
    """The wizard and the event stream, driven through the host seam."""

    def make_host(self, with_imu=True):
        self.data_dir = tempfile.mkdtemp(prefix='setup-tool-imu-')
        self.device = FakeSerialDevice()
        self.device.timeout = 0.05
        self.bus = FakeI2CBus() if with_imu else None
        self.host = SetupToolHost(
            self.device, bind='127.0.0.1', port=0, config_dir=self.data_dir,
            apply_frames_timeout=0.8, apply_command_delay=0.0,
            imu=Mpu9150(self.bus) if with_imu else None,
            imu_sample_interval=TEST_IMU_INTERVAL,
            imu_window_s=TEST_IMU_WINDOW,
            aim_capture_s=TEST_IMU_CAPTURE)
        self.host.start()
        self.addCleanup(self.host.stop)
        self.port = self.host.actual_port

    def ws_connect(self):
        return connect(f'ws://127.0.0.1:{self.port}/', open_timeout=5)

    def recv_of_type(self, ws, type_, timeout=5):
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            self.assertGreater(remaining, 0, f'{type_} never came')
            message = json.loads(ws.recv(timeout=remaining))
            if message.get('type') == type_:
                return message

    def set_pose(self, heading, pitch=0.0, roll=0.0):
        accel, mag = radar_frame_pose(heading, pitch, roll)
        self.bus.set_pose(accel, mag)

    def wait_heading(self, ws, heading, timeout=5):
        """Wait for the windowed heading to settle on the current pose."""
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            message = json.loads(ws.recv(timeout=max(0.1, deadline
                                                     - time.monotonic())))
            if message.get('type') != 'imu':
                continue
            last = message
            if abs(wrap_180(message['heading'] - heading)) < DEG_TOL:
                return message
        self.fail(f'heading never settled near {heading} (last {last})')

    # ---- stream basics ----------------------------------------------------

    def test_probe_recorded_desk_tilt_through_host(self):
        """The recorded probe vectors: flat on the real desk the unit
        read pitch +0.5°, roll −2.3° — a desk tilted −2.3° in roll. The
        same pose must reproduce through the whole pipe (fake bus →
        driver → axis map → math → ``imu`` event)."""
        self.make_host()
        self.set_pose(0.0, pitch=0.5, roll=-2.3)
        with self.ws_connect() as ws:
            self.recv_of_type(ws, 'stage')
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                message = json.loads(ws.recv(timeout=max(
                    0.1, deadline - time.monotonic())))
                if message.get('type') != 'imu':
                    continue
                if abs(message['pitch'] - 0.5) < DEG_TOL \
                        and abs(message['roll'] + 2.3) < DEG_TOL:
                    return
            self.fail('probe desk pose (+0.5°/−2.3°) never reproduced')

    def test_imu_events_carry_tilt_and_heading(self):
        self.make_host()
        self.set_pose(214.0, pitch=15.0, roll=-5.0)
        with self.ws_connect() as ws:
            self.recv_of_type(ws, 'stage')            # snapshot
            message = self.wait_heading(ws, 214.0)
            self.assertAlmostEqual(message['pitch'], 15.0, delta=DEG_TOL)
            self.assertAlmostEqual(message['roll'], -5.0, delta=DEG_TOL)

    def test_no_imu_host_fully_functional_and_silent(self):
        self.make_host(with_imu=False)
        with self.ws_connect() as ws:
            self.recv_of_type(ws, 'stage')
            ws.send(json.dumps({'type': 'config_get'}))
            config = self.recv_of_type(ws, 'config')
            self.assertIn('sensorPosition', config['values'])
            ws.send(json.dumps({'type': 'config_set',
                                'values': config['values']}))
            self.assertEqual(self.recv_of_type(ws, 'config_saved')['type'],
                             'config_saved')
            # Aim requests answer an explicit error, not silence.
            ws.send(json.dumps({'type': 'aim_capture', 'wall': 'a'}))
            aim = self.recv_of_type(ws, 'aim')
            self.assertIn('no IMU', aim.get('error', ''))
            # And no imu events ever arrive.
            try:
                message = json.loads(ws.recv(timeout=1.0))
            except TimeoutError:
                message = None
            self.assertTrue(message is None
                            or message.get('type') != 'imu')

    def test_running_stage_is_imu_silent(self):
        self.make_host()
        self.set_pose(0.0)
        feeder = FrameFeeder(self.device, first_num=7000).start()
        self.addCleanup(feeder.stop)
        with self.ws_connect() as ws:
            self.recv_of_type(ws, 'stage')
            self.set_pose(0.0)
            self.wait_heading(ws, 0.0)

            # A Start landing mid-capture: the capture window must
            # not leak any aim/imu event into Running Stage.
            ws.send(json.dumps({'type': 'aim_capture', 'wall': 'a'}))
            time.sleep(0.3)              # capture is now mid-await
            ws.send(json.dumps({'type': 'start'}))
            # The first stage event whose stage is 'running' — stale
            # {config, …} transitions queued before the flip must be
            # skipped, so everything received after this point is
            # post-flip by queue order.
            deadline = time.monotonic() + 10
            while True:
                self.assertGreater(deadline - time.monotonic(), 0,
                                   'running never arrived')
                message = json.loads(
                    ws.recv(timeout=deadline - time.monotonic()))
                if message.get('type') == 'stage' \
                        and message.get('stage') == 'running':
                    break
            # Everything that arrives from here on is frames/stage only —
            # the capture window outlives the flip, so this drain
            # covers the interrupted capture's would-be broadcasts.
            deadline = time.monotonic() + 2.5
            while time.monotonic() < deadline:
                remaining = max(0.05, deadline - time.monotonic())
                message = json.loads(ws.recv(timeout=remaining))
                self.assertNotIn(message.get('type'), ('imu', 'aim'),
                                 'IMU leaked into Running Stage')
            # The wizard is gone too: an aim_capture gets the locked
            # snapshot, not a capture.
            ws.send(json.dumps({'type': 'aim_capture', 'wall': 'a'}))
            locked = self.recv_of_type(ws, 'stage')
            self.assertEqual(locked['stage'], 'running')
            self.assertTrue(locked.get('locked'))

    # ---- the wizard --------------------------------------------------------

    def capture_wall(self, ws, wall, heading):
        self.set_pose(heading)
        t0 = time.monotonic()
        ws.send(json.dumps({'type': 'aim_capture', 'wall': wall}))
        aim = self.recv_of_type(ws, 'aim', timeout=10)
        while aim['state'] == 'capturing':
            aim = self.recv_of_type(ws, 'aim', timeout=10)
        return aim, time.monotonic() - t0

    def test_two_wall_captures_produce_bisector_any_corner(self):
        self.make_host()
        with self.ws_connect() as ws:
            self.recv_of_type(ws, 'stage')
            self.set_pose(350.0)
            self.wait_heading(ws, 350.0)

            # A 90° corner across north: 350° and 80° bisect to 35°.
            aim_a, dt_a = self.capture_wall(ws, 'a', 350.0)
            self.assertAlmostEqual(aim_a['wall_a']['heading'], 350.0,
                                   delta=DEG_TOL)
            self.assertEqual(aim_a['state'], 'half')
            self.assertGreater(dt_a, 0.5)     # ~1 s test capture window
            self.assertLess(dt_a, 4.0)

            aim_b, _ = self.capture_wall(ws, 'b', 80.0)
            self.assertAlmostEqual(aim_b['wall_b']['heading'], 80.0,
                                   delta=DEG_TOL)
            self.assertEqual(aim_b['state'], 'aiming')
            self.assertAlmostEqual(aim_b['bisector'], 35.0, delta=DEG_TOL)

    def test_aimed_band_and_error_sign_live(self):
        self.make_host()
        with self.ws_connect() as ws:
            self.recv_of_type(ws, 'stage')
            self.set_pose(350.0)
            self.wait_heading(ws, 350.0)
            self.capture_wall(ws, 'a', 350.0)
            self.capture_wall(ws, 'b', 80.0)          # bisector 35°

            def wait_verdict(want_aimed, heading):
                self.set_pose(heading)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    message = json.loads(ws.recv(timeout=max(
                        0.1, deadline - time.monotonic())))
                    if message.get('type') != 'imu':
                        continue
                    if 'aim_aimed' not in message:
                        continue
                    if message['aim_aimed'] == want_aimed and \
                            abs(wrap_180(message['heading']
                                         - heading)) < DEG_TOL:
                        return message
                self.fail(f'aimed={want_aimed} at {heading} never seen')

            inside = wait_verdict(True, 36.9)         # +1.9° — inside band
            self.assertAlmostEqual(inside['aim_err'], 1.9, delta=DEG_TOL)
            wait_verdict(True, 33.1)                  # −1.9° — inside band
            over = wait_verdict(False, 40.0)          # +5° — outside
            self.assertGreater(over['aim_err'], 2.5)  # positive = turn LEFT
            wait_verdict(False, 28.0)                 # −7° — outside

    def test_opposite_walls_have_no_bisector(self):
        self.make_host()
        with self.ws_connect() as ws:
            self.recv_of_type(ws, 'stage')
            self.set_pose(45.0)
            self.wait_heading(ws, 45.0)
            self.capture_wall(ws, 'a', 45.0)
            aim_b, _ = self.capture_wall(ws, 'b', 225.0)
            self.assertIsNone(aim_b['bisector'])
            self.assertIn('bisector', aim_b.get('error', ''))

    def test_reset_clears_and_mid_wizard_connect_sees_state(self):
        self.make_host()
        with self.ws_connect() as ws:
            self.recv_of_type(ws, 'stage')
            self.set_pose(10.0)
            self.wait_heading(ws, 10.0)
            self.capture_wall(ws, 'a', 10.0)

            # A second browser connecting mid-wizard learns where it is.
            with self.ws_connect() as ws2:
                self.recv_of_type(ws2, 'stage')
                aim = self.recv_of_type(ws2, 'aim')
                self.assertEqual(aim['state'], 'half')
                self.assertAlmostEqual(aim['wall_a']['heading'], 10.0,
                                       delta=DEG_TOL)

            ws.send(json.dumps({'type': 'aim_reset'}))
            aim = self.recv_of_type(ws, 'aim')
            self.assertEqual(aim['state'], 'idle')
            self.assertIsNone(aim['wall_a'])

    def test_capture_averages_the_window(self):
        """The capture is an average: pose noise inside the window must
        wash out to the mean, not the instantaneous value."""
        self.make_host()
        with self.ws_connect() as ws:
            self.recv_of_type(ws, 'stage')
            self.set_pose(100.0)
            self.wait_heading(ws, 100.0)
            # Small ±0.5° wobble around the wall heading during capture.
            stop = threading.Event()

            def wobble():
                flip = False
                while not stop.is_set():
                    self.set_pose(100.5 if flip else 99.5)
                    flip = not flip
                    stop.wait(0.04)

            thread = threading.Thread(target=wobble, daemon=True)
            thread.start()
            self.addCleanup(stop.set)
            aim, _ = self.capture_wall(ws, 'a', 100.0)
            stop.set()
            self.assertAlmostEqual(aim['wall_a']['heading'], 100.0,
                                   delta=DEG_TOL)

    def test_sweep_capture_cancels_body_fixed_bias(self):
        """The sweep capture is the bias killer: with a hard-iron bias of
        the probe-measured as-worn size (~a third of the field: unit +
        installer's watch, urad-pi 2026-10-11) a still single-pose
        heading is off by tens of degrees, but a capture taken while
        rolling one full turn lands back on the wall normal — the bias
        term is sinusoidal in the rotation and the circular mean over a
        full turn cancels it."""
        self.make_host()
        self.bus.mag_bias = (0.25, -0.12, 0.0)    # |b|/|B| ≈ 0.3, wall-plane
        # (chip z deliberately zero: a boresight bias survives any
        # wall-plane sweep by geometry — see
        # test_imu_physical_frame.test_boresight_bias_survives_sweeps)
        with self.ws_connect() as ws:
            self.recv_of_type(ws, 'stage')
            stop = threading.Event()

            def sweep():
                roll = 0
                while not stop.is_set():
                    self.set_pose(355.0, 0.0, roll)
                    roll = (roll + 12) % 360
                    stop.wait(0.01)

            thread = threading.Thread(target=sweep, daemon=True)
            thread.start()
            self.addCleanup(stop.set)
            aim, _ = self.capture_wall(ws, 'a', 355.0)
            stop.set()
            self.assertAlmostEqual(aim['wall_a']['heading'], 355.0,
                                   delta=3.0)

    # ---- page carries the UI (markers, per ADR-0004's manual-verify rule) --

    def test_page_carries_aim_and_badge_ui(self):
        self.make_host()
        with urllib.request.urlopen(
                f'http://127.0.0.1:{self.port}/', timeout=5) as resp:
            page = resp.read().decode('utf-8')
        with urllib.request.urlopen(
                f'http://127.0.0.1:{self.port}/app.js', timeout=5) as resp:
            app = resp.read().decode('utf-8')

        for marker in ('id="aim-panel"', 'id="aim-cap-a"', 'id="aim-cap-b"',
                       'id="aim-live"', 'id="aim-toggle"'):
            self.assertIn(marker, page)
        # The badge is JS-mounted (the editor re-renders); the adopt
        # action and the wizard protocol must exist in the client.
        for marker in ("'imu'", "'aim'", 'aim_capture', 'aim_reset',
                       'useMeasuredTilt', 'imu-adopt'):
            self.assertIn(marker, app)


if __name__ == '__main__':
    unittest.main()
