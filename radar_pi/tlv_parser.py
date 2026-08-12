#!/usr/bin/env python3
"""
Transport-agnostic parser for TI mmWave SDK data frames.

A "frame" is the binary blob the radar emits on its data UART (and that
``radar_sender.py`` forwards unchanged over UDP): an 8-byte magic word, a
40-byte header, then a sequence of Type-Length-Value (TLV) items, padded to a
32-byte boundary. See ``docs/hardware/sensor-reference.md`` ("UART Data
Protocol") for the byte layout.

The single public entry point is :func:`parse_frame`, which takes a raw frame
buffer that **starts at the magic word** and returns the parsed frame as a dict.

This module deliberately has **no dependency on serial ports, sockets, numpy or
any GUI**. It exists so the frame format can be decoded on a captured buffer
with no hardware attached, and shared by every transport (Pi single-UART
reader, UDP sender/receiver, and later the 3D People Tracking pipeline).

Out-of-Box Demo TLVs currently decoded (mmWave SDK 3.6 regression baseline):

    1  - Detected Points          (x, y, z, doppler  - 4 x float32 per point)
    2  - Range Profile            (one uint32 per range bin)
    7  - Detected Points Side Info (snr, noise       - 2 x uint16 per point)

The 3D People Tracking TLV type IDs are exposed as constants below so the next
ticket (#3) can fill in their decoders. Until then those TLVs are recognised
but skipped, so they never break Out-of-Box parsing.
"""

import struct

# --- Frame sync --------------------------------------------------------------

#: 8-byte magic word that starts every TI mmWave SDK frame:
#: ``02 01 04 03 06 05 08 07``.
MAGIC_WORD = b'\x02\x01\x04\x03\x06\x05\x08\x07'

#: Frame header = magic(8) + version(4) + totalPacketLen(4) + platform(4) +
#: frameNum(4) + timeCPUCycles(4) + numDetectedObj(4) + numTLVs(4) +
#: subFrameNum(4) = 40 bytes.
HEADER_STRUCT = '<Q8I'
HEADER_LEN = struct.calcsize(HEADER_STRUCT)  # 40

#: TLV header = type(4) + length(4) = 8 bytes.
TLV_HEADER_STRUCT = '<2I'
TLV_HEADER_LEN = struct.calcsize(TLV_HEADER_STRUCT)  # 8

# --- Out-of-Box Demo TLV types (mmWave SDK 3.6) ------------------------------

TLV_DETECTED_POINTS = 1            # x, y, z, doppler (4 x float32 / point)
TLV_RANGE_PROFILE = 2              # one uint32 per range bin
TLV_DETECTED_POINTS_SIDE_INFO = 7  # snr, noise (2 x uint16 / point)

# --- 3D People Tracking TLV types (placeholders; decoders land in #3) --------

TLV_TRACK_TARGET_LIST = 1010    # trackData
TLV_TRACK_TARGET_INDEX = 1011   # trackIndexes
TLV_TRACK_TARGET_HEIGHT = 1012  # heightData
TLV_TRACK_POINT_CLOUD = 1020    # extended point cloud

# --- Sanity bounds -----------------------------------------------------------

MAX_DETECTED_OBJ = 10000
MAX_FRAME_LEN = 100000

# --- Per-record struct sizes for the OOB decoders ----------------------------

_POINT_STRUCT = '<4f'                  # x, y, z, doppler
_POINT_SIZE = struct.calcsize(_POINT_STRUCT)      # 16
_SIDE_STRUCT = '<2H'                   # snr, noise
_SIDE_SIZE = struct.calcsize(_SIDE_STRUCT)        # 4
_RANGE_BIN_STRUCT = '<I'               # one range-bin value
_RANGE_BIN_SIZE = struct.calcsize(_RANGE_BIN_STRUCT)  # 4

#: scale factor for the side-info SNR/noise fields (stored in 0.1 dB units).
dB_SCALE = 0.1


