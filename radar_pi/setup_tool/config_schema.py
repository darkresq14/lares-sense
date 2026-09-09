#!/usr/bin/env python3
"""
Parameter schema for the 6 m 3D people-tracking cfg (issue #10).

Every parameter of the shipped ``xwr6843_aop_3d_people_track_6m.cfg`` is
described here: tier, title, per-field type / documented range / default
(the shipped cfg values) / format, and info text. The info text is sourced
from the TI docs:

- mmWave SDK user's guide (CLI commands: ``dfeDataOutputMode``, ``channelCfg``,
  ``adcCfg``, ``adcbufCfg``, ``lowPower``, ``profileCfg``, ``chirpCfg``,
  ``frameCfg``)
- 3D People Tracking detection layer tuning guide (CFAR, angle estimation,
  antenna geometry, FOV, compensation)
- 3D People Tracking tracker layer tuning guide (boundary boxes, sensor
  position, gating / allocation / state / max-acceleration, trackingCfg)
- 3D people tracking user guide (physical-setup commands)

Defaults are embedded as literals and cross-checked against the pristine
template by tests (``tests.test_config_model``) — they must stay in lockstep
with the shipped cfg.

This module is pure data: no IO, no parsing logic (see ``config_model.py``).
"""

from dataclasses import dataclass, field as dc_field
from typing import Optional, Tuple, Union

# --- tiers ------------------------------------------------------------------

@dataclass(frozen=True)
class Tier:
    id: str           # machine id, stable in the WS protocol
    label: str        # editor heading
    blurb: str        # one line of guidance under the heading


#: The three editing tiers, easiest first (ADR-0004 Config Stage).
TIERS = (
    Tier('room_mounting', 'Room & mounting',
         'Edit at every install: the room boxes and how/where the sensor '
         'is mounted.'),
    Tier('tracking', 'Tracking',
         'Advanced: tracker behaviour — gating, track lifecycle, allocation, '
         'motion limits.'),
    Tier('rf_expert', 'RF & chirp (expert)',
         'Expert, do not touch unless you know: chirp profile, channels, '
         'CFAR, antenna geometry, calibration.'),
)

#: Tier lookup by id.
TIER_BY_ID = {t.id: t for t in TIERS}

# --- fields & parameters ------------------------------------------------------

@dataclass(frozen=True)
class Field:
    """One positional argument of a cfg command line."""

    key: str                                  # stable machine id (errors, WS)
    label: str                                # short editor label
    type: str                                 # 'int' | 'float'
    fmt: str                                  # output format: 'd','g','.1f',...
    default: Union[int, float]                # shipped cfg value
    minimum: Optional[Union[int, float]]      # documented range
    maximum: Optional[Union[int, float]]
    info: str = ''                            # one-line doc text
    unit: str = ''                            # unit suffix for the editor


@dataclass(frozen=True)
class Param:
    """One cfg command as an editable parameter.

    ``instances`` > 1 means the command appears on that many consecutive
    lines (chirpCfg: one per chirp definition); each instance carries the
    same field layout.
    """

    command: str                              # first token of the cfg line
    tier: str
    title: str
    info: str
    fields: Tuple[Field, ...]
    instances: int = 1
    instance_label: str = ''                  # editor label per instance
    line_suffix: str = ''                     # vendor cfg quirk (trailing ' ')
    #: Per-instance default lines (the shipped cfg), overriding the generic
    #: field defaults when the instances differ (chirpCfg: chirp 0/1/2 with
    #: TX masks 1/2/4). None = every instance uses the field defaults.
    default_instances: Optional[Tuple[Tuple[Union[int, float], ...], ...]] = None


def _f(key, label, type_, fmt, default, minimum, maximum,
       info='', unit=''):
    return Field(key, label, type_, fmt, default, minimum, maximum, info, unit)


def _i(key, label, default, minimum, maximum, info='', unit=''):
    return Field(key, label, 'int', 'd', default, minimum, maximum, info, unit)


# --- helper builders for the repetitive groups -------------------------------

