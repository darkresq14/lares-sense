#!/usr/bin/env python3
"""
Text rendering for a parsed radar frame (``tlv_parser.parse_frame`` output).

Both the Pi reader (``read_radar.py``) and the UDP receiver
(``radar_receiver.py``) need to turn a parsed frame into console text, so the
formatting lives here once. The formatter has **no serial/socket/GUI**
dependency and is unit-testable on a hand-built parsed dict.

It renders whatever a frame actually carries:

- the detected point cloud (cartesian x/y/z, doppler, range, azimuth, SNR), and
- the 3D People Tracking outputs when present — tracks, per-track heights and
  the per-point track index. An Out-of-Box frame simply has none of those, so
  the same formatter serves both demos.

A "3D People Tracking" tag is shown when a frame carries tracks, which is what
the server uses to confirm it received a fully-decoded tracking frame.
"""

import math

# --- TLV 1011 special codes (mirror tlv_parser.TRACK_INDEX_*) ----------------
_TRACK_INDEX_WEAK_SNR = 253
_TRACK_INDEX_BOUNDS = 254
_TRACK_INDEX_NOISE = 255

_INDEX_LABELS = {
    _TRACK_INDEX_WEAK_SNR: 'weak SNR',
    _TRACK_INDEX_BOUNDS: 'out of bounds',
    _TRACK_INDEX_NOISE: 'noise',
}


def _range_azimuth(x, y, z):
    """Return (range_m, azimuth_deg) for a cartesian point."""
    rng = math.sqrt(x * x + y * y + z * z)
    if abs(y) > 0.001:
        azimuth = math.degrees(math.atan2(x, y))
    elif x >= 0:
        azimuth = 90.0
    else:
        azimuth = -90.0
    return rng, azimuth


def format_frame(data):
    """Return a multi-line string rendering of a parsed frame.

    Returns an empty string for ``None`` (no usable frame).
    """
    if data is None:
        return ''

    frame_num = data['frame_num']
    num_points = data['num_points']
    points = data['points']
    snr = data['snr']
    tracks = data.get('tracks', [])
    heights = data.get('track_heights', [])
    indexes = data.get('track_indexes', [])

    is_tracking = bool(tracks or heights)
    tag = '3D People Tracking' if is_tracking else 'point cloud'
    parts = [f"Frame {frame_num:5d} | {num_points} points"
             f" | {len(tracks)} tracks"
             f" | {tag}"]

    # Height lookup by track id, for the per-point / per-track summary.
    height_by_id = {h['tid']: h for h in heights}

    if tracks:
        parts.append('  Tracks:')
        for t in tracks:
            px, py, pz = t['position']
            vx, vy, vz = t['velocity']
            ax, ay, az = t['acceleration']
            h = height_by_id.get(t['tid'])
            height_str = ''
            if h is not None:
                height_str = (f"  height {h['min_z']:.2f}..{h['max_z']:.2f}m"
                              f" ({h['max_z'] - h['min_z']:.2f}m tall)")
            parts.append(
                f"    [id={t['tid']:2d}] pos=({px:6.2f},{py:6.2f},{pz:5.2f}) "
                f"vel=({vx:5.2f},{vy:5.2f},{vz:5.2f}) "
                f"acc=({ax:5.2f},{ay:5.2f},{az:5.2f}) "
                f"conf={t['confidence']:.2f}{height_str}")

    if points:
        parts.append('  Points:')
        num_pts = min(num_points, len(points))
        for i in range(num_pts):
            x, y, z, doppler = points[i]
            s = snr[i] if i < len(snr) else 0.0
            rng, azimuth = _range_azimuth(x, y, z)
            track_str = ''
            if i < len(indexes):
                idx = indexes[i]
                if idx in _INDEX_LABELS:
                    track_str = f"  track={idx} ({_INDEX_LABELS[idx]})"
                else:
                    track_str = f"  track={idx}"
            parts.append(
                f"    [{i:3d}] x={x:7.3f} y={y:7.3f} z={z:7.3f}  "
                f"v={doppler:6.2f} m/s  range={rng:6.3f}m  "
                f"az={azimuth:6.1f}deg  SNR={s:5.1f}{track_str}")

    return '\n'.join(parts)
