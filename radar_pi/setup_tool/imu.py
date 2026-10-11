#!/usr/bin/env python3
"""
MPU9150 IMU module for the Setup Tool (issue #15).

The unit's case lid carries an MPU9150 (accelerometer + AK8975 compass)
mounted **flat on the lid, upside-down**: its PCB is parallel to the lid
and chip +z points into the case, so the radar boresight — out through
the lid, perpendicular to the case's large faces — is along chip −z. The
math below speaks the tool frame (x right, y the boresight, z up when
aimed), so :func:`apply_axis_map` rotates chip→tool: x→x, y→−z, z→−y.
The desk probe on urad-pi pins the z sign: unit flat on the desk, lid
up, reads raw accel (0, 0, −1 g), |g| = 1.000 — and under the fixed map
that pose correctly reads pitch −90 (the beam points at the ceiling);
the earlier "reads level flat on desk" observation was the frame bug's
signature, not a verification. Pressing the flat bottom flush against a
wall makes the boresight that wall's inward normal — no aiming skill at
the wall, only flushness.

Three layers, no cross-dependencies:

- :class:`Mpu9150` — smbus2 driver (accel + AK8975 behind bypass mode),
  probed lazily, silently absent when the chip is not there.
- pure math — :func:`pitch_roll`, :func:`tilt_comp_heading`,
  :func:`bisector`, :func:`wrap_180`, :func:`aim_error`. Tilt from
  gravity, **tilt-compensated** heading (naive ``atan2(hy, hx)`` is wrong
  at mounted tilt — during a wall capture the unit rolls ~90°), and the
  corner **bisector** from two wall captures (a wrap-around-safe heading
  average, valid for any corner angle, not just 90°).
- :class:`RollingImu` — the ~2 s averaging window captures and the live
  aim indicator share (compass noise σ ≈ 2.5° instantaneous; 2 s of
  averaging brings it to ≈ 0.5° in a constant local field).

No compass calibration wizard, ever — but not because errors cancel on
their own (the original claim; falsified live 2026-10-11): the unit and
the installer's watch carry a body-FIXED hard-iron bias (~10 µT
as-worn), which bends each heading differently per grip and does NOT
cancel between captures. What makes the no-wizard policy survive is the
sweep capture: each wall is captured while the installer slowly rotates
the unit one full turn in the wall plane, and the vector average of the
leveled field (:func:`leveled_field`) zeroes the bias's wall-plane
components exactly. The component along the boresight is invisible to
wall-plane rotation by geometry and survives as the method's ~±10°
accuracy floor as worn (captures with the non-watch hand shrink it).
Do not add a calibration wizard; keep the sweeps.
"""

import math
import threading
import time
from collections import deque

#: I2C addresses (bus 1 on the Pi; MPU9150 answers, AK8975 behind bypass).
MPU_ADDR = 0x68
AK8975_ADDR = 0x0C

#: Probe identities: WHO_AM_I of the MPU9150 and WIA of the AK8975.
MPU_WHO_AM_I = 0x68
AK8975_WIA = 0x48

# MPU9150 register map (the MPU6050 slice + the bypass bit).
REG_PWR_MGMT_1 = 0x6B
REG_ACCEL_XOUT_H = 0x3B
REG_INT_PIN_CFG = 0x37
BYPASS_ENABLE = 0x02

# AK8975 register map.
REG_WIA = 0x00
REG_ST1 = 0x02
REG_HXL = 0x03
REG_ST2 = 0x09
REG_CNTL = 0x0A
CNTL_SINGLE_MEASUREMENT = 0x01
ST1_DRDY = 0x01

#: Accel scale for the default ±2 g range (LSB per g).
ACCEL_LSB_PER_G = 16384.0

#: AK8975 sensitivity (µT per LSB).
MAG_UT_PER_LSB = 0.3

#: Aim Assist tuning (issue #15; carried here so the math and the policy
#: live next to each other).
IMU_SAMPLE_INTERVAL = 0.1     #: sample cadence, ~10 Hz (Config Stage)
IMU_WINDOW_SECONDS = 2.0      #: live badge/aim window — σ ≈ 2.5° → ≈ 0.5°
AIM_CAPTURE_SECONDS = 10.0    #: one slow full-turn sweep per wall (below)
AIM_BAND_DEG = 2.5            #: "aimed" band — ±60° FOV makes it irrelevant

