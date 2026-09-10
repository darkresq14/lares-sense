# lares-sense

mmWave radar people-tracking: IWR6843AoP sensor, Pi-hosted reader, server-side
storage. This glossary fixes the language the code, docs, and issues use.

## Language

**Setup Tool**:
The Pi-served web page used at install time to configure the sensor and verify
what it sees before normal operation starts.
_Avoid_: visualizer, dashboard, demo app

**Config Stage**:
The Setup Tool's editing state: all sensor parameters are editable, live frames
may be viewed but nothing is recorded on the server. Entered when no Working
Config exists, and on every deliberate stop.
_Avoid_: setup mode, admin mode

**Running Stage**:
The Setup Tool's operating state: parameters are read-only, the sensor streams,
and frames are sent to the server for persistence. Entered on Start, and
automatically on boot when a Working Config exists.
_Avoid_: production mode, live mode

**Working Config**:
The current full set of sensor parameters held by the Setup Tool — the single
source of truth for what the sensor was last told to do. Persisted as a cfg
file on the Pi across sessions; the repo keeps only the pristine vendor
template.
_Avoid_: profile, preset, template

**Replay Visualizer** (deferred):
A future server-side page that scrubs through recorded sessions from the
database. Distinct from the Setup Tool; do not merge them.
_Avoid_: playback tool

**Aim Assist**:
The Config Stage wizard that finds the corner bisector from two wall
captures and then guides the installer to aim the sensor along it. The
installer holds and rotates; every angle number comes from the sensor.
_Avoid_: alignment tool, compass wizard

**Wall Capture**:
One averaged compass heading taken with the case bottom pressed flush
against a wall, recording that wall's inward normal. Flushness is the only
contact requirement; rotation of the unit is free.
_Avoid_: calibration, wall scan

**Measured Tilt**:
Sensor-derived orientation of the mounted unit, offered to the sensor
position fields via a one-click adopt rather than continuous auto-sync.
_Avoid_: auto-tilt, live tilt sync
