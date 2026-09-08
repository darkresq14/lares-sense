#!/usr/bin/env python3
"""
Unit tests for tlv_parser.parse_frame.

These run with **no hardware attached**. Instead of a UART/UDP capture they
build byte-exact TI mmWave SDK frames in memory (magic word + 40-byte header +
TLV items) per docs/hardware/sensor-reference.md "UART Data Protocol", feed them
to parse_frame, and assert the decode. This covers both the Out-of-Box Demo
TLVs (types 1, 2, 7) and the 3D People Tracking TLVs (1020, 1010, 1011, 1012).

Run from the radar_pi/ directory (tests live in tests/, scripts stay flat):

    python3 -m unittest tests.test_tlv_parser -v
"""

import math
import struct
import unittest

from tlv_parser import (
    HEADER_LEN,
    HEADER_STRUCT,
    MAGIC_WORD,
    MAX_DETECTED_OBJ,
    TLV_DETECTED_POINTS,
    TLV_DETECTED_POINTS_SIDE_INFO,
    TLV_RANGE_PROFILE,
    TLV_TRACK_TARGET_HEIGHT,
    TLV_TRACK_TARGET_INDEX,
    TLV_TRACK_TARGET_LIST,
    TLV_TRACK_POINT_CLOUD,
    parse_frame,
)

# mmWave SDK 3.6 version field (Major=3, Minor=6). Value is round-tripped, not
# interpreted, so any constant works; this one is realistic.
_VERSION = 0x03060000
_PLATFORM = 0x6843A000


def _tlv(tlv_type, payload):
    """Pack one TLV: 4-byte type + 4-byte length + payload."""
    return struct.pack('<2I', tlv_type, len(payload)) + payload


def build_frame(tlvs, num_detected_obj, frame_num=42, num_tlvs=None):
    """Build a byte-exact TI mmWave SDK frame.

    ``tlvs`` is an ordered list of (type_id, payload_bytes); pass () for a frame
    with no TLV items. ``num_detected_obj`` is written into the header (it is the
    decoder's expected point count, independent of how many TLVs are present).
    """
    body = b''.join(_tlv(t, p) for t, p in tlvs)
    total_packet_len = HEADER_LEN + len(body)
    n_tlvs = len(tlvs) if num_tlvs is None else num_tlvs
    magic_int = int.from_bytes(MAGIC_WORD, 'little')
    header = struct.pack(
        HEADER_STRUCT,
        magic_int,            # magic word (8 bytes, unpacked as Q)
        _VERSION,             # version
        total_packet_len,     # total packet length
        _PLATFORM,            # platform
        frame_num,            # frame number
        0,                    # time CPU cycles
        num_detected_obj,     # num detected objects
        n_tlvs,               # num TLVs
        0,                    # subframe number
    )
    return header + body


def points_payload(points):
    """Pack a TLV 1 payload from an iterable of (x, y, z, doppler)."""
    return b''.join(struct.pack('<4f', x, y, z, d) for x, y, z, d in points)


def side_payload(side_info):
    """Pack a TLV 7 payload from an iterable of (snr_raw, noise_raw) uint16."""
    return b''.join(struct.pack('<2H', s, n) for s, n in side_info)


def range_payload(bins):
    """Pack a TLV 2 payload from an iterable of uint32 range-bin values."""
    return b''.join(struct.pack('<I', v) for v in bins)


# --- 3D People Tracking payload builders (TLV 1010/1011/1012/1020) -----------

# TLV 1020 (compressed spherical point cloud) decompression units:
# (elevationUnit, azimuthUnit, dopplerUnit, rangeUnit, snrUnit) - 5 floats.
_PC_UNIT_STRUCT = '<5f'
# per point: elevation(i8), azimuth(i8), doppler(i16), range(u16), snr(u16)
_PC_POINT_STRUCT = '<2bh2H'