#: Axis map, lid mount: raw chip axes -> tool frame. The chip lies flat on
#: the lid upside-down (PCB parallel to the lid, +z into the case), so the
#: boresight out the lid is chip −z; the map makes it the tool's +y. One
#: more trap: the tool triple (x right, y forward, z up) is LEFT-handed
#: (right × forward = down), while the chip's axes are right-handed — so
#: the map must flip a sign, not just permute: x→x, y→−z, z→−y. Get this
#: backwards and every heading reads mirrored (azimuth → −azimuth, mod
#: 360) — spin-stable but 2θ off true. The z sign is pinned by the desk
#: probe: flat on the desk, lid up, raw accel (0, 0, −1 g), |g| = 1.000
#: (urad-pi). The map is an involution (its own inverse). The remaining
#: freedom — the chip's in-plane rotation on the lid — cancels in
#: pitch/heading math (it is a roll offset; see test_imu_physical_frame).
def apply_axis_map(x, y, z):
    """Map raw chip axes into the tool frame (boresight = tool +y)."""
    return x, -z, -y


#: The AK8975 compass die sits FLIPPED 180° about x relative to the accel
#: die in the same MPU9150 package. Full-turn wall sweeps on urad-pi
#: (2026-10-11) proved its z register still reads the boresight — through
#: a flush sweep only the shared-map y component (chip z) stays constant
#: while the other two circle; a bias-aware joint fit over five live
#: sessions picks the flipped map at 9.0 µT rms vs 15.4 for the unflipped
#: one; and with it, same-wall readings agree across poses, grips and
#: sessions to 2.0° (fixtures in tests/test_imu_live_replay.py). The undo
#: is the pure swap x->x, y->z, z->y — det −1, the same handedness fix
#: the accel map carries. A missed flip MIRRORS every azimuth
#: (h -> 180 − h): live, aim guidance ended 180° out with left/right
#: inverted, while wall captures near azimuth ~270° — the mirror's fixed
#: point — deceptively looked fine. Residual ~5° in-plane die rotation
#: and soft-iron (~8 µT) sit inside the ±10° as-worn accuracy floor
#: (docs/hardware/sensor-reference.md).
def apply_mag_axis_map(x, y, z):
    """Map raw AK8975 axes into the tool frame (die flipped about x)."""
    return x, z, y


def circular_mean(headings):
    """Wrap-around-safe mean of headings (degrees, 0 ≤ m < 360).

    Sums the unit vectors and takes the resultant's angle — crossing
    north just works. Returns None when the vectors cancel (opposite
    headings: the mean is undefined)."""
    x = sum(math.cos(math.radians(h)) for h in headings)
    y = sum(math.sin(math.radians(h)) for h in headings)
    if math.hypot(x, y) < 1e-9:
        return None
    return math.degrees(math.atan2(y, x)) % 360.0


# ---- pure math ----------------------------------------------------------

def pitch_roll(ax, ay, az):
    """Tilt from gravity, in the radar frame; degrees.

    Frame (shared with overlays.js): x right, y the boresight, z up; a
    level unit reads gravity as (0, 0, +1). **Pitch** is nose-down
    positive (the ``elev_tilt`` sense: a wall mount tilts ~15° down),
    **roll** leans about the boresight. Valid for |pitch| < 90°.
    """
    pitch = math.atan2(-ay, math.hypot(ax, az))
    roll = math.atan2(ax, az)
    return math.degrees(pitch), math.degrees(roll)


def leveled_field(pitch, roll, mx, my, mz):
    """The magnetic field in the leveled frame — (hx, hy), the two
    horizontal components after tilt compensation.

    This is the right thing to average over a sweep: a body-fixed bias
    is ADDITIVE here (and rotates to exactly zero mean over a full-turn
    sweep), while it bends per-sample headings through atan2 — averaging
    headings leaves a second-order residue (measured −9.3° at |bias| ≈
    a third of the field), averaging these does not.
    """
    p = math.radians(pitch)
    r = math.radians(roll)
    # Level the field: undo roll, then pitch (the exact inverse of the
    # rotation that turned (0,0,+1) gravity into the measured accel).
    hx = mx * math.cos(r) - mz * math.sin(r)
    hy = my * math.cos(p) + math.sin(p) * (mx * math.sin(r)
                                           + mz * math.cos(r))
    return hx, hy


