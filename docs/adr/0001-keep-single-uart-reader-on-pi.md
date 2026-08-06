# ADR-0001: Keep single-UART reader on Pi

- **Status**: Accepted
- **Date**: 2026-08-06
- **Origin**: [#1 — ADR: keep single-UART reader on Pi](https://github.com/darkresq14/lares-sense/issues/1)

## Context

The deployment host for the IWR6843AoP is a **Raspberry Pi 3B** attached through the
**uRAD IWR-AWR Raspberry Pi GPIO adapter**. That adapter physically wires only a
single UART between the Pi and the radar (see
[`docs/hardware/pi-setup.md`](../hardware/pi-setup.md) "Architecture" table and
[`docs/hardware/sensor-reference.md`](../hardware/sensor-reference.md)
"USB vs GPIO Connection"):

- Pi `UART_TX` (pin 8) → radar **CFG_RX** (config commands in)
- radar **DATA_TX** → Pi `UART_RX` (pin 10) (point-cloud data out)

Because the radar's **CFG_TX (UARTA_TX, header pin 3) is not connected to the Pi**,
config ACKs never reach the host. The reader therefore sends the CLI config
**blind (no ACK)** at **115200 baud**, then switches the same port to **921600 baud**
to receive the data stream. This is exactly the mode the current Out-of-Box reader
(`radar_pi/read_radar.py`) already runs in successfully.

Two findings from the hardware work are load-bearing for this decision:

1. **Single-UART wiring is a physical constraint of the deployment form factor.**
   The GPIO adapter simply does not bring out a second UART.
2. **The Pi 3B mini-UART does not work for this stream.** By default the Pi 3B
   assigns the reliable PL011 (`ttyAMA0`) to Bluetooth and leaves the
   frequency-locked mini-UART (`ttyS0`) on GPIO 14/15, which yields 0 bytes.
   The documented fix (`dtoverlay=disable-bt`) reclaims PL011 for GPIO serial
   (`/dev/serial0 -> ttyAMA0`). See
   [`docs/hardware/sensor-reference.md`](../hardware/sensor-reference.md)
   "Raspberry Pi 3B UART Issue (Critical Finding)" and
   [`docs/hardware/pi-setup.md`](../hardware/pi-setup.md) Step 4.

The TI reference visualizer offers an alternative reader,
`readAndParseUartDoubleCOMPort`, which uses **two** COM ports (a config port and a
data port) so it can both send config and receive ACKs/responses on dedicated
lines. To use it we would need either:

- a **USB (CP2105 dual USB-to-UART bridge)** connection — this is the sensor's
  micro-USB "official PC" path and is **not** the deployment form factor, or
- the **ESP32 dual-UART** path, which is a different host entirely
  ([`docs/hardware/esp32-connection.md`](../hardware/esp32-connection.md)).

## Decision

**Keep the host-side radar reader on single-UART (Pi GPIO) mode.** Do not switch
the Pi reader to the dual-COM-port (`readAndParseUartDoubleCOMPort`) model.

Concretely, the reader continues to:

1. Send the CLI config blind (no ACK) over `/dev/serial0` at 115200 baud.
2. Switch the same port to 921600 baud and parse the data stream.
3. Run on the Pi 3B with PL011 (`ttyAMA0`) reclaimed from Bluetooth via
   `dtoverlay=disable-bt`.

## Consequences

**Positive**

- Matches the physical deployment (GPIO adapter). No extra hardware or USB cable.
- Reuses a reader mode already proven to work with the Out-of-Box Demo firmware;
  no protocol change, no new failure surface.
- Keeps the deployment headless and cheap (no PC/USB bridge required).

**Negative**

- **No config ACKs on the Pi.** Config errors are silent on the host side; a
  misconfiguration manifests only as "no data stream" and must be diagnosed by
  absence of frames rather than a NACK. Operators rely on the troubleshooting
  guide in [`pi-setup.md`](../hardware/pi-setup.md) (LED state, firmware
  verification, baud/pin-mux checks) instead.
- **No config return path on the Pi.** The single Pi UART is itself full-duplex
  (TX pin 8 and RX pin 10 are both wired), but only the radar's **DATA_TX** reaches
  the Pi; the **CFG_TX / UARTA_TX (header pin 3)** ACK/response line is not wired
  by the GPIO adapter. So there is no channel for config responses — bidirectional
  CLI interaction (e.g. live `sensorStop`/parameter tweaks with response) is not
  available without changing the wiring, and the reader treats config as a
  one-shot push.

**Deferred alternatives (revisit if constraints change)**

- **Dual-COM-port reading via USB (CP2105).** Gives full bidirectional config +
  data, ACKs included, but requires the USB form factor — acceptable for bench
  debugging, not for deployment. Use the USB path only for lab/bring-up.
- **ESP32 dual-UART host.** Native dual UART, 3.3 V logic, built-in WiFi, cheaper
  than the Pi — a real deployment alternative tracked separately
  ([`esp32-connection.md`](../hardware/esp32-connection.md)). Choosing it is a
  host substitution, not a reader-mode change on the Pi.
- **Server-side parsing.** The reader could forward raw frames over UDP unchanged
  and let the server parse TLVs regardless of transport (the current
  `radar_sender.py`/`radar_receiver.py` split already moves parsing off the Pi for
  the UDP path). For now the **primary** parsing still happens host-side on the Pi;
  moving it fully server-side is a future option, independent of the UART-mode
  decision here.

## Related

- [`docs/hardware/sensor-reference.md`](../hardware/sensor-reference.md) —
  USB vs GPIO connection, UART TLV protocol, Pi 3B mini-UART fix.
- [`docs/hardware/pi-setup.md`](../hardware/pi-setup.md) — single-UART wiring,
  PL011/disable-bt setup, troubleshooting.
- [`docs/hardware/esp32-connection.md`](../hardware/esp32-connection.md) —
  ESP32 dual-UART alternative host.
- `radar_pi/read_radar.py` — current single-UART Out-of-Box reader.