def _box_fields(xmin, xmax, ymin, ymax, zmin, zmax):
    return (
        _f('x_min', 'X min', 'float', 'g', xmin, -50, 50,
           'Minimum horizontal distance to the sensor origin (m).', 'm'),
        _f('x_max', 'X max', 'float', 'g', xmax, -50, 50,
           'Maximum horizontal distance to the sensor origin (m).', 'm'),
        _f('y_min', 'Y min', 'float', 'g', ymin, -50, 50,
           'Minimum depth distance to the sensor origin (m).', 'm'),
        _f('y_max', 'Y max', 'float', 'g', ymax, -50, 50,
           'Maximum depth distance to the sensor origin (m).', 'm'),
        _f('z_min', 'Z min', 'float', 'g', zmin, -50, 50,
           'Minimum height; Z = 0 is the ground plane (m).', 'm'),
        _f('z_max', 'Z max', 'float', 'g', zmax, -50, 50,
           'Maximum height above the ground plane (m).', 'm'),
    )


def _ant_fields(values, doc):
    return tuple(
        _i(f'ant{i}', f'ant {i}', v, -8, 8, doc)
        for i, v in enumerate(values))


#: chirpCfg argument layout, shared by all three instances.
CHIRP_FIELDS = (
    _i('chirp_start_idx', 'chirp start', 0, 0, 511,
       'First chirp index this definition applies to.'),
    _i('chirp_end_idx', 'chirp end', 0, 0, 511,
       'Last chirp index this definition applies to.'),
    _i('profile_id', 'profile', 0, 0, 3,
       'Must match the profileCfg profile id.'),
    _i('start_freq_var', 'start freq var', 0, 0, 0,
       'Start-frequency variation in Hz; only 0 is supported/tested.', 'Hz'),
    _i('freq_slope_var', 'slope var', 0, 0, 0,
       'Frequency-slope variation in kHz/us; only 0 is supported/tested.'),
    _i('idle_time_var', 'idle var', 0, 0, 0,
       'Idle-time variation in us; only 0 is supported/tested.', 'us'),
    _i('adc_start_var', 'adc start var', 0, 0, 0,
       'ADC-start-time variation in us; only 0 is supported/tested.', 'us'),
    _i('tx_mask', 'TX mask', 1, 0, 7,
       'TX antenna enable mask: 1=TX0, 2=TX1, 4=TX2 (one per chirp for '
       'TDM-MIMO).'),
)


# --- the schema, in cfg-file order --------------------------------------------