def _decode_detected_points(payload, num_detected_obj):
    """Decode TLV 1 -> list of (x, y, z, doppler) tuples."""
    points = []
    if num_detected_obj <= 0:
        return points
    count = min(num_detected_obj, len(payload) // _POINT_SIZE)
    for i in range(count):
        x, y, z, doppler = struct.unpack_from(_POINT_STRUCT, payload, i * _POINT_SIZE)
        points.append((x, y, z, doppler))
    return points


def _decode_side_info(payload, num_detected_obj):
    """Decode TLV 7 -> (snr_list, noise_list) in dB (input is 0.1 dB units)."""
    snr, noise = [], []
    if num_detected_obj <= 0:
        return snr, noise
    count = min(num_detected_obj, len(payload) // _SIDE_SIZE)
    for i in range(count):
        s, n = struct.unpack_from(_SIDE_STRUCT, payload, i * _SIDE_SIZE)
        snr.append(s * dB_SCALE)
        noise.append(n * dB_SCALE)
    return snr, noise


def _decode_range_profile(payload):
    """Decode TLV 2 -> list of uint32 range-bin values."""
    count = len(payload) // _RANGE_BIN_SIZE
    return [struct.unpack_from(_RANGE_BIN_STRUCT, payload, i * _RANGE_BIN_SIZE)[0]
            for i in range(count)]


def parse_frame(frame_data):
    """Parse one TI mmWave SDK frame buffer into a dict.

    ``frame_data`` must start at the magic word and contain at least the full
    40-byte header. Returns ``None`` for a buffer that is too short, has a
    malformed header, or claims an absurd number of detected objects; the
    caller treats ``None`` as "not a usable frame".

    The returned dict has the keys:

    ``frame_num``      int   - frame counter from the header
    ``num_points``     int   - header's numDetectedObj
    ``points``         list  - (x, y, z, doppler) tuples (TLV 1)
    ``snr``            list  - per-point SNR in dB (TLV 7)
    ``noise``          list  - per-point noise in dB (TLV 7)
    ``range_profile``  list  - uint32 range-bin values (TLV 2)

    Any list is empty when its TLV is absent from the frame.
    """
    if frame_data is None or len(frame_data) < HEADER_LEN:
        return None

    try:
        (_magic, _version, _total_packet_len, _platform, frame_num,
         _time_cpu_cycles, num_detected_obj, num_tlvs,
         _sub_frame_num) = struct.unpack(HEADER_STRUCT, frame_data[:HEADER_LEN])
    except struct.error:
        return None

    if num_detected_obj > MAX_DETECTED_OBJ:
        return None

    points, snr, noise, range_profile = [], [], [], []

    offset = HEADER_LEN
    for _ in range(num_tlvs):
        if offset + TLV_HEADER_LEN > len(frame_data):
            break

        tlv_type, tlv_length = struct.unpack_from(TLV_HEADER_STRUCT, frame_data, offset)
        offset += TLV_HEADER_LEN

        if tlv_length > MAX_FRAME_LEN or offset + tlv_length > len(frame_data):
            break

        payload = frame_data[offset:offset + tlv_length]
        offset += tlv_length

        if tlv_type == TLV_DETECTED_POINTS:
            points = _decode_detected_points(payload, num_detected_obj)
        elif tlv_type == TLV_DETECTED_POINTS_SIDE_INFO:
            snr, noise = _decode_side_info(payload, num_detected_obj)
        elif tlv_type == TLV_RANGE_PROFILE:
            range_profile = _decode_range_profile(payload)
        # 3D People Tracking TLVs (1010/1011/1012/1020) are recognised by their
        # constants above but not decoded yet, so they are skipped here.
        # Their decoders are added in #3.

    return {
        'frame_num': frame_num,
        'num_points': num_detected_obj,
        'points': points,
        'snr': snr,
        'noise': noise,
        'range_profile': range_profile,
    }
