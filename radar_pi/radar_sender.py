#!/usr/bin/env python3
"""
Reads radar point cloud data from IWR6843AoP via UART and streams
raw TI frames over UDP to a remote server.

Each UDP datagram contains one complete TI mmWave SDK frame (including
the magic word), so the receiver can parse frames the same way.

Runs on Raspberry Pi. Usage:
    python3 radar_sender.py --host <SERVER_IP> [--port 5005]

For 3D People Tracking, pass the AOP tracking config:
    --config radar_pi/xwr6843_aop_3d_people_track_6m.cfg
The default config is the Out-of-Box Demo.
"""

import sys
import os
import time
import socket
import argparse

from read_radar import (
    send_config, read_one_frame, CLI_BAUD, DATA_BAUD, DEFAULT_PORT
)
from tlv_parser import parse_frame
import serial


def main():
    parser = argparse.ArgumentParser(description='Read radar data and stream over UDP')
    parser.add_argument('--host', required=True, help='Server IP address to send UDP to')
    parser.add_argument('--port', type=int, default=5005, help='UDP port (default: 5005)')
    parser.add_argument('--serial-port', default=DEFAULT_PORT,
                        help=f'Serial port (default: {DEFAULT_PORT})')
    parser.add_argument('--config', default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                         'xwr6843_aop_out_of_box.cfg'),
                        help='Config file path')
    parser.add_argument('--skip-config', action='store_true',
                        help='Skip sending config (sensor already configured)')
    args = parser.parse_args()

    server_addr = (args.host, args.port)

    print(f"Radar UDP Sender")
    print(f"  Serial: {args.serial_port}")
    print(f"  Config: {args.config}")
    print(f"  Target: {args.host}:{args.port}")
    print()

    # Open serial port
    try:
        ser = serial.Serial(args.serial_port, CLI_BAUD, timeout=0.5)
        print(f"Opened {args.serial_port} @ {CLI_BAUD} baud")
    except serial.SerialException as e:
        print(f"ERROR: Cannot open {args.serial_port}: {e}")
        sys.exit(1)

    # Create UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    print(f"UDP socket ready -> {args.host}:{args.port}")

    # Send config
    if not args.skip_config:
        if not os.path.isfile(args.config):
            print(f"ERROR: Config file not found: {args.config}")
            ser.close()
            sys.exit(1)
        print("\n--- Sending configuration ---")
        send_config(ser, args.config)
        print("--- Configuration sent ---\n")

    # Switch baud rate for data
    print(f"Switching to {DATA_BAUD} baud...")
    ser.baudrate = DATA_BAUD
    ser.timeout = 0.6
    ser.reset_input_buffer()
    time.sleep(0.5)
    ser.reset_input_buffer()
    print("Streaming frames... (Ctrl+C to stop)\n")

    frame_count = 0
    error_count = 0

    try:
        while True:
            try:
                raw_frame = read_one_frame(ser)
            except KeyboardInterrupt:
                raise
            except Exception:
                error_count += 1
                if error_count > 10:
                    print("Too many errors. Stopping.")
                    break
                continue

            if raw_frame is None:
                error_count += 1
                continue

            # Send raw frame over UDP
            try:
                sock.sendto(raw_frame, server_addr)
            except Exception as e:
                print(f"UDP send error: {e}")
                continue

            # Parse for local summary
            parsed = parse_frame(raw_frame)
            if parsed is not None:
                error_count = 0
                frame_count += 1
                fn = parsed['frame_num']
                n = parsed['num_points']
                n_tracks = len(parsed['tracks'])
                print(f"  Frame {fn:5d} | {n} points | {n_tracks} tracks | "
                      f"{len(raw_frame)} bytes -> {args.host}:{args.port}")
            else:
                error_count += 1

    except KeyboardInterrupt:
        print(f"\nStopped after {frame_count} frames.")

    # Cleanup
    print("Stopping sensor...")
    try:
        ser.baudrate = CLI_BAUD
        time.sleep(0.1)
        ser.write(b'sensorStop\n')
        time.sleep(0.2)
    except Exception:
        pass
    ser.close()
    sock.close()
    print("Done.")


if __name__ == '__main__':
    main()