def compressed_pc_payload(punit, points):
    """Pack a TLV 1020 payload.

    ``punit`` is the 5-float decompression-unit tuple. ``points`` is an iterable
    of (elevation int8, azimuth int8, doppler int16, range uint16, snr uint16)
    raw integer tuples as the firmware emits them.
    """
    payload = struct.pack(_PC_UNIT_STRUCT, *punit)
    for el, az, dop, rng, snr in points:
        payload += struct.pack(_PC_POINT_STRUCT, el, az, dop, rng, snr)
    return payload


def expected_cartesian(rng_m, az_rad, el_rad):
    """Mirror TI's spherical->cartesian conversion for test expectations."""
    return (rng_m * math.sin(az_rad) * math.cos(el_rad),
            rng_m * math.cos(az_rad) * math.cos(el_rad),
            rng_m * math.sin(el_rad))


def target_list_payload(tracks):
    """Pack a TLV 1010 payload.

    ``tracks`` is an iterable of (tid, floats) where ``floats`` is the 27
    floats after the tid: posX..accZ (9) + error-covariance [16] + g + confidence.
    """
    payload = b''
    for tid, floats in tracks:
        payload += struct.pack('<I27f', tid, *floats)
    return payload


def target_height_payload(heights):
    """Pack a TLV 1012 payload from an iterable of (tid, maxZ, minZ)."""
    return b''.join(struct.pack('<I2f', tid, mx, mn) for tid, mx, mn in heights)


def target_index_payload(indexes):
    """Pack a TLV 1011 payload: one uint8 per index."""
    return struct.pack('<%dB' % len(indexes), *indexes)