#: Ordered parameter list. Generation walks this order; the editor regroups
#: by tier.
SCHEMA = (
    # -- SDK / data-path parameters ------------------------------------------
    Param('dfeDataOutputMode', 'rf_expert', 'DFE data output mode',
          'How the DFE streams samples: 1 = frame-based chirps, 2 = '
          'continuous, 3 = advanced frame. This demo uses frame-based.',
          (_i('mode', 'mode', 1, 1, 3,
              '1: frame based chirps, 2: continuous chirping, '
              '3: advanced frame config.'),)),
    Param('channelCfg', 'rf_expert', 'Channels',
          'RX/TX antenna channel enables. Must not change between '
          'sensorStop and sensorStart (reboot to change).',
          (_i('rx_channel_en', 'RX mask', 15, 0, 15,
              'Receive antenna mask, e.g. 15 = 0x1111b = all 4 RX.'),
           _i('tx_channel_en', 'TX mask', 7, 0, 7,
              'Transmit antenna mask, e.g. 7 = all 3 TX.'),
           _i('cascading', 'cascading', 0, 0, 1,
              'Cascading flag: 0 for a standalone device.'))),
    Param('adcCfg', 'rf_expert', 'ADC',
          'ADC resolution and output format. Only 16-bit complex is '
          'supported by this demo.',
          (_i('adc_bits', 'bits', 2, 0, 2,
              '0: 12-bits, 1: 14-bits, 2: 16-bits (only 16 supported).'),
           _i('adc_out_fmt', 'format', 1, 0, 2,
              '0: real, 1: complex 1x, 2: complex 2x (only complex '
              'supported).'))),
    Param('adcbufCfg', 'rf_expert', 'ADC buffer',
          'ADCBUF hardware configuration.',
          (_i('sub_frame_idx', 'subframe', -1, -1, -1,
              'Subframe index; -1 applies to all (legacy mode: -1).'),
           _i('adc_out_fmt', 'out format', 0, 0, 1,
              'ADCBUF output format: 0 complex, 1 real.'),
           _i('sample_swap', 'sample swap', 1, 0, 1,
              'I/Q sample swap: only 1 is supported.'),
           _i('chan_interleave', 'interleave', 1, 0, 1,
              'Channel interleave: 1 = non-interleaved (only supported).'),
           _i('chirp_threshold', 'chirp thresh', 1, 1, 1,
              'Ping/pong chirp threshold; only 1 supported on xwr68xx '
              '(HWA 1D FFT).'))),
    Param('lowPower', 'rf_expert', 'Low power',
          'Low-power ADC mode configuration. Must not change between '
          'sensorStop and sensorStart.',
          (_i('dont_care', "don't care", 0, 0, 0, 'Set to 0.'),
           _i('adc_mode', 'ADC mode', 0, 0, 1,
              '0x00: regular ADC mode, 0x01: low-power ADC (not supported '
              'on xwr6xxx — keep 0).'))),
    Param('profileCfg', 'rf_expert', 'Chirp profile',
          'The FMCW chirp profile: timings, slope, sampling. Together with '
          'chirpCfg and frameCfg it fixes range/velocity resolution and '
          'max range (this shipped profile: ~0.084 m range resolution, '
          '~7.2 m max range, 96 samples, 2.95 Msps).',
          (_i('profile_id', 'profile id', 0, 0, 3,
              'Profile identifier (one profile per config).'),
           _f('start_freq', 'start freq', 'float', '.2f', 60.75, 57, 64,
              'Ramp start frequency in GHz (IWR6843: 57-64 band).', 'GHz'),
           _f('idle_time', 'idle time', 'float', '.2f', 30.00, 0, 5000,
              'Idle time before the ramp starts.', 'us'),
           _f('adc_start_time', 'adc start', 'float', '.2f', 25.00, 0, 1000,
              'ADC valid start time within the ramp.', 'us'),
           _f('ramp_end_time', 'ramp end', 'float', '.2f', 59.10, 0, 5000,
              'Ramp end time.', 'us'),
           _i('tx_out_power', 'TX power', 394758, 0, 1048575,
              'TX output power back-off code (raw register scale as '
              'shipped; leave unchanged unless recalculating the chirp).'),
           _i('tx_phase_shifter', 'TX phase', 0, 0, 8388607,
              'TX phase shifter code (raw; 0 as shipped).'),
           _f('freq_slope', 'slope', 'float', '.2f', 54.71, 0, 240,
              'Frequency slope of the ramp in MHz/us.', 'MHz/us'),
           _f('tx_start_time', 'TX start', 'float', 'g', 1, 0, 10,
              'TX start time relative to ramp start.', 'us'),
           _i('num_adc_samples', 'ADC samples', 96, 16, 1024,
              'Number of ADC samples per chirp.'),
           _f('dig_out_sample_rate', 'sample rate', 'float', '.2f',
              2950.00, 100, 12500, 'ADC sampling frequency in ksps.', 'ksps'),
           _i('hpf_corner_freq1', 'HPF1', 2, 0, 3,
              'HPF1 corner: 0=175kHz, 1=235kHz, 2=350kHz, 3=700kHz.'),
           _i('hpf_corner_freq2', 'HPF2', 1, 0, 3,
              'HPF2 corner: 0=350kHz, 1=700kHz, 2=1.4MHz, 3=2.8MHz.'),
           _i('rx_gain', 'RX gain', 36, 0, 63,
              'OR\'ed RX gain (dB) and RF gain target code.')),
          line_suffix=' '),
    Param('chirpCfg', 'rf_expert', 'Chirp definitions',
          'Per-chirp variations on the profile. This demo ships a TDM-MIMO '
          'set: chirps 0-2 each enable exactly one TX antenna (TX0/TX1/TX2 '
          'masks 1/2/4); all variation fields are 0.',
          CHIRP_FIELDS,
          instances=3, instance_label='chirp set',
          default_instances=((0, 0, 0, 0, 0, 0, 0, 1),
                             (1, 1, 0, 0, 0, 0, 0, 2),
                             (2, 2, 0, 0, 0, 0, 0, 4))),
    Param('frameCfg', 'rf_expert', 'Frame timing',
          'Frame structure: which chirps, how many loops per frame, frame '
          'period. Shipped: 288 chirps (3 TX x 96 loops) every 55 ms '
          '(~18 fps).',
          (_i('chirp_start_idx', 'chirp start', 0, 0, 511,
              'First chirp of the frame.'),
           _i('chirp_end_idx', 'chirp end', 2, 0, 511,
              'Last chirp of the frame.'),
           _i('num_loops', 'loops', 96, 1, 255,
              'Loops over the chirp set per frame (multiple of 4 on '
              'xwr68xx).'),
           _i('num_frames', 'frames', 0, 0, 65535,
              'Frames to send; 0 = infinite.'),
           _f('frame_periodicity', 'frame period', 'float', '.2f', 55.00,
              5, 1000, 'Frame period in ms.', 'ms'),
           _i('trigger_select', 'trigger', 1, 1, 2,
              '1: software trigger (only supported), 2: hardware.'),
           _f('frame_trig_delay', 'trig delay', 'float', 'g', 0, 0, 10,
              'Frame trigger delay in ms.', 'ms'))),
    # -- detection layer -------------------------------------------------------
    Param('dynamicRACfarCfg', 'rf_expert', 'Dynamic CFAR',
          '2-pass CFAR (range then angle) on the dynamic-scene '
          'range-angle heatmap. Lower thresholds = more (noisier) points; '
          'window sizes shape the local noise estimate.',
          (_i('sub_frame_idx', 'subframe', -1, -1, -1,
              'Always -1 in this demo.'),
           _i('discard_left_range', 'discard L (range)', 4, 0, 255,
              'Range bins discarded on the near side.'),
           _i('discard_right_range', 'discard R (range)', 4, 0, 255,
              'Range bins discarded on the far side.'),
           _i('discard_left_angle', 'discard L (angle)', 2, 0, 255,
              'Angle bins discarded on the left.'),
           _i('discard_right_angle', 'discard R (angle)', 2, 0, 255,
              'Angle bins discarded on the right.'),
           _i('ref_win_size_range', 'ref win (range)', 8, 0, 255,
              'Range-CFAR reference window size (samples).'),
           _i('ref_win_size_angle', 'ref win (angle)', 12, 0, 255,
              'Angle-CFAR reference window size (samples).'),
           _i('guard_win_size_range', 'guard win (range)', 4, 0, 255,
              'Range-CFAR guard window size (samples).'),
           _i('guard_win_size_angle', 'guard win (angle)', 12, 0, 255,
              'Angle-CFAR guard window size (samples).'),
           _f('range_thre', 'range thresh', 'float', '.2f', 5.00, 0, 100,
              'Range-CFAR threshold factor (linear scale).'),
           _f('angle_thre', 'angle thresh', 'float', '.2f', 8.00, 0, 100,
              'Angle-CFAR threshold factor (linear scale).'),
           _f('sidelobe_thre', 'sidelobe thresh', 'float', '.2f', 0.40,
              0, 100, 'Sidelobe threshold to declare a local peak valid.'),
           _i('enable_2nd_pass', '2nd pass', 1, 0, 1,
              '1: second-pass angle CFAR (suggested).'),
           _i('dynamic_flag', 'dynamic flag', 1, 0, 3,
              '0/1: static thresholds, 2: range-dynamic, 3: range- and '
              'angle-dynamic CFAR.'))),
    Param('staticRACfarCfg', 'rf_expert', 'Static CFAR',
          'Same 2-pass CFAR structure, applied to the static-scene heatmap '
          '(people standing still).',
          (_i('sub_frame_idx', 'subframe', -1, -1, -1,
              'Always -1 in this demo.'),
           _i('discard_left_range', 'discard L (range)', 6, 0, 255,
              'Range bins discarded on the near side.'),
           _i('discard_right_range', 'discard R (range)', 2, 0, 255,
              'Range bins discarded on the far side.'),
           _i('discard_left_angle', 'discard L (angle)', 2, 0, 255,
              'Angle bins discarded on the left.'),
           _i('discard_right_angle', 'discard R (angle)', 2, 0, 255,
              'Angle bins discarded on the right.'),
           _i('ref_win_size_range', 'ref win (range)', 8, 0, 255,
              'Range-CFAR reference window size (samples).'),
           _i('ref_win_size_angle', 'ref win (angle)', 8, 0, 255,
              'Angle-CFAR reference window size (samples).'),
           _i('guard_win_size_range', 'guard win (range)', 6, 0, 255,
              'Range-CFAR guard window size (samples).'),
           _i('guard_win_size_angle', 'guard win (angle)', 4, 0, 255,
              'Angle-CFAR guard window size (samples).'),
           _f('range_thre', 'range thresh', 'float', '.2f', 8.00, 0, 100,
              'Range-CFAR threshold factor (linear scale).'),
           _f('angle_thre', 'angle thresh', 'float', '.2f', 15.00, 0, 100,
              'Angle-CFAR threshold factor (linear scale).'),
           _f('sidelobe_thre', 'sidelobe thresh', 'float', '.2f', 0.30,
              0, 100, 'Sidelobe threshold to declare a local peak valid.'),
           _i('enable_2nd_pass', '2nd pass', 0, 0, 1,
              '1: second-pass angle CFAR.'),
           _i('dynamic_flag', 'dynamic flag', 0, 0, 3,
              '0/1: static thresholds, 2: range-dynamic, 3: range- and '
              'angle-dynamic CFAR.'))),
    Param('dynamicRangeAngleCfg', 'rf_expert', 'Range-angle heatmap',
          'Dynamic-scene range-angle heatmap estimation (Capon '
          'beamforming). Method 1 = range-azimuth detection then elevation '
          'per detection — the 3D wall-mount chain this cfg ships.',
          (_i('sub_frame_idx', 'subframe', -1, -1, -1,
              'Always -1 in this demo.'),
           _f('angle_search_step', 'angle step', 'float', '.2f', 0.75,
              0.5, 10,
              'Angle search granularity in degrees (>= 0.75 wall-mount; '
              'memory-limited).', 'deg'),
           _f('diag_loading', 'diag loading', 'float', '.4f', 0.0010, 0,
              1, 'Covariance diagonal loading; TI strongly suggests not '
              'changing 0.001.'),
           _i('est_method', 'est method', 1, 1, 2,
              '1: range-azimuth + elevation (wall mount), 2: '
              'range-azimuth-elevation with zoom-in (ceil mount).'),
           _i('doppler_est_method', 'doppler method', 0, 0, 0,
              '0: single-peak Doppler search (only method supported).'))),
    Param('dynamic2DAngleCfg', 'rf_expert', 'Elevation estimation',
          'Elevation-angle estimation per detected range-azimuth point '
          '(field meanings as shipped for estimation method 1).',
          (_i('sub_frame_idx', 'subframe', -1, -1, -1,
              'Always -1 in this demo.'),
           _f('elev_search_step', 'elev step', 'float', '.1f', 3.0, 1, 10,
              'Elevation search granularity in degrees (>= 1, '
              'memory-limited).', 'deg'),
           _f('diag_loading', 'diag loading', 'float', '.4f', 0.0300, 0, 1,
              'Angle covariance diagonal loading; keep 0.03.'),
           _i('max_n_peak', 'max peaks', 1, 1, 1,
              'Peaks to search in elevation; only 1 supported.'),
           _i('peak_exp_samples', 'peak exp samples', 0, 0, 0,
              'Peak expansion samples; only 0 supported (method 1).'),
           _i('elev_only', 'elev only', 1, 1, 1,
              '1: elevation estimation only (only method supported).'),
           _f('sidelobe_thre', 'sidelobe thresh', 'float', '.2f', 0.30,
              0, 1, 'Local-peak sidelobe threshold (reserved for future '
              'multi-peak search).'),
           _f('peak_exp_rel_thre', 'peak exp rel', 'float', '.2f', 0.85,
              0, 1, 'Peak-expansion relative threshold (reserved).'),
           _f('peak_exp_snr_thre', 'peak exp SNR', 'float', '.2f', 8.00,
              0, 100, 'Peak-expansion SNR threshold (reserved).'))),
    Param('staticRangeAngleCfg', 'rf_expert', 'Static heatmap',
          'Static-scene heatmap estimation (Bartlett beamforming), as a '
          'decimation of the dynamic-scene angle grid.',
          (_i('sub_frame_idx', 'subframe', -1, -1, -1,
              'Always -1 in this demo.'),
           _i('static_proc_enabled', 'static proc', 0, 0, 1,
              '1: static scene processing enabled (0 as shipped — static '
              'points come from the dynamic chain).'),
           _i('azim_step_deci', 'azim decim', 8, 1, 16,
              'Azimuth decimation factor x dynamic azimuth step.'),
           _i('elev_step_deci', 'elev decim', 8, 1, 16,
              'Elevation decimation factor x dynamic elevation step.'))),
    Param('antGeometry0', 'rf_expert', 'Antenna geometry (azimuth)',
          'Virtual antennas\' physical location index in the azimuth domain '
          '(AOP pattern; do not touch unless the antenna layout changes).',
          _ant_fields((-1, -1, 0, 0, -3, -3, -2, -2, -1, -1, 0, 0),
                      'Azimuth location index of virtual antenna.')),
    Param('antGeometry1', 'rf_expert', 'Antenna geometry (elevation)',
          'Virtual antennas\' physical location index in the elevation '
          'domain (AOP pattern; do not touch unless the antenna layout '
          'changes).',
          _ant_fields((-1, 0, -1, 0, -3, -2, -3, -2, -3, -2, -3, -2),
                      'Elevation location index of virtual antenna.')),
    Param('antPhaseRot', 'rf_expert', 'Antenna phase rotation',
          'Phase rotation per virtual antenna from the board design: 1 = '
          'no rotation, -1 = rotated (AOP pattern as shipped).',
          _ant_fields((1, -1, 1, -1, 1, -1, 1, -1, 1, -1, 1, -1),
                      'Phase rotation of virtual antenna (+1/-1).')),
    Param('fovCfg', 'rf_expert', 'Field of view',
          'Sensor azimuth/elevation FOV. The value configures the total '
          'extent on ONE side, i.e. 70 means [-70, +70] degrees.',
          (_i('sub_frame_idx', 'subframe', -1, -1, -1,
              'Always -1 in this demo.'),
           _f('azimuth_fov', 'azimuth FOV', 'float', '.1f', 70.0, 10, 90,
              'Azimuth FOV extent per side.', 'deg'),
           _f('elevation_fov', 'elevation FOV', 'float', '.1f', 70.0, 10,
              90, 'Elevation FOV extent per side.', 'deg'))),
    Param('compRangeBiasAndRxChanPhase', 'rf_expert',
          'Range bias & RX phase compensation',
          'Range bias and per-virtual-antenna phase compensation from the '
          'mmWave SDK out-of-box calibration procedure. Values as shipped '
          '(bias 0, unity phases).',
          (_i('range_bias', 'range bias', 0, 0, 0,
              'Range bias for the array (meters x 1000?); always 0 in this '
              'chain.'),
           ) + tuple(
               _i(f'phase_{i}', f'phase {i}', v, -1, 1,
                  f'Compensation factor element {i} (real/imaginary '
                  'interleaved unity vector as shipped).')
               for i, v in enumerate((1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0,
                                      1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0)))),
    # -- tracker layer ---------------------------------------------------------
    Param('staticBoundaryBox', 'room_mounting', 'Static boundary box',
          'Zone where tracks may become static (people standing still). '
          'Keep it smaller than the boundary box so the tracker can tell '
          '"went static" from "left the room"; TI recommends min Y >= 2.',
          _box_fields(-3, 3, 0.5, 7.5, 0, 3)),
    Param('boundaryBox', 'room_mounting', 'Boundary box',
          'The room: only points inside this box are tracked. Points '
          'outside are discarded — this is how the hallway beyond the wall '
          'or ghost targets behind walls get ignored.',
          _box_fields(-4, 4, 0, 8, 0, 3)),
    Param('sensorPosition', 'room_mounting', 'Sensor position & tilt',
          'Where and how the sensor is mounted: height above the floor plus '
          'azimuth and elevation tilt. Get this right and world '
          'coordinates fall out correctly.',
          (_f('z', 'height', 'float', 'g', 2, 0, 20,
              'Height of the sensor above the ground plane (Z = 0).', 'm'),
           _f('azim_tilt', 'azimuth tilt', 'float', 'g', 0, -90, 90,
              'Horizontal rotation about the vertical axis; positive '
              'rotates from +X toward +Y.', 'deg'),
           _f('elev_tilt', 'elevation tilt', 'float', 'g', 15, -90, 90,
              'Vertical rotation; positive tilts down toward the ground '
              '(wall mount: ~15).', 'deg'))),
    Param('gatingParam', 'tracking', 'Gating',
          'Association gate size around each track prediction: how far a '
          'point may sit from a track (per axis and velocity) and still '
          'belong to it. Too small splits one person into tracks; too '
          'large merges people.',
          (_f('gain', 'gain', 'float', 'g', 3, 0.5, 10,
              'Gating function gain (limits scale by this; "three sigma" '
              'value 3 is standard).'),
           _f('limit_width', 'width', 'float', 'g', 2, 0.1, 10,
              'Gating limit in width.', 'm'),
           _f('limit_depth', 'depth', 'float', 'g', 2, 0.1, 10,
              'Gating limit in depth.', 'm'),
           _f('limit_height', 'height', 'float', 'g', 3, 0.1, 10,
              'Gating limit in height.', 'm'),
           _f('limit_velocity', 'velocity', 'float', 'g', 4, 0.1, 20,
              'Gating limit in radial velocity.', 'm/s'))),
    Param('stateParam', 'tracking', 'Track lifecycle',
          'Track state machine thresholds: how many consecutive hits to '
          'confirm a track and how many misses to free it in each '
          'condition (static inside the static box, exiting outside it).',
          (_i('det2act_thre', 'det to active', 3, 1, 100,
              'Continuous HIT frames to promote DETECT to ACTIVE.'),
           _i('det2free_thre', 'det to free', 3, 1, 100,
              'Continuous MISS frames to drop an unconfirmed track.'),
           _i('active2free_thre', 'active to free', 12, 1, 100,
              'Misses to free an ACTIVE track (normal condition).'),
           _i('static2free_thre', 'static to free', 500, 1, 10000,
              'Misses to free a STATIC track inside the static zone '
              '(extends a seated person\'s life).'),
           _i('exit2free_thre', 'exit to free', 5, 1, 100,
              'Misses to free an ACTIVE track outside the static zone '
              '(quick delete after leaving).'),
           _i('sleep2free_thre', 'sleep to free', 6000, 1, 100000,
              'Maximum total lifespan of a static track (frames).'))),
    Param('allocationParam', 'tracking', 'Track allocation',
          'When unassociated points deserve a new track: minimum SNR, '
          'velocity, point count and cluster compactness for an allocation '
          'set.',
          (_f('snr_thre', 'SNR thresh', 'float', 'g', 20, 0, 1000,
              'Minimum total (linear) SNR of the allocation set.'),
           _f('snr_thre_obscured', 'SNR (obscured)', 'float', 'g', 100, 0,
              1000, 'Minimum total SNR when the set is obscured by '
              'another target.'),
           _f('velocity_thre', 'velocity thresh', 'float', 'g', 0.1, 0, 5,
              'Minimum radial velocity of the set centroid (ignores '
              'spectral leakage around static reflectors).', 'm/s'),
           _f('points_thre', 'points thresh', 'float', 'g', 20, 1, 200,
              'Minimum number of points in the allocation set.'),
           _f('max_distance_thre', 'max distance', 'float', 'g', 0.5,
              0.01, 10, 'Max squared geometric distance of a candidate to '
              'the set centroid.', 'm^2'),
           _f('max_vel_thre', 'max velocity diff', 'float', 'g', 20, 0.1,
              50, 'Max radial-velocity difference of a candidate to the '
              'centroid.', 'm/s'))),
    Param('maxAcceleration', 'tracking', 'Max acceleration',
          'How much the target acceleration may change per axis between '
          'frames — how much you trust the constant-acceleration motion '
          'model. Lower = smoother tracks, higher = snappier response.',
          (_f('max_accel_x', 'X', 'float', 'g', 0.1, 0.01, 10,
              'Max acceleration change, X direction.', 'm/s^2'),
           _f('max_accel_y', 'Y', 'float', 'g', 0.1, 0.01, 10,
              'Max acceleration change, Y direction.', 'm/s^2'),
           _f('max_accel_z', 'Z', 'float', 'g', 0.1, 0.01, 10,
              'Max acceleration change, Z direction.', 'm/s^2'))),
    Param('trackingCfg', 'tracking', 'Tracker enable & sizing',
          'Enables the group tracker and sizes it: point/track capacity and '
          'velocity ladder must cover what the chirp actually produces '
          '(max 1000 points / 200 tracks; deltaT must match the frame '
          'period).',
          (_i('enable', 'enable', 1, 0, 1,
              '1: enable the group tracker.'),
           _i('initial_params', 'initial config', 2, 0, 10,
              'Index into the internal default parameter set (overridden '
              'by the other tracker commands).'),
           _i('max_num_points', 'max points', 800, 1, 1000,
              'Maximum detection points per frame (tracker memory).'),
           _i('max_num_tracks', 'max tracks', 30, 1, 200,
              'Maximum simultaneous tracks (tracker memory).'),
           _i('max_radial_velocity', 'max velocity', 46, 0, 1000,
              'Maximum radial velocity in 10*cm/s units (46 = 4.6 m/s).'),
           _i('radial_vel_resolution', 'vel resolution', 96, 1, 1000,
              'Radial velocity resolution in mm/s the chirp provides.'),
           _i('delta_t', 'frame time', 55, 10, 200,
              'Frame time in ms — must match frameCfg periodicity.', 'ms'))),
    Param('presenceBoundaryBox', 'room_mounting', 'Presence boundary box',
          'Early-presence zone: points with walking-like velocity in this '
          'box report presence before tracks are allocated (e.g. smart '
          'lighting). Does not need to be smaller than the boundary box.',
          _box_fields(-3, 3, 0.5, 7.5, 0, 3)),
)

