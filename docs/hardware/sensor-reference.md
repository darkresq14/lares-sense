# IWR6843AoP Sensor Reference

Everything learned while setting up and operating the uRAD Industrial IWR6843AoP mmWave radar with a Raspberry Pi.

---

## The Sensor

**Model**: uRAD Industrial IWR6843AoP (by Anteral, based on TI IWR6843AoP chip)

**Key specs**:
- Frequency: 60-64 GHz
- 3 TX, 4 RX antennas (Antenna on Package - emits perpendicular to the chip top face)
- Output power: 15 dBm
- Field of view: 160 deg azimuth, 160 deg elevation
- Range: up to 70m (car), 40m (people)
- Range resolution: ~5 cm (configurable)
- Velocity range: +/-45 m/s
- Velocity resolution: ~0.06 m/s
- Angle accuracy: ~1 deg
- Azimuth/elevation resolution: ~30 deg
- Power: 5V, ~340mA
- Operating temp: -20 to +55 C

**Coordinate system** (default firmware orientation):
- Origin at center of chip
- X positive: right (from observer behind the radar)
- Y positive: forward (away from observer)
- Z positive: up
- Emitted signal goes through the top face of the chip (the side with the metal shield)

**LED indicators**:
- Red LED: connected to 5V line (power indicator)
- Green LED: connected to UART TX data line (data ready indicator)
- Both should be ON in functional mode

**DIP switch** (SOP2 line):
- OFF (low): Functional mode (runs firmware from flash)
- ON (high): Flashing mode (waits for firmware upload via UART)

See [`flashing.md`](flashing.md) for the full step-by-step procedure (prep,
UniFlash GUI, headless re-flash, bench verify, troubleshooting).

**10-pin header pinout**:
| Pin | Signal | Chip ball | Notes |
|-----|--------|-----------|-------|
| 1 | GND | - | Ground plane |
| 2 | 5V | - | Power input (4.5-5.5V) |
| 3 | UART TX config | MSS_UARTA_TX (U16) | Config responses FROM sensor |
| 4 | UART RX config | MSS_UARTA_RX (V16) | Config commands TO sensor |
| 5 | UART TX data | MSS_UARTB_TX (E2) | Point cloud data FROM sensor |
| 6 | UART RX data | MSS_UARTA_RX (U12) | Data commands TO sensor (unused in OOB) |
| 7 | RESET | - | Rising edge triggers reset, has pullup |
| 8 | ON/OFF | - | HIGH=ON, LOW=OFF (<1mA in OFF), has pullup |
| 9 | GPIO1 | L3 | User-programmable, unused by default firmware |
| 10 | GPIO0 | M2 | User-programmable, unused by default firmware |

**Warning**: Pins 3 and 5 are TX from the chip side, so they are RX from the host perspective. Pins 4 and 6 are RX from the chip side, so TX from the host.

### Known firmware behavior — the sensorStop wedge (measured 2026-09-12)

After `sensorStop` is issued **while streaming**, this sensor never starts
again — `sensorStart`, a full `flushCfg` + cfg reconfig, even 16 s of
patience all stay silent until a hardware reset (power cycle or the RST
line — pin 7, wired to GPIO5 by the uRAD adapter). `sensorStop` while
already stopped is harmless. Consequences:

- The Setup Tool pulses the RST line (GPIO5) instead of ever sending
  `sensorStop` — Start/Apply/Stop are reset-based
  (`setup_tool/sensor_reset.py`; ADR-0004 amendment of 2026-09-12).
- Manual poking over serial: after a `sensorStop`, pulse RST
  (GPIO5 LOW ≥ 0.2 s → HIGH) before reconfiguring, e.g. gpiozero
  `OutputDevice(5)` off/on.
- A Pi reboot also heals it (the sensor power-cycles with the Pi).

---

## USB vs GPIO Connection

The sensor has a micro-USB port with a Silicon Labs **CP2105 dual USB-to-UART bridge**. When connected via USB:
- Creates TWO virtual COM ports (Enhanced COM = config, Standard COM = data)
- Full bidirectional communication on both UARTs
- This is the "official" way to use the sensor with a PC

The uRAD IWR-AWR Raspberry Pi adapter connects via the GPIO header instead:
- **Single UART only**: Pi TX (pin 8) -> sensor CFG_RX, sensor DATA_TX -> Pi RX (pin 10)
- Config ACKs go out on UARTA_TX (pin 3 of header) which is NOT connected to the Pi
- You send commands blindly (no ACK feedback) and only receive the data stream
- Baud rate must be switched: 115200 for config, 921600 for data

---

## Firmware

**Default firmware**: Out of Box Demo from TI MMWAVE-SDK 3.6 (HWA version - uses Hardware FFT accelerator)