def tilt_comp_heading(pitch, roll, mx, my, mz):
    """Tilt-compensated compass heading; degrees, 0 ≤ h < 360.

    Projects the measured magnetic field onto the horizontal plane using
    the accel-derived ``pitch``/``roll`` (:func:`pitch_roll`'s frame and
    signs), then takes the heading of the body's forward (+y) axis:
    0 = magnetic north, 90 = east, clockwise viewed from above. Naive
    ``atan2(hy, hx)`` is wrong here — during a wall capture the unit
    rolls ~90°, and any mounted tilt bends the raw reading.
    """
    hx, hy = leveled_field(pitch, roll, mx, my, mz)
    return math.degrees(math.atan2(-hx, hy)) % 360.0


def wrap_180(deg):
    """Wrap an angle into (−180, 180]."""
    return (deg + 180.0) % 360.0 - 180.0


def bisector(heading_a, heading_b):
    """The bisector of two wall-capture headings; degrees, or None.

    Circular mean of the two unit vectors — wrap-around-safe and valid
    for any corner angle, not just 90°: 350° and 80° bisect to 35°.
    Returns None when the two headings are opposite (resultant ≈ 0):
    the bisector is then undefined, which for walls means the captures
    did not come from a corner.
    """
    return circular_mean((heading_a, heading_b))


def aim_error(heading, target):
    """Signed remaining error from ``heading`` to ``target``; degrees.

    Positive = heading is clockwise past the target (viewed from above),
    so the installer should turn **left** by that much. The aimed band
    (±2.5°, deliberately looser than the old bench script's ±1.5° — the
    ±60° FOV makes a 2.5° error irrelevant) is judged on |error|.
    """
    return wrap_180(heading - target)


# ---- the ~2 s averaging window -------------------------------------------

class RollingImu:
    """Thread-safe ring of (t, pitch, roll, heading, hx, hy) samples.

    The IMU thread appends; the asyncio loop reads the window means
    (captures) and the same means feed the published events. The heading
    mean is circular (wrap-around safe); pitch/roll and the leveled
    field (hx, hy) are arithmetic — their mean feeds ``field_heading``,
    the sweep-capture estimator (see :func:`leveled_field`).
    """

    def __init__(self, window_s):
        self._window_s = window_s
        self._samples = deque()
        self._lock = threading.Lock()

    def add(self, t, pitch, roll, heading, hx, hy):
        with self._lock:
            self._samples.append((t, pitch, roll, heading, hx, hy))
            self._trim(t)

    def clear(self):
        """Drop all samples — a capture starts its window at the gate,
        not with whatever the installer was doing before it."""
        with self._lock:
            self._samples.clear()

    def _trim(self, now):
        horizon = now - self._window_s
        while self._samples and self._samples[0][0] < horizon:
            self._samples.popleft()

    def mean(self):
        """Circular-mean heading + arithmetic pitch/roll/field; None if
        empty."""
        with self._lock:
            if not self._samples:
                return None
            samples = list(self._samples)
        heading = circular_mean([s[3] for s in samples])
        if heading is None:             # all headings cancelled out
            heading = samples[-1][3]
        pitch = sum(s[1] for s in samples) / len(samples)
        roll = sum(s[2] for s in samples) / len(samples)
        hx = sum(s[4] for s in samples) / len(samples)
        hy = sum(s[5] for s in samples) / len(samples)
        if math.hypot(hx, hy) < 1e-9:   # degenerate — fall back
            field_heading = heading
        else:
            field_heading = math.degrees(math.atan2(-hx, hy)) % 360.0
        return {'heading': heading, 'pitch': pitch, 'roll': roll,
                'field_heading': field_heading, 'n': len(samples)}


# ---- smbus2 driver --------------------------------------------------------

