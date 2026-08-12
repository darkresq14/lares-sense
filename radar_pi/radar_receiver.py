#!/usr/bin/env python3
"""
Listens for raw TI mmWave radar frames over UDP, parses them,
and prints point cloud data to the console.

Each UDP datagram should contain one complete TI frame (starting
with the magic word 02 01 04 03 06 05 08 07).

Runs on the server. No external dependencies. Usage:
    python3 radar_receiver.py [--port 5005]
"""

import socket
import struct
import time
import math
import argparse
import sys

from tlv_parser import parse_frame, MAGIC_WORD

# ---------- constants ----------
DEFAULT_PORT = 5005
RECV_BUFFER_SIZE = 65536


# Frame parsing lives in tlv_parser.py (shared with the Pi reader); parse_frame
# and MAGIC_WORD are imported above. This file keeps the UDP socket loop and
# its own console printer.
def print_frame(data):
    """Print parsed frame to console."""
    if data is None:
        return

    n = data['num_points']
    fn = data['frame_num']
    pts = data['points']
    snr = data['snr']

    if n == 0:
        print(f"  Frame {fn:5d} | 0 detected points")
        return

    print(f"  Frame {fn:5d} | {n} points")

    for i in range(min(n, len(pts))):
        x, y, z, doppler = pts[i]
        s = snr[i] if i < len(snr) else 0.0

        rng = math.sqrt(x * x + y * y + z * z)
        if abs(y) > 0.001:
            azimuth = math.degrees(math.atan2(x, y))
        elif x >= 0:
            azimuth = 90.0
        else:
            azimuth = -90.0

        print(f"    [{i:3d}] x={x:7.3f} y={y:7.3f} z={z:7.3f}  "
              f"v={doppler:6.2f} m/s  range={rng:6.3f}m  "
              f"az={azimuth:6.1f}deg  SNR={s:5.1f}dB")


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

    # Buffer for accumulating data across datagrams
    buf = bytearray()
    frame_count = 0

    try:
        while True:
            try:
                data, addr = sock.recvfrom(RECV_BUFFER_SIZE)
            except socket.timeout:
                continue

            buf.extend(data)

            # Extract complete frames from buffer using magic word
            while True:
                # Find magic word
                idx = buf.find(MAGIC_WORD)
                if idx == -1:
                    # Keep last 7 bytes in case magic word is split
                    if len(buf) > 7:
                        buf = buf[-7:]
                    break

                # Discard bytes before magic word
                if idx > 0:
                    buf = buf[idx:]

                # Need at least header to get packet length
                if len(buf) < 16:
                    break

                total_packet_len = struct.unpack('<I', buf[12:16])[0]

                if total_packet_len < 40 or total_packet_len > 100000:
                    # Invalid frame, skip this magic word
                    buf = buf[1:]
                    continue

                # Check if we have the complete frame
                if len(buf) < total_packet_len:
                    break

                # Extract one complete frame
                frame_data = bytes(buf[:total_packet_len])
                buf = buf[total_packet_len:]

                # Parse and display
                parsed = parse_frame(frame_data)
                if parsed is not None:
                    frame_count += 1
                    print_frame(parsed)

    except KeyboardInterrupt:
        print(f"\nStopped after {frame_count} frames.")
    finally:
        sock.close()


if __name__ == '__main__':
    main()
