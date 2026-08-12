#!/usr/bin/env python3
"""
Bench bring-up / verifier for the IWR6843AoP over the USB dual-COM link
(Silicon Labs CP2105). Confirms a freshly flashed firmware actually streams data
and that ``tlv_parser`` decodes it live -- without needing the Pi.

Dual-COM (USB) mapping (see ``docs/hardware/flashing.md``):

  Enhanced COM port -> config UART (UARTA): send the CLI config at 115200 baud
  Standard COM port -> data UART  (UARTB): read TI frames at 921600 baud

Unlike the Pi single-UART path (``read_radar.py`` / ``radar_sender.py``), the
config port here is bidirectional, so config ACKs are visible. This is the
lab/bring-up path called out in ADR-0001; deployment stays single-UART on the Pi.

Usage:
    python3 bringup_dualcom.py --config-port COM4 --data-port COM3 \\
        --cfg xwr6843_aop_3d_people_track_6m.cfg [--num-frames 20]
"""

import argparse
import os
import sys
import time

import serial

from read_radar import send_config, read_one_frame, CLI_BAUD, DATA_BAUD
from tlv_parser import parse_frame
from frame_display import print_frame

DEFAULT_CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'xwr6843_aop_3d_people_track_6m.cfg')


def _open(port, baud, label):
    try:
        ser = serial.Serial(port, baud, timeout=0.5)
        print(f"Opened {label}: {port} @ {baud} baud")
        return ser
    except serial.SerialException as e:
        print(f"ERROR: cannot open {label} {port}: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    p = argparse.ArgumentParser(
        description='Bring up / verify IWR6843AoP firmware over USB dual-COM')
    p.add_argument('--config-port', required=True,
                   help='Config UART (UARTA) = Enhanced COM port (e.g. COM4)')
    p.add_argument('--data-port', required=True,
                   help='Data UART (UARTB) = Standard COM port (e.g. COM3)')
    p.add_argument('--cfg', default=DEFAULT_CFG, help='CLI config file')
    p.add_argument('--num-frames', type=int, default=20,
                   help='Frames to read before stopping (0=infinite)')
    p.add_argument('--skip-config', action='store_true',
                   help='Skip sending config (sensor already configured)')
    args = p.parse_args()

    if not args.skip_config and not os.path.isfile(args.cfg):
        print(f"ERROR: config file not found: {args.cfg}", file=sys.stderr)
        sys.exit(1)

    print("IWR6843AoP dual-COM bring-up")
    print(f"  config: {args.config_port} @ {CLI_BAUD}")
    print(f"  data:   {args.data_port} @ {DATA_BAUD}")
    print(f"  cfg:    {args.cfg}")
    print()

    config_ser = _open(args.config_port, CLI_BAUD, 'config')

    if not args.skip_config:
        print("--- Sending configuration (ACKs visible on config UART) ---")
        send_config(config_ser, args.cfg)
        print("--- Configuration sent ---\n")

    data_ser = _open(args.data_port, DATA_BAUD, 'data')
    data_ser.timeout = 0.6
    time.sleep(0.5)
    data_ser.reset_input_buffer()
    print("Reading frames... (Ctrl+C to stop)\n")

    frame_count = 0
    error_count = 0
    fully_decoded_count = 0

    try:
        while True:
            if args.num_frames > 0 and frame_count >= args.num_frames:
                break
            try:
                raw = read_one_frame(data_ser)
            except KeyboardInterrupt:
                raise
            except Exception:
                error_count += 1
                if error_count > 20:
                    print("Too many read errors. Stopping.")
                    break
                continue

            if raw is None:
                error_count += 1
                continue

            parsed = parse_frame(raw)
            if parsed is None:
                error_count += 1
                continue

            error_count = 0
            frame_count += 1

            fully_decoded = bool(parsed['points'] and parsed['tracks']
                                 and parsed['track_heights']
                                 and parsed['track_indexes'])
            if fully_decoded:
                fully_decoded_count += 1
                if fully_decoded_count == 1:
                    print("*** First fully-decoded 3D People Tracking frame "
                          "(point cloud + tracks + heights + indexes) ***")
            print_frame(parsed)
            print()

    except KeyboardInterrupt:
        print(f"\nStopped after {frame_count} frames "
              f"({fully_decoded_count} fully-decoded).")

    print("\nStopping sensor...")
    try:
        time.sleep(0.1)
        config_ser.write(b'sensorStop\n')
        time.sleep(0.2)
    except Exception:
        pass
    config_ser.close()
    data_ser.close()
    print("Done.")


if __name__ == '__main__':
    main()
