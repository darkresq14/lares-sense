#!/usr/bin/env python3
"""
Unit tests for frame_display.format_frame.

These run with no hardware: they feed hand-built parsed-frame dicts (the shape
``tlv_parser.parse_frame`` returns) to ``format_frame`` and assert the rendered
text contains the 3D People Tracking sections (point cloud + tracks + heights +
indexes). That is the "server logs a fully-decoded frame" check, testable
without a sensor.

Run from the radar_pi/ directory:

    python3 -m unittest test_frame_display -v
"""

import unittest

from frame_display import format_frame


def _tracking_frame():
    return {
        'frame_num': 4242,
        'num_points': 2,
        'points': [(0.0, 1.0, 0.5, 0.2), (1.0, 2.0, 0.9, -0.1)],
        'snr': [12.0, 9.5],
        'noise': [],
        'range_profile': [],
        'tracks': [{
            'tid': 0,
            'position': [0.1, 1.2, 0.8],
            'velocity': [0.0, 0.1, 0.0],
            'acceleration': [0.0, 0.0, 0.0],
            'error_covariance': [0.0] * 16,
            'g': 9.81,
            'confidence': 0.9,
        }],
        'track_heights': [{'tid': 0, 'max_z': 1.7, 'min_z': 0.1}],
        'track_indexes': [0, 255],
    }


class FormatFrameTests(unittest.TestCase):

    def test_renders_none_as_empty(self):
        self.assertEqual(format_frame(None), '')

    def test_tracking_header_line_tags_3d_people_tracking(self):
        out = format_frame(_tracking_frame())
        self.assertIn('Frame  4242', out)
        self.assertIn('2 points', out)
        self.assertIn('1 tracks', out)
        self.assertIn('3D People Tracking', out)

    def test_renders_track_with_position_velocity_confidence(self):
        out = format_frame(_tracking_frame())
        self.assertIn('Tracks:', out)
        self.assertIn('[id= 0]', out)
        self.assertIn('pos=(  0.10,  1.20, 0.80)', out)
        self.assertIn('vel=( 0.00, 0.10, 0.00)', out)
        self.assertIn('conf=0.90', out)

    def test_renders_height_profile(self):
        out = format_frame(_tracking_frame())
        self.assertIn('height 0.10..1.70m (1.60m tall)', out)

    def test_renders_points_with_track_index_and_noise_label(self):
        out = format_frame(_tracking_frame())
        self.assertIn('Points:', out)
        # point 0 belongs to track 0
        self.assertIn('track=0', out)
        # point 1 (index 255) is labelled as noise
        self.assertIn('track=255 (noise)', out)

    def test_oob_frame_renders_without_tracking_sections(self):
        oob = {
            'frame_num': 1,
            'num_points': 1,
            'points': [(0.0, 1.0, 0.0, 0.0)],
            'snr': [5.0],
            'noise': [1.0],
            'range_profile': [],
            'tracks': [],
            'track_heights': [],
            'track_indexes': [],
        }
        out = format_frame(oob)
        self.assertIn('point cloud', out)
        self.assertNotIn('3D People Tracking', out)
        self.assertNotIn('Tracks:', out)
        self.assertNotIn('track=', out)


if __name__ == '__main__':
    unittest.main()
