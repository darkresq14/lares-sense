#!/usr/bin/env python3
"""
Unit tests for frame_store.frame_to_row (no database required).

``frame_to_row`` turns a parsed frame dict (the shape ``tlv_parser.parse_frame``
returns) into the column values for one ``frames`` table row. The expectations
here are hand-written literals (shared via ``test_fixtures``), independent of
the serialization code, so a regression in column mapping fails loudly.

Integration tests against a real Postgres+TimescaleDB live in
``test_frame_store_integration.py`` (requires Docker; auto-skipped otherwise).

Run from the radar_pi/ directory:

    python3 -m unittest tests.test_frame_store -v
"""

import datetime
import json
import unittest

from frame_store import frame_to_row
from tests.test_fixtures import oob_frame, tracking_frame


TS = datetime.datetime(2025, 8, 14, 12, 0, 0,
                       tzinfo=datetime.timezone.utc)


class FrameToRowTests(unittest.TestCase):

    def test_point_cloud_split_into_parallel_arrays(self):
        row = frame_to_row(tracking_frame(), TS)
        self.assertEqual(row['ts'], TS)
        self.assertEqual(row['frame_num'], 4242)
        self.assertEqual(row['num_points'], 2)
        self.assertEqual(row['x'], [1.0, 3.0])
        self.assertEqual(row['y'], [2.0, 4.0])
        self.assertEqual(row['z'], [0.5, 0.9])
        self.assertEqual(row['doppler'], [0.2, -0.1])
        self.assertEqual(row['snr'], [12.0, 9.5])

    def test_tracks_and_heights_stored_as_json(self):
        frame = tracking_frame()
        row = frame_to_row(frame, TS)
        self.assertEqual(json.loads(row['track_data']), frame['tracks'])
        self.assertEqual(json.loads(row['height_data']), frame['track_heights'])

    def test_track_indexes_stored_as_list(self):
        row = frame_to_row(tracking_frame(), TS)
        self.assertEqual(row['track_indexes'], [3, 255])

    def test_absent_tracking_tlvs_are_null(self):
        # An Out-of-Box frame: no tracks / heights / indexes at all.
        row = frame_to_row(oob_frame(), TS)
        self.assertIsNone(row['track_data'])
        self.assertIsNone(row['height_data'])
        self.assertIsNone(row['track_indexes'])
        # Point arrays are still arrays (empty when no points) so unnest-style
        # queries never trip over NULL columns.
        self.assertEqual(row['x'], [1.0])

    def test_empty_point_cloud_keeps_empty_arrays(self):
        parsed = tracking_frame()
        parsed.update(num_points=0, points=[], snr=[],
                      track_indexes=[], tracks=[], track_heights=[])
        row = frame_to_row(parsed, TS)
        for col in ('x', 'y', 'z', 'doppler', 'snr'):
            self.assertEqual(row[col], [])


if __name__ == '__main__':
    unittest.main()
