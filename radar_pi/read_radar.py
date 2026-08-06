#!/usr/bin/env python3
"""
Standalone point cloud reader for IWR6843AoP connected to Raspberry Pi
via uRAD IWR-AWR adapter (single UART mode).

The uRAD adapter connects the radar to the Pi's GPIO UART:
  - Pi TX (pin 8)  -> CFG_RX (radar receives config commands)
  - Pi RX (pin 10) <- DATA_TX (radar sends point cloud data)

This means only ONE serial port is used (/dev/serial0):
  1. Config is sent at 115200 baud
  2. Data is received at 921600 baud
  The baud rate switch happens after sending sensorStart.

Usage:
    python3 read_radar.py [--port PORT] [--config CFG]
"""

import serial
import struct
import time
import math
import sys
import os
import argparse
import numpy as np

# ---------- constants ----------
UART_MAGIC_WORD = bytearray(b'\x02\x01\x04\x03\x06\x05\x08\x07')
CLI_BAUD = 115200
DATA_BAUD = 921600
DEFAULT_PORT = '/dev/serial0'

# TLV types we care about
TLV_DETECTED_POINTS = 1
TLV_RANGE_PROFILE = 2
TLV_DETECTED_POINTS_SIDE_INFO = 7


# ---------- config sending ----------
def send_config(ser, config_path):
    """Send configuration commands to the radar via CLI port."""
    with open(config_path, 'r') as f:
        lines = f.readlines()

    # Filter comments and empty lines
    commands = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith('%'):
            continue
        commands.append(line)

    for cmd in commands:
        print(f"  TX> {cmd}")
        ser.write((cmd + '\n').encode())
        time.sleep(0.05)

        # Read acknowledgments
        wait_end = time.time() + 0.1
        while time.time() < wait_end:
            while ser.in_waiting > 0:
                resp = ser.readline()
                if resp:
                    text = resp.decode('utf-8', errors='replace').strip()
                    if text:
                        print(f"  RX< {text}")
            time.sleep(0.01)

    time.sleep(0.1)
    ser.reset_input_buffer()


# ---------- frame reading ----------
def find_magic_word(ser):
    """Read from serial until we find the 8-byte magic word. Returns magic word bytes."""
    index = 0
    frame_data = bytearray()

    while True:
        byte = ser.read(1)
        if len(byte) < 1:
            continue  # timeout, keep trying

        if byte[0] == UART_MAGIC_WORD[index]:
            index += 1
            frame_data.append(byte[0])
            if index == 8:
                return frame_data
        else:
            index = 0
            frame_data = bytearray()


def read_one_frame(ser):
    """Read one complete frame from the data port."""
    # Find magic word (start of frame)
    frame_data = find_magic_word(ser)

    # Read version (4 bytes) + totalPacketLen (4 bytes)
    header_rest = ser.read(8)
    if len(header_rest) < 8:
        return None
    frame_data += bytearray(header_rest)

    # Extract total packet length
    total_packet_len = struct.unpack('<I', header_rest[4:8])[0]

    # Read the rest of the frame (subtract magic_word + version + length = 16 bytes)
    remaining = total_packet_len - 16
    if remaining <= 0 or remaining > 100000:
        return None

    rest = ser.read(remaining)
    if len(rest) < remaining:
        return None
    frame_data += bytearray(rest)

    return frame_data