class Mpu9150:
    """MPU9150 over an smbus2-compatible bus; radar-frame reads.

    The bus is injected (duck-typed to the smbus2 slice the driver uses:
    ``read_i2c_block_data`` / ``write_byte_data`` / ``read_byte_data``),
    so tests drive the chip through a fake bus — same public path as the
    Pi (ADR-0004's single-seam discipline). :meth:`probe` builds the real
    thing, silently returning None when smbus2, the bus or the chip is
    absent — the Setup Tool stays fully functional without an IMU.
    """

    def __init__(self, bus):
        self._bus = bus
        # Wake (sleep bit clear; internal clock is fine for accel+mag).
        bus.write_byte_data(MPU_ADDR, REG_PWR_MGMT_1, 0x00)
        # Bypass mode: the AK8975 sits behind the MPU's auxiliary bus and
        # only answers on the main bus with bypass enabled.
        bus.write_byte_data(MPU_ADDR, REG_INT_PIN_CFG, BYPASS_ENABLE)

    @classmethod
    def probe(cls, bus_number=1):
        """Try to find the chip; a ready Mpu9150 or None — never raises."""
        try:
            from smbus2 import SMBus
        except ImportError:
            return None
        try:
            bus = SMBus(bus_number)
        except Exception:
            return None
        try:
            if bus.read_byte_data(MPU_ADDR, 0x75) != MPU_WHO_AM_I:
                bus.close()
                return None
            # The compass only answers once the MPU is awake and its aux
            # bus is in bypass. A fresh power-up sits in SLEEP with bypass
            # off — a read at 0x0C then fails (Errno 121) and probe()
            # would miss a perfectly healthy chip. Same writes as
            # __init__ (idempotent); found live on urad-pi after a power
            # cycle: WHO_AM_I answered, WIA did not.
            bus.write_byte_data(MPU_ADDR, REG_PWR_MGMT_1, 0x00)
            bus.write_byte_data(MPU_ADDR, REG_INT_PIN_CFG, BYPASS_ENABLE)
            if bus.read_byte_data(AK8975_ADDR, REG_WIA) != AK8975_WIA:
                bus.close()
                return None
            return cls(bus)
        except Exception:
            try:
                bus.close()
            except Exception:
                pass
            return None

    def read(self):
        """One sample: radar-frame accel (g) + mag (µT) + timestamp.

        Triggers an AK8975 single measurement per read (10 Hz sample rate
        — single-shot is the reliable mode at that pace) and reads the
        accelerometer around it. Raises on bus errors; the host's IMU
        thread degrades silently.
        """
        accel = self._bus.read_i2c_block_data(MPU_ADDR, REG_ACCEL_XOUT_H, 6)
        self._bus.write_byte_data(AK8975_ADDR, REG_CNTL,
                                  CNTL_SINGLE_MEASUREMENT)
        mag = self._read_mag()
        # Register order: accel blocks are big-endian pairs (H then L),
        # the AK8975's are little-endian (L then H) — the classic trap.
        ax = _s16(accel[1], accel[0]) / ACCEL_LSB_PER_G
        ay = _s16(accel[3], accel[2]) / ACCEL_LSB_PER_G
        az = _s16(accel[5], accel[4]) / ACCEL_LSB_PER_G
        mx = mag[0] * MAG_UT_PER_LSB
        my = mag[1] * MAG_UT_PER_LSB
        mz = mag[2] * MAG_UT_PER_LSB
        ax, ay, az = apply_axis_map(ax, ay, az)
        mx, my, mz = apply_mag_axis_map(mx, my, mz)
        return {'t': time.monotonic(),
                'ax': ax, 'ay': ay, 'az': az,
                'mx': mx, 'my': my, 'mz': mz}

    def _read_mag(self):
        """One single-shot AK8975 measurement as (x, y, z) LSB."""
        deadline = time.monotonic() + 0.012
        while True:
            if self._bus.read_byte_data(AK8975_ADDR, REG_ST1) & ST1_DRDY:
                break
            if time.monotonic() >= deadline:
                raise IOError('AK8975 measurement did not complete')
            time.sleep(0.001)
        raw = self._bus.read_i2c_block_data(AK8975_ADDR, REG_HXL, 6)
        self._bus.read_byte_data(AK8975_ADDR, REG_ST2)   # re-arm
        return (_s16(raw[0], raw[1]), _s16(raw[2], raw[3]),
                _s16(raw[4], raw[5]))

    def close(self):
        try:
            self._bus.close()
        except Exception:
            pass


def _s16(lo, hi):
    """Two register bytes -> signed int16."""
    value = (hi << 8) | (lo & 0xFF)
    return value - 0x10000 if value >= 0x8000 else value