**Binary location** (in the radar toolbox):
```
radar_toolbox_4_00_00_05/source/ti/examples/Out_Of_Box_Demo/prebuilt_binaries/out_of_box_6843_aop.bin
```

**Flashing**:
- Use TI UniFlash tool (desktop or CLI version)
- Requires SOP2=HIGH (DIP switch ON = flashing mode)
- Flash via the config UART port (Enhanced COM / UARTA)
- After flashing, set SOP2=LOW and reset

See [`flashing.md`](flashing.md) for the full step-by-step procedure (prep,
UniFlash GUI, headless re-flash, bench verify, troubleshooting).

**Firmware verification**: Send config + sensorStart over UART. If the sensor has the correct firmware, it will start streaming binary frames. No response = wrong/missing firmware or UART issue.

**TI ecosystem for firmware**:
- mmWave SDK 3.6 (C-based, runs ON the radar chip)
- Radar Toolbox (host-side tools, visualizers, examples)
- Code Composer Studio (IDE for custom firmware development)
- UniFlash (firmware upload tool)

---

## UART Data Protocol

The Out of Box Demo outputs binary frames over the data UART. This is NOT a proprietary uRAD protocol - it's the standard TI mmWave SDK UART output format.

### Frame structure

```
Frame = Header (40 bytes) + TLVs + Padding (32-byte alignment)
```

### Frame header (40 bytes)

| Field | Type | Bytes | Description |
|-------|------|-------|-------------|
| Magic Word | uint16 x4 | 8 | Sync word: `02 01 04 03 06 05 08 07` |
| Version | uint32 | 4 | SDK version (Major*2^24 + Minor*2^16 + ...) |
| Total Packet Length | uint32 | 4 | Length including header |
| Platform | uint32 | 4 | Device type code |
| Frame Number | uint32 | 4 | Increments, resets on power cycle |
| Time CPU Cycles | uint32 | 4 | Timestamp in CPU cycles |
| Num Detected Obj | uint32 | 4 | Number of detected points |
| Num TLVs | uint32 | 4 | Number of TLV items in this frame |
| Subframe Number | uint32 | 4 | 0 if advanced subframe not enabled |

### TLV structure

Each TLV has an 8-byte header:
| Field | Type | Bytes |
|-------|------|-------|
| Type | uint32 | 4 |
| Length | uint32 | 4 |

### Important TLV types

Out-of-Box Demo TLVs:

| Type ID | Name | Payload per point | Fields |
|---------|------|-------------------|--------|
| 1 | Detected Points | 16 bytes (4 x float32) | X(m), Y(m), Z(m), Doppler(m/s) |
| 2 | Range Profile | 4 bytes per bin (uint32) | One value per range bin |
| 7 | Detected Points Side Info | 4 bytes (2 x uint16) | SNR(0.1dB units), Noise(0.1dB units) |

3D People Tracking demo TLVs (Radar Toolbox `3D_people_track_6843`):

| Type ID | Name | Layout | Fields |
|---------|------|--------|--------|
| 1020 | Compressed Point Cloud | 20-byte unit block (5 x float32) + 8 bytes/point (`2bh2H`) | elevation, azimuth, doppler, range, snr (raw ints, scaled by the units) → decoded to cartesian X/Y/Z + doppler + SNR |
| 1010 | Target List (trackData) | 112 bytes/track (`I27f`) | tid + posX/Y/Z + velX/Y/Z + accX/Y/Z + error covariance[16] + g + confidence |
| 1012 | Target Height (heightData) | 12 bytes/track (`I2f`) | tid + max_z + min_z |
| 1011 | Target Index (trackIndexes) | 1 byte/point (`B`) | track id per detected point; 253=weak SNR, 254=out of bounds, 255=noise |

Type 1 is sent when `guiMonitor` has detected objects bit set and there are detections.
Type 7 is sent when `guiMonitor` has detected objects bit = 2.

### Typical frame size

With 7-8 detected points: ~800-864 bytes per frame. At 10 frames/second (100ms frame period), that's ~8 KB/s bandwidth.

---

## CLI Configuration Commands

