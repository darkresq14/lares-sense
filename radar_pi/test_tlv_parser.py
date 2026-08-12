#!/usr/bin/env python3
"""
Unit tests for tlv_parser.parse_frame.

These run with **no hardware attached**. Instead of a UART/UDP capture they
build byte-exact TI mmWave SDK frames in memory (magic word + 40-byte header +
TLV items) per docs/hardware/sensor-reference.md "UART Data Protocol", feed them
to parse_frame, and assert the decode. This is the regression baseline for the
Out-of-Box Demo TLVs (types 1, 2, 7).

Run from the radar_pi/ directory (matches the scripts' flat-import convention):

    python3 -m unittest test_tlv_parser -v
"""

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


class ParseFrameTests(unittest.TestCase):

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

    # -- 3D People Tracking placeholders (decoders land in #3) ----------------

    def test_tracking_tlv_constants_defined(self):
        self.assertEqual(TLV_TRACK_TARGET_LIST, 1010)
        self.assertEqual(TLV_TRACK_TARGET_INDEX, 1011)
        self.assertEqual(TLV_TRACK_TARGET_HEIGHT, 1012)
        self.assertEqual(TLV_TRACK_POINT_CLOUD, 1020)

    def test_tracking_tlvs_are_skipped_without_breaking_oob(self):
        # A frame mixing an (undecoded) tracking TLV with a real OOB points TLV:
        # the parser must skip the unknown payload and still decode TLV 1.
        pts = [(1.0, 2.0, 3.0, 0.0), (-1.0, -2.0, -3.0, 0.5)]
        fake_tracking_payload = b'\x01\x02\x03\x04\x05\x06\x07\x08'
        frame = build_frame(
            [(TLV_TRACK_POINT_CLOUD, fake_tracking_payload),
             (TLV_DETECTED_POINTS, points_payload(pts))],
            num_detected_obj=2,
        )

        parsed = parse_frame(frame)

        self.assertIsNotNone(parsed)
        self.assertEqual(len(parsed['points']), 2)
        self.assertAlmostEqual(parsed['points'][0][0], 1.0)
        self.assertAlmostEqual(parsed['points'][1][1], -2.0)


if __name__ == '__main__':
    unittest.main()
