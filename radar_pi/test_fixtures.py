#!/usr/bin/env python3
"""
Shared fixtures for the frame_store tests.

Both the unit tests (``test_frame_store.py``) and the integration tests
(``test_frame_store_integration.py``) need hand-built parsed-frame dicts in the
shape ``tlv_parser.parse_frame`` returns. They live here once so the two files
assert against the same literals (the same precedent as ``test_tlv_parser``'s
``build_frame``, which the integration tests also reuse).
"""

import struct

from tlv_parser import (
    TLV_TRACK_POINT_CLOUD, TLV_TRACK_TARGET_HEIGHT, TLV_TRACK_TARGET_INDEX,
    TLV_TRACK_TARGET_LIST,
)

try:
    from test_tlv_parser import build_frame
except ImportError:  # pragma: no cover - only when run outside radar_pi/
    build_frame = None


def tracking_frame(frame_num=4242, tid=3, n_points=2):
    """Hand-built parsed 3D People Tracking frame dict (points + one track)."""
    points = [(1.0, 2.0, 0.5, 0.2), (3.0, 4.0, 0.9, -0.1)]
    points = points[:n_points] if n_points < len(points) else points
    return {
        'frame_num': frame_num,
        'num_points': len(points),
        'points': points,
        'snr': [12.0, 9.5][:len(points)],
        'noise': [],
        'range_profile': [],
        'tracks': [{
            'tid': tid,
            'position': [0.1, 1.2, 0.8],
            'velocity': [0.0, 0.1, 0.0],
            'acceleration': [0.0, 0.0, 0.0],
            'error_covariance': [0.5] * 16,
            'g': 9.81,
            'confidence': 0.9,
        }],
        'track_heights': [{'tid': tid, 'max_z': 1.7, 'min_z': 0.1}],
        'track_indexes': [tid, 255][:len(points)],
    }


def oob_frame(frame_num=1):
    """Hand-built parsed Out-of-Box frame dict (no tracking TLVs at all)."""
    return {
        'frame_num': frame_num, 'num_points': 1,
        'points': [(1.0, 2.0, 3.0, 0.0)], 'snr': [5.0],
        'noise': [1.0], 'range_profile': [7],
        'tracks': [], 'track_heights': [], 'track_indexes': [],
    }


def build_tracking_frame_bytes(frame_num, tid=7, n_points=3):
    """Byte-exact 3D People Tracking frame: TLVs 1020/1010/1012/1011.

    Structural values (frame_num, tid, point count) are chosen independently
    of the decoder; assertions rely only on those, not on recomputing the
    parser's cartesian conversion.
    """
    units = struct.pack('<5f', 0.001, 0.001, 0.01, 0.01, 0.1)
    points = b''.join(struct.pack('<2bh2H', 10, 20, 5, 300, 100)
                      for _ in range(n_points))

    target = struct.pack('<I27f', tid, *([0.0] * 27))
    height = struct.pack('<I2f', tid, 1.7, 0.1)
    indexes = bytes([tid] * n_points)

    return build_frame(
        [
            (TLV_TRACK_POINT_CLOUD, units + points),
            (TLV_TRACK_TARGET_LIST, target),
            (TLV_TRACK_TARGET_HEIGHT, height),
            (TLV_TRACK_TARGET_INDEX, indexes),
        ],
        num_detected_obj=n_points, frame_num=frame_num,
    )