The sensor is configured by sending text commands over the config UART (115200 baud, newline terminated). Each command returns an ACK (on a separate UART line that the Pi adapter doesn't connect to).

**Command sequence** (always):
```
sensorStop          <- stop any running sensor
flushCfg            <- clear old configuration
<... config commands ...>
sensorStart         <- start measuring
```

**Key commands in the config file**:

| Command | Purpose |
|---------|---------|
| `dfeDataOutputMode 1` | Object detection mode |
| `channelCfg 15 5 0` | All 4 RX, 3 TX antennas |
| `adcCfg 2 1` | ADC settings (16-bit, 800 MHz) |
| `profileCfg` | RF chirp profile (start freq, slope, ADC samples, etc.) |
| `chirpCfg` | Maps profiles to chirps and TX antennas |
| `frameCfg` | Frame structure (chirps per frame, frame period, num frames) |
| `guiMonitor` | Which TLV types to include in output |
| `cfarCfg` | Detection thresholds |
| `aoaFovCfg` | Angle of arrival field of view |

**The guiMonitor line** controls what data you receive:
```
guiMonitor -1 1 1 0 0 0 1
               ^ ^         Detected points (TLV 1) and side info (TLV 7)
```

---

## Raspberry Pi 3B UART Issue (Critical Finding)

This was the biggest debugging challenge. The Pi 3B has **two UARTs**:
- **PL011** (ttyAMA0): Full hardware UART, reliable, supports any baud rate
- **Mini UART** (ttyS0): Auxiliary UART, clock tied to VPU core frequency, less reliable

By default on Pi 3B:
- PL011 is assigned to **Bluetooth**
- Mini UART is assigned to **GPIO pins 14/15** (the only accessible serial port)
- `/dev/serial0` -> `ttyS0` (mini UART)

**The mini UART did not work at all** for radar communication (0 bytes received/sent). The fix:

```bash
echo 'dtoverlay=disable-bt' | sudo tee -a /boot/firmware/config.txt
sudo reboot
```

After this:
- PL011 is freed from Bluetooth and assigned to GPIO pins 14/15
- `/dev/serial0` -> `ttyAMA0` (PL011)
- GPIO 14/15 pin mux changes from ALT5 (mini UART) to ALT0 (PL011)

**Verification**:
```bash
ls -la /dev/serial0          # should show -> ttyAMA0
pinctrl get | grep '14:\|15:' # should show a0 (not a5)
```

**Other Pi serial requirements**:
- `enable_uart=1` in `/boot/firmware/config.txt`
- No `console=serial0` in `/boot/firmware/cmdline.txt`
- User must be in `dialout` group
- Login shell over serial must be disabled (raspi-config)

---

## The TI Ecosystem (No Python SDK)

Important: **TI does not provide a pip-installable Python library** for parsing mmWave UART data. The ecosystem is:

| Component | Language | Purpose |
|-----------|----------|---------|
| mmWave SDK 3.6 | C | Runs on the radar chip itself |
| Radar Toolbox | Python (PyQt GUI) | Host-side visualizer, includes parser code but tightly coupled to GUI |
| mmWave Demo Visualizer | JavaScript (browser) | Web-based real-time visualizer (v3.6 compatible, v4.2 is NOT) |
| UniFlash | Java/CLI | Firmware upload tool |
| Code Composer Studio | C/IDE | Custom firmware development |

The parser functions in the Radar Toolbox (`parseTLVs.py`, `parseFrame.py`) contain the same logic we use, but they depend on numpy and the GUI framework. Everyone in the community writes their own parser - it's simple enough (magic word sync, header unpack, TLV iteration).

---

## UDP Streaming

The raw TI binary frames can be forwarded over UDP as-is. Each datagram = one complete TI frame (starts with magic word). This preserves the standard frame format so any TI-compatible parser can decode it.

**Sender** (Pi): reads raw frames from UART, sends via `sock.sendto(raw_frame, (server_ip, port))`
**Receiver** (server): buffers incoming UDP data, finds frames by magic word, parses and displays

**Firewall lesson**: ICMP (ping) passes through most firewalls but UDP often doesn't. On a server with `iptables INPUT policy DROP` and UFW managing rules, you must explicitly allow the UDP port:
```bash
sudo ufw allow 5005/udp
```

---

## ESP32 Alternative

The ESP32 is a potentially better host than the Pi for this sensor:
- **Dual UART**: full bidirectional config + data simultaneously (no single-UART hack)
- **3.3V logic**: direct compatibility, no level shifters
- **WiFi built-in**: UDP streaming without extra hardware
- **UART supports 5 Mbps**: 921600 baud data stream has plenty of headroom
- **Much cheaper**: ~5 EUR vs ~35 EUR
- **Remote flashing possible**: with one added wire from SOP2 pad to ESP32 GPIO
- **Tradeoff**: needs C/C++ (ESP-IDF), not Python; limited processing power

See [`esp32-connection.md`](esp32-connection.md) for full wiring and architecture details.

---

## Useful File Locations (in radar_toolbox_4_00_00_05)

| File | Path |
|------|------|
| OOB firmware binary | `source/ti/examples/Out_Of_Box_Demo/prebuilt_binaries/out_of_box_6843_aop.bin` |
| 3D People Tracking firmware | `source/ti/examples/Industrial_and_Personal_Electronics/People_Tracking/3D_People_Tracking/prebuilt_binaries/3D_people_track_6843_demo.bin` |
| 3D PT AOP chirp config | `source/ti/examples/Industrial_and_Personal_Electronics/People_Tracking/3D_People_Tracking/chirp_configs/AOP_6m_default.cfg` |
| OOB demo docs | `source/ti/examples/Out_Of_Box_Demo/docs/Out_Of_Box_Demo_User_Guide.html` |
| Parser code (Python) | `tools/visualizers/Applications_Visualizer/common/parseTLVs.py` |
| Frame parser | `tools/visualizers/Applications_Visualizer/common/parseFrame.py` |
| TLV defines | `tools/visualizers/Applications_Visualizer/common/tlv_defines.py` |
| OOB demo class | `tools/visualizers/Applications_Visualizer/common/Demo_Classes/out_of_box_x843.py` |
| mmWave SDK user guide | Referenced in OOB docs, section 3.4 for CLI command reference |

---

## Key References

- uRAD product page: https://www.uRAD.es/en/
- TI IWR6843AoP chip: https://www.ti.com/product/IWR6843AOP
- MMWAVE-SDK: https://www.ti.com/tool/MMWAVE-SDK (version 3.6 for this sensor)
- mmWave Demo Visualizer: https://dev.ti.com/gallery/view/mmwave/mmWave_Demo_Visualizer/ver/3.6.0/ (v3.6 only, v4.2 is NOT compatible)
- UniFlash: https://www.ti.com/tool/UNIFLASH
- Radar Toolbox: Available via TI Developer Zone Resource Explorer
- uRAD contact: contact@urad.es

---

## MPU9150 IMU & Aim Assist

The unit's case lid carries an **MPU9150** (accelerometer + AK8975
compass), wired to the Pi's I2C bus 1. Probed facts (urad-pi, Pi 3B,
unit flat on desk):

