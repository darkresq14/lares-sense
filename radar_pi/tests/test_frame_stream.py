#!/usr/bin/env python3
"""
Unit tests for frame_stream.FrameStream.

The UDP receiver (and the new DB persister) both accumulate datagram bytes in
a buffer and carve complete TI mmWave frames out of it using the magic word.
That byte-buffering logic lives in frame_stream so it is unit-testable with no
socket and shared by every UDP consumer.

Frames are built byte-exact with the helper from test_tlv_parser. Run from the
radar_pi/ directory:

    python3 -m unittest tests.test_frame_stream -v
"""

import struct
import unittest

from frame_stream import FrameStream
from tlv_parser import MAGIC_WORD, HEADER_LEN
from tests.test_tlv_parser import build_frame


def _one_frame(frame_num=7):
    """A minimal valid frame with one detected point."""
    payload = struct.pack('<4f', 1.0, 2.0, 3.0, 0.5)
    frame = build_frame([(1, payload)], num_detected_obj=1, frame_num=frame_num)
    assert frame.startswith(MAGIC_WORD)
    return frame


class FrameStreamTests(unittest.TestCase):

    def test_feed_complete_frame_returns_it(self):
        stream = FrameStream()
        frame = _one_frame()
        out = stream.feed(frame)
        self.assertEqual(out, [frame])

    def test_frame_split_across_datagrams_is_reassembled(self):
        stream = FrameStream()
        frame = _one_frame()
        cut = len(frame) // 2
        self.assertEqual(stream.feed(frame[:cut]), [])
        self.assertEqual(stream.feed(frame[cut:]), [frame])

    def test_magic_word_split_across_datagrams_is_reassembled(self):
        stream = FrameStream()
        frame = _one_frame()
        # Split exactly inside the 8-byte magic word.
        self.assertEqual(stream.feed(frame[:5]), [])
        self.assertEqual(stream.feed(frame[5:]), [frame])

    def test_garbage_before_frame_is_discarded(self):
        stream = FrameStream()
        frame = _one_frame()
        out = stream.feed(b'\xde\xad\xbe\xef' + frame)
        self.assertEqual(out, [frame])

    def test_invalid_length_after_magic_is_skipped(self):
        stream = FrameStream()
        # Magic word followed by a bogus totalPacketLen (0): the stream must
        # drop this magic-word occurrence, not the rest of the buffer.
        bad = MAGIC_WORD + struct.pack('<I', 0)
        frame = _one_frame()
        out = stream.feed(bad + frame)
        self.assertEqual(out, [frame])

    def test_partial_frame_without_magic_is_trimmed(self):
        stream = FrameStream()
        # Pure garbage with no magic word anywhere: only the last 7 bytes may
        # be retained (a magic word could still straddle the boundary).
        stream.feed(b'\x00' * 100)
        frame = _one_frame()
        self.assertEqual(stream.feed(frame), [frame])

    def test_two_frames_in_one_datagram(self):
        stream = FrameStream()
        f1, f2 = _one_frame(1), _one_frame(2)
        out = stream.feed(f1 + f2)
        self.assertEqual(out, [f1, f2])

    def test_short_buffer_of_header_only_waits(self):
        stream = FrameStream()
        # Magic + 3 header bytes: not enough to read totalPacketLen yet, so
        # the stream must wait instead of invalidating the frame start.
        self.assertEqual(stream.feed(_one_frame()[:11]), [])
        self.assertEqual(stream.feed(_one_frame()[11:]), [_one_frame()])


if __name__ == '__main__':
    unittest.main()
