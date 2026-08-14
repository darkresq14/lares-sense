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

from read_radar import read_one_frame, CLI_BAUD, DATA_BAUD
from tlv_parser import parse_frame
from frame_display import print_frame

DEFAULT_CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'xwr6843_aop_3d_people_track_6m.cfg')

# ACK keywords that mark a CLI command as finished (so we can send the next one).
_ACK_DONE = ('Done', 'mmwDemo', 'Ignored')
# Keywords that mean the command was rejected — surface but keep going.
_ACK_ERR = ('Error', 'error', 'rejected', 'Usage')


def send_config_acked(ser, cfg_path, per_cmd_timeout=8.0):
    """Send the CLI config on the bidirectional config UART, waiting for each
    command's ``Done`` (or error) ACK before sending the next.

    Unlike ``read_radar.send_config`` (which fires blind — the Pi single-UART
    path never sees ACKs), this is safe for the complex 3D People Tracking
    config: it won't overrun the CLI and lose commands.
    """
    with open(cfg_path, 'r') as f:
        commands = [ln.strip() for ln in f if ln.strip() and not ln.startswith('%')]

    ser.reset_input_buffer()
    rejected = 0
    for cmd in commands:
        if cmd == 'sensorStart':
            time.sleep(1.0)  # let the heavy 3D PT config settle before starting
        print(f"  TX> {cmd}")
        ser.reset_input_buffer()
        ser.write((cmd + '\n').encode())
        # sensorStart (tracker init) is slow -- give it a long ACK window.
        timeout = 20.0 if cmd == 'sensorStart' else per_cmd_timeout
        deadline = time.time() + timeout
        acked = False
        while time.time() < deadline:
            chunk = ser.read(64).decode('utf-8', errors='replace')
            if not chunk:
                time.sleep(0.02)
                continue
            for line in chunk.splitlines():
                line = line.strip()
                if not line:
                    continue
                print(f"  RX< {line}")
                if any(k in line for k in _ACK_ERR):
                    acked = True
                    rejected += 1
                if any(k in line for k in _ACK_DONE) or any(k in line for k in _ACK_ERR):
                    acked = True
            if acked:
                break
        if not acked:
            print("  [no ACK within timeout]")
    return rejected


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
        print("--- Sending configuration (ACK-aware on config UART) ---")
        rejected = send_config_acked(config_ser, args.cfg)
        print(f"--- Configuration sent ({rejected} rejected) ---\n")
        if rejected:
            print("WARNING: some commands were rejected; the sensor may not stream. "
                  "Check the config matches the flashed firmware.\n")
        # Release the config UART before reading the data UART -- matches the
        # proven diag path (holding it open correlated with no data on COM3).
        config_ser.close()
        print("(config UART released)\n")

    data_ser = _open(args.data_port, DATA_BAUD, 'data')
    data_ser.timeout = 1.0
    time.sleep(0.5)
    data_ser.reset_input_buffer()

    # Bounded "is data flowing?" probe so we never hang when nothing streams.
    print("Probing data port for a stream...")
    probe_deadline = time.time() + 8.0
    probe = b''
    while time.time() < probe_deadline:
        probe = data_ser.read(4096)
        if probe:
            break
    if not probe:
        print(f"No data on {args.data_port} @ {DATA_BAUD} within 8s. "
              "Sensor not streaming — check config was accepted, the data COM "
              "port, and baud.")
        data_ser.close()
        _stop(args.config_port)
        return
    print(f"Data flowing ({len(probe)} bytes) — syncing frames...\n")
    data_ser.timeout = 0.6
    # 'probe' bytes already consumed; read_one_frame resyncs on the next magic.

    frame_count = 0
    error_count = 0
    fully_decoded_count = 0
    phase_deadline = time.time() + 30.0  # hard cap on the read phase

    try:
        while True:
            if args.num_frames > 0 and frame_count >= args.num_frames:
                break
            if time.time() > phase_deadline:
                print("Read phase timed out.")
                break
            try:
                raw = read_one_frame(data_ser)
            except KeyboardInterrupt:
                raise
            except Exception:
                error_count += 1
                if error_count > 40:
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

    print(f"\n{frame_count} frames decoded ({fully_decoded_count} fully-decoded).")
    data_ser.close()
    _stop(args.config_port)


def _stop(config_port):
    """Reopen the config UART briefly to stop the sensor."""
    print("\nStopping sensor...")
    try:
        c = serial.Serial(config_port, CLI_BAUD, timeout=0.5)
        time.sleep(0.1)
        c.write(b'sensorStop\n')
        time.sleep(0.3)
        c.close()
    except Exception as e:
        print(f"  (could not send sensorStop: {e})")
    print("Done.")


if __name__ == '__main__':
    main()