- MPU9150 answers WHO_AM_I `0x68` at address `0x68`; the AK8975 answers
  WIA `0x48` at `0x0C` behind the MPU's bypass mode.
- The MPU is mounted **upside-down on the case lid**; axis map to the
  radar frame: x→−x, y→y, z→−z. Verified live: flat desk reads pitch
  +0.5°, roll −2.3°, |g| = 1.000, noise σ ≈ 0.3°.
- Compass instantaneous noise σ ≈ 2.5° (strong but **constant** local
  field). 2 s averaging brings it to ≈ 0.5°.
- The radar boresight is **perpendicular to the case's two large faces**
  (bottom/lid). Pressing the flat bottom against a wall makes the
  boresight that wall's inward normal — no aiming skill needed at
  contact; only flushness matters, rotation of the unit is free.

**No compass calibration, ever.** The Aim Assist bisector is the
difference of two wall-capture headings sharing the same constant local
distortion, so static error cancels exactly. Do not add a calibration
wizard.

### Mounting assumptions (corner installs)

- Vertical pole in the corner, grip head + GoPro mount at the case end
  opposite the ports; the unit aims along the corner bisector with a
  slight downward tilt (~15° set by grip). The pole sits behind the
  boresight, outside the ±60° FOV.
- The **boundary box is the room proxy** — it may intentionally batch
  two rooms into one larger box. There is no separate room-dimension
  input; heights stay manual (`sensorPosition` z, boundary z-max — the
  IMU is blind to height; tape measure).
- Future installs may keep the unit on the grip during wall captures if
  the case face still sits flush — **flushness is the only contact
  requirement**.

### Install-time flow (Setup Tool, Config Stage)

1. Unclip the unit from the GoPro/grip mount.
2. Capture wall A: case bottom flush against wall A → the tool averages
   the heading ~2 s. Same on wall B.
3. The tool computes the bisector (valid for any corner angle — the math
   averages the two headings with wrap-around handling). Live
   turn-left/turn-right indicator with remaining error; the aimed band is
   **±2.5°** (deliberately loose: the ±60° FOV makes a 2.5° aim error
   irrelevant; the old bench script's ±1.5° was torture at the noise
   level).
4. Re-clip, rotate the grip until aimed, set ~15° downward elevation by
   grip, then one-click **use measured** fills the `sensorPosition`
   azim_tilt/elev_tilt fields (elevation from measured pitch, azimuth
   from the heading against the bisector — 0 when aimed). Sensor height
   stays manual.

The IMU samples ~10 Hz in Config Stage only and is silent in Running
Stage. If the chip is absent the Setup Tool is fully functional — badge
hidden, no errors.

**Bring-up item**: the measured-tilt → `sensorPosition` mapping
(elevation from measured pitch, azimuth from heading against the
bisector, sign chosen to match the page's boresight rendering in
`overlays.js`) is to be verified **once** against the TI mmWave Demo
Visualizer scene at manual bring-up, then trusted.
