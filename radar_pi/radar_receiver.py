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

# ---------- constants ----------
MAGIC_WORD = bytearray(b'\x02\x01\x04\x03\x06\x05\x08\x07')

TLV_DETECTED_POINTS = 1
TLV_DETECTED_POINTS_SIDE_INFO = 7

DEFAULT_PORT = 5005
RECV_BUFFER_SIZE = 65536


# ---------- frame parsing ----------
def parse_frame(frame_data):
    """Parse a TI mmWave SDK frame. Returns dict with point cloud data."""
    header_struct = 'Q8I'  # 8 bytes magic + 8 x uint32 = 40 bytes
    header_len = struct.calcsize(header_struct)

    if len(frame_data) < header_len:
        return None

    try:
        magic, version, total_packet_len, platform, frame_num, \
            time_cpu_cycles, num_detected_obj, num_tlvs, sub_frame_num = \
            struct.unpack(header_struct, frame_data[:header_len])
    except struct.error:
        return None

    if num_detected_obj > 10000:
        return None

    points = []
    snr_values = []
    noise_values = []

    offset = header_len
    tlv_header_len = 8

    for i in range(num_tlvs):
        if offset + tlv_header_len > len(frame_data):
            break

        tlv_type, tlv_length = struct.unpack('<2I', frame_data[offset:offset + tlv_header_len])
        offset += tlv_header_len

        if offset + tlv_length > len(frame_data):
            break

        tlv_data = frame_data[offset:offset + tlv_length]

        if tlv_type == TLV_DETECTED_POINTS and num_detected_obj > 0:
            point_size = struct.calcsize('4f')
            num_points = min(num_detected_obj, tlv_length // point_size)
            for j in range(num_points):
                try:
                    x, y, z, doppler = struct.unpack('4f',
                        tlv_data[j * point_size:(j + 1) * point_size])
                    points.append((x, y, z, doppler))
                except struct.error:
                    break

        elif tlv_type == TLV_DETECTED_POINTS_SIDE_INFO and num_detected_obj > 0:
            side_size = struct.calcsize('2H')
            num_points = min(num_detected_obj, tlv_length // side_size)
            for j in range(num_points):
                try:
                    snr, noise = struct.unpack('2H',
                        tlv_data[j * side_size:(j + 1) * side_size])
                    snr_values.append(snr * 0.1)
                    noise_values.append(noise * 0.1)
                except struct.error:
                    break

        offset += tlv_length

    return {
        'frame_num': frame_num,
        'num_points': num_detected_obj,
        'points': points,
        'snr': snr_values,
        'noise': noise_values,
    }


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
