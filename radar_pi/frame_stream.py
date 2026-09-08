#!/usr/bin/env python3
"""
Byte-buffer frame extraction for UDP consumers.

The radar sender forwards raw TI mmWave SDK frames over UDP. Datagrams can
arrive fragmented (one frame split across datagrams), glued (several frames in
one datagram) or polluted (garbage bytes around frames), so every UDP consumer
accumulates incoming bytes in a buffer and carves complete frames out of it
using the 8-byte magic word.

``FrameStream`` is that buffer, factored out of ``radar_receiver.py`` so the
receiver and the DB persister (``radar_persist.py``) share one implementation.
It has no socket dependency: bytes in, complete frames out, ready to feed
``tlv_parser.parse_frame``.

Rules (matching the receiver's original loop):

- Bytes before a magic word are discarded.
- A frame is complete when the header's ``totalPacketLen`` bytes are buffered;
  an implausible length (``< 40`` or ``> 100000``) invalidates that magic-word
  occurrence and the search resumes one byte later.
- With no magic word in the buffer, only the last 7 bytes are retained, since a
  magic word could straddle the datagram boundary.
"""

from tlv_parser import MAGIC_WORD, HEADER_LEN

#: Magic word (8) + version (4) + totalPacketLen (4): the minimum bytes needed
#: before totalPacketLen can be read at offset 12.
_MIN_HEADER_FOR_LEN = 16

_MIN_FRAME_LEN = HEADER_LEN          # 40: header alone is the shortest frame
_MAX_FRAME_LEN = 100000              # sanity bound from tlv_parser

#: Longest magic-word prefix that can straddle two feeds (8 - 1).
_TAIL_KEEP = len(MAGIC_WORD) - 1


class FrameStream:
    """Feed datagram bytes in, get complete raw frames out.

    ``feed(data)`` appends ``data`` to the internal buffer and returns the
    list of complete frames extracted in order. Buffer state survives across
    feeds, so frames split across datagrams are reassembled.
    """

    def __init__(self):
        self._buf = bytearray()

    def feed(self, data):
        """Append incoming bytes and return every complete frame they yield."""
        self._buf.extend(data)
        frames = []

        while True:
            idx = self._buf.find(MAGIC_WORD)
            if idx == -1:
                # No magic word: keep only a possible straddling tail.
                if len(self._buf) > _TAIL_KEEP:
                    del self._buf[:-_TAIL_KEEP]
                break

            # Discard bytes before the magic word.
            if idx > 0:
                del self._buf[:idx]

            # Need magic + version + totalPacketLen before deciding anything.
            if len(self._buf) < _MIN_HEADER_FOR_LEN:
                break

            total_packet_len = int.from_bytes(self._buf[12:16], 'little')

            if total_packet_len < _MIN_FRAME_LEN or total_packet_len > _MAX_FRAME_LEN:
                # Invalid header: skip this magic-word occurrence.
                del self._buf[:1]
                continue

            if len(self._buf) < total_packet_len:
                break  # wait for the rest of the frame

            frames.append(bytes(self._buf[:total_packet_len]))
            del self._buf[:total_packet_len]

        return frames
