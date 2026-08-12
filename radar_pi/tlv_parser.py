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

Out-of-Box Demo TLVs decoded (mmWave SDK 3.6 regression baseline):

    1  - Detected Points          (x, y, z, doppler  - 4 x float32 per point)
    2  - Range Profile            (one uint32 per range bin)
    7  - Detected Points Side Info (snr, noise       - 2 x uint16 per point)

3D People Tracking demo TLVs decoded (Radar Toolbox ``3D_people_track_6843``):

    1020 - Compressed Point Cloud  (spherical -> cartesian x, y, z, doppler + snr)
    1010 - Target List             (trackData: id + pos/vel/acc + error cov + g + conf)
    1012 - Target Height           (heightData: per-track max_z / min_z)
    1011 - Target Index            (trackIndexes: track id per detected point)

Any other TLV type is recognised by length and skipped, so an unrecognised TLV
never breaks parsing of the rest of the frame.
"""

import math
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

# --- 3D People Tracking TLV types (mmWave SDK 3.6 + Radar Toolbox) ----------
#
# The 3D People Tracking demo (3D_people_track_6843) emits a different TLV set
# than the Out-of-Box Demo: the point cloud is a *compressed spherical* cloud
# (TLV 1020, decoded to cartesian here), plus the tracker outputs below.
# See ``docs/hardware/sensor-reference.md`` and the demo source in
# ``docs_and_tools/.../3D_People_Tracking/src/6843/mss/mss_main.c``.

TLV_TRACK_POINT_CLOUD = 1020    # compressed spherical point cloud (range/az/el/dop/snr)
TLV_TRACK_TARGET_LIST = 1010    # trackData   - per track: id + pos/vel/acc + ec[16] + g + conf
TLV_TRACK_TARGET_INDEX = 1011   # trackIndexes - one track id per detected point
TLV_TRACK_TARGET_HEIGHT = 1012  # heightData  - per track: max_z / min_z

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

# --- 3D People Tracking per-record struct sizes -----------------------------

#: TLV 1020 decompression units: elevationUnit, azimuthUnit, dopplerUnit,
#: rangeUnit, snrUnit (5 x float32).
_PC_UNIT_STRUCT = '<5f'
_PC_UNIT_SIZE = struct.calcsize(_PC_UNIT_STRUCT)        # 20
#: TLV 1020 per point: elevation(i8), azimuth(i8), doppler(i16), range(u16),
#: snr(u16). Signed widths handle the firmware's int8/int16 sign extension.
_PC_POINT_STRUCT = '<2bh2H'
_PC_POINT_SIZE = struct.calcsize(_PC_POINT_STRUCT)       # 8

#: TLV 1010 per track: tid(uint32) + 27 float32 (pos/vel/acc + ec[16] + g + conf).
_TARGET_STRUCT = '<I27f'
_TARGET_SIZE = struct.calcsize(_TARGET_STRUCT)          # 112

#: TLV 1012 per track: tid(uint32) + max_z(float) + min_z(float).
_HEIGHT_STRUCT = '<I2f'
_HEIGHT_SIZE = struct.calcsize(_HEIGHT_STRUCT)          # 12

#: TLV 1011 per detected point: one uint8 track id (253/254/255 = not associated).
_TARGET_INDEX_SIZE = 1

#: Special TLV 1011 codes for points not associated with any track.
TRACK_INDEX_WEAK_SNR = 253   # point not associated, SNR too weak
TRACK_INDEX_BOUNDS = 254     # point outside the boundary of interest
TRACK_INDEX_NOISE = 255      # point not associated, considered noise


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


# --- 3D People Tracking decoders --------------------------------------------

def _decode_compressed_point_cloud(payload, num_detected_obj):
    """Decode TLV 1020 -> (points, snr) for the 3D People Tracking demo.

    The firmware emits a *compressed spherical* point cloud: a 20-byte unit
    block (5 float32 scale factors) followed by one 8-byte point each. Each
    point stores signed elevation/azimuth (int8, in unit-radians), signed
    doppler (int16, in m/s-units), unsigned range (uint16, in m-units) and
    unsigned SNR (uint16, linear units). Points are decompressed to spherical
    and converted to cartesian (x, y, z) the same way TI's reference visualiser
    does (``sphericalToCartesianPointCloud``):

        x = range * sin(azimuth)   * cos(elevation)
        y = range * cos(azimuth)   * cos(elevation)
        z = range * sin(elevation)

    Returns a ``(points, snr)`` pair matching the OOB TLV 1/7 shape so callers
    can treat the detected point cloud uniformly regardless of demo. ``points``
    is a list of (x, y, z, doppler) tuples; ``snr`` is per-point linear SNR.
    """
    points, snr = [], []
    if num_detected_obj <= 0 or len(payload) < _PC_UNIT_SIZE:
        return points, snr

    (elev_unit, az_unit, dop_unit, range_unit, snr_unit) = struct.unpack_from(
        _PC_UNIT_STRUCT, payload, 0)

    available = (len(payload) - _PC_UNIT_SIZE) // _PC_POINT_SIZE
    count = min(num_detected_obj, available)
    base = _PC_UNIT_SIZE
    for i in range(count):
        elevation, azimuth, doppler, rng_raw, snr_raw = struct.unpack_from(
            _PC_POINT_STRUCT, payload, base + i * _PC_POINT_SIZE)
        rng = rng_raw * range_unit
        az = azimuth * az_unit
        el = elevation * elev_unit
        ce = math.cos(el)
        points.append((rng * math.sin(az) * ce,
                       rng * math.cos(az) * ce,
                       rng * math.sin(el),
                       doppler * dop_unit))
        snr.append(snr_raw * snr_unit)
    return points, snr


def _decode_target_list(payload):
    """Decode TLV 1010 -> list of track dicts (trackData).

    Each track is 112 bytes: uint32 ``tid`` + 27 float32 = position(3),
    velocity(3), acceleration(3), error covariance[16] (4x4, row-major),
    g, confidence.
    """
    tracks = []
    count = len(payload) // _TARGET_SIZE
    for i in range(count):
        d = struct.unpack_from(_TARGET_STRUCT, payload, i * _TARGET_SIZE)
        tracks.append({
            'tid': d[0],
            'position': list(d[1:4]),
            'velocity': list(d[4:7]),
            'acceleration': list(d[7:10]),
            'error_covariance': list(d[10:26]),
            'g': d[26],
            'confidence': d[27],
        })
    return tracks


def _decode_target_height(payload):
    """Decode TLV 1012 -> list of {'tid', 'max_z', 'min_z'} (heightData)."""
    heights = []
    count = len(payload) // _HEIGHT_SIZE
    for i in range(count):
        tid, max_z, min_z = struct.unpack_from(_HEIGHT_STRUCT, payload, i * _HEIGHT_SIZE)
        heights.append({'tid': tid, 'max_z': max_z, 'min_z': min_z})
    return heights


def _decode_target_index(payload, num_detected_obj):
    """Decode TLV 1011 -> list of track ids, one per detected point.

    Values 253/254/255 (see :data:`TRACK_INDEX_*`) mean the point was not
    associated with any track (weak SNR / out of bounds / noise).
    """
    available = len(payload) // _TARGET_INDEX_SIZE
    count = min(num_detected_obj, available) if num_detected_obj > 0 else available
    return [payload[i] for i in range(count)]


def parse_frame(frame_data):
    """Parse one TI mmWave SDK frame buffer into a dict.

    ``frame_data`` must start at the magic word and contain at least the full
    40-byte header. Returns ``None`` for a buffer that is too short, has a
    malformed header, or claims an absurd number of detected objects; the
    caller treats ``None`` as "not a usable frame".

    The returned dict has the keys:

    ``frame_num``      int   - frame counter from the header
    ``num_points``     int   - header's numDetectedObj
    ``points``         list  - (x, y, z, doppler) tuples (TLV 1 or TLV 1020)
    ``snr``            list  - per-point SNR (TLV 7 in dB, or TLV 1020 linear)
    ``noise``          list  - per-point noise in dB (TLV 7; empty for 3D PT)
    ``range_profile``  list  - uint32 range-bin values (TLV 2)
    ``tracks``         list  - track dicts (TLV 1010 / trackData)
    ``track_heights``  list  - height dicts (TLV 1012 / heightData)
    ``track_indexes``  list  - per-point track id (TLV 1011 / trackIndexes)

    Any list is empty when its TLV is absent from the frame. ``points``/``snr``
    are filled by TLV 1/7 for the Out-of-Box Demo or by TLV 1020 for the 3D
    People Tracking demo, so the detected point cloud has one shape regardless
    of which firmware produced the frame.
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
    tracks, track_heights, track_indexes = [], [], []

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
        elif tlv_type == TLV_TRACK_POINT_CLOUD:
            points, snr = _decode_compressed_point_cloud(payload, num_detected_obj)
        elif tlv_type == TLV_TRACK_TARGET_LIST:
            tracks = _decode_target_list(payload)
        elif tlv_type == TLV_TRACK_TARGET_HEIGHT:
            track_heights = _decode_target_height(payload)
        elif tlv_type == TLV_TRACK_TARGET_INDEX:
            track_indexes = _decode_target_index(payload, num_detected_obj)
        # Any other TLV type is unrecognised and skipped.

    return {
        'frame_num': frame_num,
        'num_points': num_detected_obj,
        'points': points,
        'snr': snr,
        'noise': noise,
        'range_profile': range_profile,
        'tracks': tracks,
        'track_heights': track_heights,
        'track_indexes': track_indexes,
    }