#: Command lookup.
PARAM_BY_COMMAND = {p.command: p for p in SCHEMA}

# --- cfg-file layout constants (generation) ----------------------------------

#: Commands the sensor control flow emits but that are not editable
#: parameters.
CONTROL_COMMANDS = ('sensorStop', 'flushCfg', 'sensorStart')

#: Section header comments, byte-faithful to the vendor template. The key is
#: the command a section starts with; a blank line precedes every section
#: after the first. (Note the vendor quirk: the tracker-guide comment line
#: carries a trailing space.)
SECTION_HEADERS = {
    'dfeDataOutputMode': (
        '% SDK Parameters',
        "% See the SDK user's guide for more information",
        '% "C:\\ti\\mmwave_sdk_[VER]\\docs\\mmwave_sdk_user_guide.pdf"',
    ),
    'profileCfg': (
        '% Detection Layer Parameters',
        '% See the Detection Layer Tuning Guide for more information',
        '% "<RADAR_TOOLBOX_INSTALL_DIR>\\source\\ti\\examples\\People_Tracking'
        '\\docs\\IWR6843_People_Tracking_PDFs'
        '\\3D_people_tracking_detection_layer_tuning_guide.pdf"',
    ),
    'staticBoundaryBox': (
        '% Tracker Layer Parameters',
        '% See the Tracking Layer Tuning Guide for more information',
        '% "C:\\ti\\radar_toolbox_[VER]\\source\\ti\\examples\\People_Tracking'
        '\\docs\\IWR6843_People_Tracking_PDFs'
        '\\3D_people_tracking_tracker_layer_tuning_guide.pdf" ',
    ),
}


def defaults():
    """The shipped cfg values as a structured values dict.

    Shape: ``{command: [[field values...], ... one list per instance]}``.
    """
    values = {}
    for param in SCHEMA:
        if param.default_instances is not None:
            values[param.command] = [list(line)
                                     for line in param.default_instances]
        else:
            values[param.command] = [
                [field.default for field in param.fields]
                for _ in range(param.instances)
            ]
    return values
