#!/usr/bin/env python3
"""
Listens for raw TI mmWave radar frames over UDP, parses them, and prints the
decoded frame to the console - point cloud, and for the 3D People Tracking
demo also tracks, per-track heights and per-point track indexes.

Each UDP datagram should contain one complete TI frame (starting
with the magic word 02 01 04 03 06 05 08 07).

Runs on the server. No external dependencies. Usage:
    python3 radar_receiver.py [--port 5005]
"""

import socket
import argparse
import sys

from tlv_parser import parse_frame
from frame_display import print_frame
from frame_stream import FrameStream

# ---------- constants ----------
DEFAULT_PORT = 5005
RECV_BUFFER_SIZE = 65536


# Frame rendering (point cloud + tracks + heights + indexes) lives in
# frame_display.print_frame, shared with the Pi reader. parse_frame comes from
# tlv_parser and datagram buffering from frame_stream; this file keeps the UDP
# socket loop.


# ---------- main ----------
def main():
    parser = argparse.ArgumentParser(description='Receive radar frames over UDP')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT,
                        help=f'UDP port to listen on (default: {DEFAULT_PORT})')
    parser.add_argument('--bind', default='0.0.0.0',
                        help='Bind address (default: 0.0.0.0)')
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.bind, args.port))
    sock.settimeout(2.0)

    print(f"Radar UDP Receiver listening on {args.bind}:{args.port}")
    print("Waiting for frames... (Ctrl+C to stop)\n")

    # Buffer for accumulating data across datagrams; frame carving lives in
    # frame_stream.FrameStream, shared with the DB persister.
    stream = FrameStream()
    frame_count = 0
    tracking_frame_count = 0

    try:
        while True:
            try:
                data, addr = sock.recvfrom(RECV_BUFFER_SIZE)
            except socket.timeout:
                continue

            for frame_data in stream.feed(data):
                # Parse and display
                parsed = parse_frame(frame_data)
                if parsed is not None:
                    frame_count += 1
                    # Confirm the first fully-decoded 3D People Tracking frame
                    # (point cloud + tracks + heights + indexes) so the whole
                    # sensor -> Pi -> server pipe is visibly green in the log.
                    # Require ALL four sections: a frame with tracks but missing
                    # points/indexes must not trip a false success.
                    fully_decoded = bool(parsed.get('points')
                                         and parsed.get('tracks')
                                         and parsed.get('track_heights')
                                         and parsed.get('track_indexes'))
                    if fully_decoded:
                        tracking_frame_count += 1
                        if tracking_frame_count == 1:
                            print("*** Received first fully-decoded 3D People "
                                  "Tracking frame (point cloud + tracks + "
                                  "heights + indexes) ***")
                    print_frame(parsed)

    except KeyboardInterrupt:
        print(f"\nStopped after {frame_count} frames.")
    finally:
        sock.close()


if __name__ == '__main__':
    main()
