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