# ---------- frame parsing ----------
def parse_frame(frame_data):
    """Parse a standard mmWave SDK output frame and return point cloud + side info."""
    # Frame header: magic(8) + version(4) + totalPacketLen(4) + platform(4) +
    #               frameNum(4) + timeCPUCycles(4) + numDetectedObj(4) + numTLVs(4) + subFrameNum(4)
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

    # Initialize output
    point_cloud = np.zeros((num_detected_obj, 4))  # x, y, z, doppler
    snr_array = np.zeros(num_detected_obj)
    noise_array = np.zeros(num_detected_obj)
    range_profile = []

    # Parse TLVs
    offset = header_len
    tlv_header_len = 8  # type(4) + length(4)

    for i in range(num_tlvs):
        if offset + tlv_header_len > len(frame_data):
            break

        tlv_type, tlv_length = struct.unpack('<2I', frame_data[offset:offset + tlv_header_len])
        offset += tlv_header_len

        if offset + tlv_length > len(frame_data):
            break

        tlv_data = frame_data[offset:offset + tlv_length]

        if tlv_type == TLV_DETECTED_POINTS and num_detected_obj > 0:
            # Each point: x(float), y(float), z(float), doppler(float) = 16 bytes
            point_struct = '4f'
            point_size = struct.calcsize(point_struct)
            num_points = min(num_detected_obj, tlv_length // point_size)

            for j in range(num_points):
                try:
                    x, y, z, doppler = struct.unpack(point_struct,
                                                      tlv_data[j * point_size:(j + 1) * point_size])
                    point_cloud[j] = [x, y, z, doppler]
                except struct.error:
                    break

        elif tlv_type == TLV_DETECTED_POINTS_SIDE_INFO and num_detected_obj > 0:
            # Each point: snr(uint16), noise(uint16) = 4 bytes
            side_struct = '2H'
            side_size = struct.calcsize(side_struct)
            num_points = min(num_detected_obj, tlv_length // side_size)

            for j in range(num_points):
                try:
                    snr, noise = struct.unpack(side_struct,
                                               tlv_data[j * side_size:(j + 1) * side_size])
                    snr_array[j] = snr * 0.1      # convert from 0.1 dB to dB
                    noise_array[j] = noise * 0.1   # convert from 0.1 dB to dB
                except struct.error:
                    break

        elif tlv_type == TLV_RANGE_PROFILE:
            # Each range bin: uint32
            num_bins = tlv_length // 4
            for j in range(num_bins):
                try:
                    val = struct.unpack('<I', tlv_data[j * 4:(j + 1) * 4])[0]
                    range_profile.append(val)
                except struct.error:
                    break

        offset += tlv_length

    return {
        'frame_num': frame_num,
        'num_points': num_detected_obj,
        'points': point_cloud,       # Nx4: x, y, z, doppler
        'snr': snr_array,            # N: SNR in dB
        'noise': noise_array,        # N: noise in dB
        'range_profile': range_profile,
    }


# ---------- display ----------
def print_frame(data):
    """Print parsed frame data to console."""
    if data is None:
        return

    n = data['num_points']
    fn = data['frame_num']
    pts = data['points']
    snr = data['snr']

    if n == 0:
        print(f"  Frame {fn:5d} | 0 detected points")
        return

    # Count points with meaningful coordinates (not all-zero)
    active = np.any(pts[:n, :3] != 0, axis=1)
    active_count = int(np.sum(active))

    print(f"  Frame {fn:5d} | {n} points ({active_count} non-zero)")

    # Print each point
    for i in range(n):
        x, y, z, doppler = pts[i]
        s = snr[i]

        # Calculate range and azimuth from cartesian
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
    parser = argparse.ArgumentParser(description='Read point clouds from IWR6843AoP radar via uRAD adapter (single UART)')
    parser.add_argument('--port', default=DEFAULT_PORT,
                        help=f'Serial port (default: {DEFAULT_PORT})')
    parser.add_argument('--config', default='xwr6843_aop_out_of_box.cfg',
                        help='Config file path')
    parser.add_argument('--num-frames', type=int, default=0,
                        help='Number of frames to read (0=infinite)')
    parser.add_argument('--skip-config', action='store_true',
                        help='Skip sending config (sensor already configured)')
    args = parser.parse_args()

    print(f"uRAD IWR6843AoP Point Cloud Reader (single UART mode)")
    print(f"  Port: {args.port}")
    print(f"  Config: {args.config}")
    print()

    # Check config file
    if not args.skip_config and not os.path.isfile(args.config):
        print(f"ERROR: Config file not found: {args.config}")
        print("Make sure xwr6843_aop_out_of_box.cfg is in the same directory.")
        sys.exit(1)

    # Open serial port at CLI baud rate for configuration
    try:
        ser = serial.Serial(args.port, CLI_BAUD, timeout=0.5)
        print(f"Opened {args.port} @ {CLI_BAUD} baud (config mode)")
    except serial.SerialException as e:
        print(f"ERROR: Cannot open {args.port}: {e}")
        print("Make sure:")
        print("  1. The uRAD adapter is connected to the Pi GPIO header")
        print("  2. Serial is enabled on the Pi (sudo raspi-config -> Interface -> Serial)")
        print("     - Login shell over serial: NO")
        print("     - Serial hardware enabled: YES")
        print("  3. Your user has permissions: sudo usermod -aG dialout $USER")
        sys.exit(1)

    # Send configuration
    if not args.skip_config:
        print("\n--- Sending configuration ---")
        send_config(ser, args.config)
        print("--- Configuration sent ---\n")

    # Switch baud rate to receive data
    print(f"Switching baud rate to {DATA_BAUD} for data reception...")
    ser.baudrate = DATA_BAUD
    ser.timeout = 0.6
    ser.reset_input_buffer()

    # Wait for sensor to start sending
    print("Waiting for data frames...\n")
    time.sleep(0.5)
    ser.reset_input_buffer()

    # Read frames
    frame_count = 0
    error_count = 0

    try:
        while True:
            if args.num_frames > 0 and frame_count >= args.num_frames:
                break

            try:
                frame_data = read_one_frame(ser)
            except KeyboardInterrupt:
                raise
            except Exception as e:
                error_count += 1
                if error_count > 10:
                    print(f"Too many read errors ({error_count}). Stopping.")
                    break
                continue

            if frame_data is None:
                error_count += 1
                if error_count % 20 == 0:
                    print(f"  ... still waiting for valid frames ({error_count} attempts)")
                continue

            parsed = parse_frame(frame_data)
            if parsed is not None:
                error_count = 0
                frame_count += 1
                print_frame(parsed)
            else:
                error_count += 1

    except KeyboardInterrupt:
        print(f"\n\nStopped by user after {frame_count} frames.")

    # Cleanup
    print("\nStopping sensor...")
    try:
        ser.baudrate = CLI_BAUD
        time.sleep(0.1)
        ser.write(b'sensorStop\n')
        time.sleep(0.2)
    except:
        pass
    ser.close()
    print("Done.")


if __name__ == '__main__':
    main()