class ParseFrameTests(unittest.TestCase):

    def assertListAlmostEqual(self, a, b, places=5):
        """Compare two float lists element-wise (float32 round-trips aren't exact)."""
        self.assertEqual(len(a), len(b))
        for x, y in zip(a, b):
            self.assertAlmostEqual(x, y, places=places)

    # -- OOB regression baseline (TLV 1, 2, 7) -------------------------------

    def test_parses_detected_points_tlv1(self):
        pts = [(1.5, -2.25, 0.125, 0.5), (-3.0, 4.0, -5.0, -1.0), (0.0, 0.0, 0.0, 0.0)]
        frame = build_frame([(TLV_DETECTED_POINTS, points_payload(pts))],
                            num_detected_obj=len(pts), frame_num=7)

        parsed = parse_frame(frame)

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed['frame_num'], 7)
        self.assertEqual(parsed['num_points'], 3)
        self.assertEqual(len(parsed['points']), 3)
        for got, want in zip(parsed['points'], pts):
            for g, w in zip(got, want):
                self.assertAlmostEqual(g, w, places=5)

    def test_parses_side_info_tlv7_in_dB(self):
        # raw values are in 0.1 dB units -> 123 raw = 12.3 dB
        side = [(123, 45), (200, 10)]
        pts = [(1.0, 2.0, 3.0, 0.0), (4.0, 5.0, 6.0, 0.0)]
        frame = build_frame(
            [(TLV_DETECTED_POINTS, points_payload(pts)),
             (TLV_DETECTED_POINTS_SIDE_INFO, side_payload(side))],
            num_detected_obj=2,
        )

        parsed = parse_frame(frame)

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed['snr'], [12.3, 20.0])
        self.assertEqual(parsed['noise'], [4.5, 1.0])

    def test_parses_range_profile_tlv2(self):
        bins = [10, 20, 30, 0xFFFFFFFF]
        frame = build_frame([(TLV_RANGE_PROFILE, range_payload(bins))],
                            num_detected_obj=0)

        parsed = parse_frame(frame)

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed['range_profile'], bins)
        self.assertEqual(parsed['points'], [])

    def test_full_oob_frame_points_side_and_range(self):
        pts = [(1.0, 2.0, 3.0, -0.5), (2.0, -1.0, 0.5, 0.25)]
        side = [(150, 80), (90, 60)]
        bins = [1, 2, 3]
        frame = build_frame(
            [(TLV_DETECTED_POINTS, points_payload(pts)),
             (TLV_DETECTED_POINTS_SIDE_INFO, side_payload(side)),
             (TLV_RANGE_PROFILE, range_payload(bins))],
            num_detected_obj=2,
            frame_num=100,
        )

        parsed = parse_frame(frame)

        self.assertEqual(parsed['frame_num'], 100)
        self.assertEqual(parsed['num_points'], 2)
        self.assertEqual(len(parsed['points']), 2)
        self.assertEqual(parsed['snr'], [15.0, 9.0])
        self.assertEqual(parsed['noise'], [8.0, 6.0])
        self.assertEqual(parsed['range_profile'], bins)

    def test_zero_detected_objects_frame(self):
        frame = build_frame([], num_detected_obj=0, frame_num=1)

        parsed = parse_frame(frame)

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed['num_points'], 0)
        self.assertEqual(parsed['points'], [])
        self.assertEqual(parsed['snr'], [])
        self.assertEqual(parsed['range_profile'], [])

    # -- robustness / error paths --------------------------------------------

    def test_short_buffer_returns_none(self):
        self.assertIsNone(parse_frame(b''))
        self.assertIsNone(parse_frame(MAGIC_WORD + b'\x00' * 10))  # < 40 bytes

    def test_too_many_detected_objects_returns_none(self):
        frame = build_frame([], num_detected_obj=MAX_DETECTED_OBJ + 1)
        self.assertIsNone(parse_frame(frame))

    def test_none_input_returns_none(self):
        self.assertIsNone(parse_frame(None))

    def test_truncated_tlv_payload_is_decoded_partially(self):
        # Header claims 2 points but the TLV 1 payload only holds 1 -> decoder
        # decodes the one full point and stops (mirrors the original reader).
        pts = [(1.0, 2.0, 3.0, 0.0)]
        frame = build_frame([(TLV_DETECTED_POINTS, points_payload(pts))],
                            num_detected_obj=2)
        parsed = parse_frame(frame)
        self.assertEqual(len(parsed['points']), 1)

    def test_magic_word_constant_matches_spec(self):
        self.assertEqual(MAGIC_WORD, b'\x02\x01\x04\x03\x06\x05\x08\x07')
        self.assertEqual(HEADER_LEN, 40)

    # -- 3D People Tracking constants (used by the decoders below) ----------

    def test_tracking_tlv_constants_defined(self):
        self.assertEqual(TLV_TRACK_TARGET_LIST, 1010)
        self.assertEqual(TLV_TRACK_TARGET_INDEX, 1011)
        self.assertEqual(TLV_TRACK_TARGET_HEIGHT, 1012)
        self.assertEqual(TLV_TRACK_POINT_CLOUD, 1020)

    def test_unknown_tlv_is_skipped_without_breaking_oob(self):
        # A frame mixing an unrecognised TLV with a real OOB points TLV: the
        # parser must skip the unknown payload and still decode TLV 1.
        pts = [(1.0, 2.0, 3.0, 0.0), (-1.0, -2.0, -3.0, 0.5)]
        fake_unknown_payload = b'\x01\x02\x03\x04\x05\x06\x07\x08'
        frame = build_frame(
            [(9999, fake_unknown_payload),
             (TLV_DETECTED_POINTS, points_payload(pts))],
            num_detected_obj=2,
        )

        parsed = parse_frame(frame)

        self.assertIsNotNone(parsed)
        self.assertEqual(len(parsed['points']), 2)
        self.assertAlmostEqual(parsed['points'][0][0], 1.0)
        self.assertAlmostEqual(parsed['points'][1][1], -2.0)

    # -- 3D People Tracking decoders (TLV 1010/1011/1012/1020) ----------------

    def test_parses_compressed_point_cloud_tlv1020(self):
        # pUnit: (elevUnit, azUnit, dopUnit, rangeUnit, snrUnit)
        punit = (0.01, 0.01, 0.001, 0.002, 0.04)
        # raw (elevation, azimuth, doppler, range, snr) ints as firmware emits
        pts = [
            (0, 0, 0, 500, 100),    # rng=1.0 m, az=0,   el=0    -> straight ahead
            (0, 50, 10, 1000, 200), # rng=2.0 m, az=0.5, el=0
            (30, 0, -5, 750, 50),   # rng=1.5 m, az=0,   el=0.3
        ]
        frame = build_frame([(TLV_TRACK_POINT_CLOUD, compressed_pc_payload(punit, pts))],
                            num_detected_obj=3, frame_num=9)

        parsed = parse_frame(frame)

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed['frame_num'], 9)
        self.assertEqual(len(parsed['points']), 3)
        # point 0: rng=1.0, az=0, el=0 -> (0, 1, 0), doppler 0, snr 4.0
        x, y, z, d = parsed['points'][0]
        self.assertAlmostEqual(x, 0.0, places=6)
        self.assertAlmostEqual(y, 1.0, places=6)
        self.assertAlmostEqual(z, 0.0, places=6)
        self.assertAlmostEqual(d, 0.0, places=6)
        self.assertAlmostEqual(parsed['snr'][0], 100 * 0.04, places=5)
        # point 1: rng=2.0, az=0.5 rad, el=0 -> cartesian via the TI formula
        ex, ey, ez = expected_cartesian(2.0, 0.5, 0.0)
        x, y, z, d = parsed['points'][1]
        self.assertAlmostEqual(x, ex, places=5)
        self.assertAlmostEqual(y, ey, places=5)
        self.assertAlmostEqual(z, ez, places=5)
        self.assertAlmostEqual(d, 10 * 0.001, places=6)
        self.assertAlmostEqual(parsed['snr'][1], 200 * 0.04, places=5)
        # point 2: rng=1.5, az=0, el=0.3 rad
        ex, ey, ez = expected_cartesian(1.5, 0.0, 0.3)
        x, y, z, d = parsed['points'][2]
        self.assertAlmostEqual(x, ex, places=5)
        self.assertAlmostEqual(y, ey, places=5)
        self.assertAlmostEqual(z, ez, places=5)
        self.assertAlmostEqual(d, -5 * 0.001, places=6)

    def test_short_compressed_point_cloud_payload_returns_empty(self):
        # Payload smaller than the 20-byte pUnit header -> no points, no crash.
        frame = build_frame([(TLV_TRACK_POINT_CLOUD, b'\x00' * 8)], num_detected_obj=3)
        parsed = parse_frame(frame)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed['points'], [])
        self.assertEqual(parsed['snr'], [])

    def test_parses_target_list_tlv1010(self):
        ec = [float(i) for i in range(16)]
        floats = [1.5, -2.5, 0.5,        # position X,Y,Z
                  0.1, -0.2, 0.0,        # velocity X,Y,Z
                  0.01, 0.02, 0.03] + ec + [9.9, 0.87]  # acc + ec[16] + g + conf
        frame = build_frame([(TLV_TRACK_TARGET_LIST, target_list_payload([(7, floats)]))],
                            num_detected_obj=0)

        parsed = parse_frame(frame)

        self.assertEqual(len(parsed['tracks']), 1)
        t = parsed['tracks'][0]
        self.assertEqual(t['tid'], 7)
        self.assertListAlmostEqual(t['position'], [1.5, -2.5, 0.5])
        self.assertListAlmostEqual(t['velocity'], [0.1, -0.2, 0.0])
        self.assertListAlmostEqual(t['acceleration'], [0.01, 0.02, 0.03])
        self.assertListAlmostEqual(t['error_covariance'], ec)
        self.assertAlmostEqual(t['g'], 9.9, places=5)
        self.assertAlmostEqual(t['confidence'], 0.87, places=5)

    def test_parses_target_height_tlv1012(self):
        heights = [(0, 1.75, 0.10), (3, 1.80, 0.05)]
        frame = build_frame([(TLV_TRACK_TARGET_HEIGHT, target_height_payload(heights))],
                            num_detected_obj=0)

        parsed = parse_frame(frame)

        self.assertEqual(len(parsed['track_heights']), 2)
        self.assertEqual(parsed['track_heights'][0]['tid'], 0)
        self.assertAlmostEqual(parsed['track_heights'][0]['max_z'], 1.75, places=5)
        self.assertAlmostEqual(parsed['track_heights'][0]['min_z'], 0.10, places=5)
        self.assertEqual(parsed['track_heights'][1]['tid'], 3)
        self.assertAlmostEqual(parsed['track_heights'][1]['max_z'], 1.80, places=5)
        self.assertAlmostEqual(parsed['track_heights'][1]['min_z'], 0.05, places=5)

    def test_parses_target_index_tlv1011(self):
        indexes = [0, 1, 1, 253, 254, 255, 0]  # includes the special not-associated codes
        punit = (0.01, 0.01, 0.001, 0.002, 0.04)
        raw_pts = [(0, 0, 0, 500, 100)] * 7
        frame = build_frame(
            [(TLV_TRACK_TARGET_INDEX, target_index_payload(indexes)),
             (TLV_TRACK_POINT_CLOUD, compressed_pc_payload(punit, raw_pts))],
            num_detected_obj=7,
        )

        parsed = parse_frame(frame)

        self.assertEqual(parsed['track_indexes'], indexes)
        self.assertEqual(len(parsed['points']), 7)

    def test_full_3d_people_tracking_frame(self):
        # One frame carrying point cloud (1020) + tracks (1010) + height (1012)
        # + index (1011): the whole extended TLV set, decoded together.
        punit = (0.01, 0.01, 0.001, 0.002, 0.04)
        pc = [(0, 0, 0, 500, 100), (0, 40, 5, 800, 150)]
        ec = [0.0] * 16
        track_floats = ([1.0, 2.0, 0.9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
                        + ec + [9.81, 0.95])
        frame = build_frame(
            [(TLV_TRACK_POINT_CLOUD, compressed_pc_payload(punit, pc)),
             (TLV_TRACK_TARGET_LIST, target_list_payload([(1, track_floats)])),
             (TLV_TRACK_TARGET_HEIGHT, target_height_payload([(1, 1.78, 0.12)])),
             (TLV_TRACK_TARGET_INDEX, target_index_payload([0, 1]))],
            num_detected_obj=2,
            frame_num=256,
        )

        parsed = parse_frame(frame)

        self.assertEqual(parsed['frame_num'], 256)
        self.assertEqual(parsed['num_points'], 2)
        self.assertEqual(len(parsed['points']), 2)
        self.assertEqual(len(parsed['snr']), 2)
        self.assertEqual(len(parsed['tracks']), 1)
        self.assertEqual(parsed['tracks'][0]['tid'], 1)
        self.assertListAlmostEqual(parsed['tracks'][0]['position'], [1.0, 2.0, 0.9])
        self.assertEqual(parsed['track_heights'][0]['tid'], 1)
        self.assertAlmostEqual(parsed['track_heights'][0]['max_z'], 1.78, places=5)
        self.assertAlmostEqual(parsed['track_heights'][0]['min_z'], 0.12, places=5)
        self.assertEqual(parsed['track_indexes'], [0, 1])

    def test_oob_and_tracking_keys_default_empty(self):
        # A bare OOB frame must still expose the new tracking keys as empty lists.
        frame = build_frame([], num_detected_obj=0)
        parsed = parse_frame(frame)
        self.assertEqual(parsed['tracks'], [])
        self.assertEqual(parsed['track_heights'], [])
        self.assertEqual(parsed['track_indexes'], [])


if __name__ == '__main__':
    unittest.main()
