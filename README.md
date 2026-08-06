# lares-sense

mmWave radar (IWR6843AoP) point-cloud sensing, parsed over UART and streamed as
UDP. The radar runs the TI Out-of-Box Demo firmware; host code parses the binary
TLV frames and forwards point clouds to a server.

## Architecture

```
   IWR6843AoP ──UART──► Host (Pi 3B / ESP32) ──UDP──► Server
   (Out-of-Box            parse TLV frames              radar_receiver.py
    Demo firmware)        send to <server>:5005         prints point clouds
```

Two supported hosts:

- **Raspberry Pi 3B** — single-UART mode, Python reader (`radar_pi/`). See
  [`docs/hardware/pi-setup.md`](docs/hardware/pi-setup.md).
- **ESP32** — dual UART, built-in WiFi, cheaper, no host PC needed. See
  [`docs/hardware/esp32-connection.md`](docs/hardware/esp32-connection.md).

## Repository layout

```
lares-sense/
├── radar_pi/            host-side Python tools (Pi reader + UDP sender/receiver)
├── docs/
│   ├── hardware/        sensor reference + host setup guides
│   └── agents/          agent-skill config (issue tracker, triage, domain)
├── docs_and_tools/      vendor tools + datasheets — NOT in git (see below)
├── fetch_vendor.sh      idempotent vendor downloader (optional)
└── README.md
```

## Documentation

| Doc | What it covers |
|-----|----------------|
| [`docs/hardware/sensor-reference.md`](docs/hardware/sensor-reference.md) | Sensor specs, firmware, UART TLV protocol, Pi 3B UART issue, TI ecosystem |
| [`docs/hardware/pi-setup.md`](docs/hardware/pi-setup.md) | Raspberry Pi wiring, prerequisites, step-by-step setup, troubleshooting |
| [`docs/hardware/esp32-connection.md`](docs/hardware/esp32-connection.md) | ESP32 wiring, dual-UART architecture, remote flashing |

## Quick start (Raspberry Pi)

1. Flash the Out-of-Box Demo firmware (`out_of_box_6843_aop.bin`) via UniFlash.
2. Wire the sensor to the Pi GPIO header (single UART) — see
   [`docs/hardware/pi-setup.md`](docs/hardware/pi-setup.md).
3. On the Pi:

   ```bash
   python3 -m venv ~/radar_env && ~/radar_env/bin/pip install pyserial numpy
   ~/radar_env/bin/python3 radar_pi/read_radar.py
   ```

4. To stream over UDP: run `radar_pi/radar_receiver.py` on the server, then
   `radar_pi/radar_sender.py` on the Pi (see pi-setup guide for ports + firewall).

## Vendor tools & docs (`docs_and_tools/`)

`docs_and_tools/` (~1.8 GB) holds TI vendor binaries and datasheets. It is
**gitignored and not versioned** — intentionally, to keep the repo small.

### How to get it

Pick one:

1. **Scripted** (dev machines) — download everything idempotently:

   ```bash
   ./fetch_vendor.sh      # skips items already present
   ```

2. **Manual** — grab from TI and drop into `docs_and_tools/`:
   - TI Radar Toolbox 4.00.00.05
   - UniFlash (`uniflash_sl.<version>.run`)
   - CP210x VCP USB driver
   - Device datasheets / reference PDFs

   Source URLs: `docs_and_tools/USEFUL_LINKS.txt` and
   <https://www.ti.com/tool/RADAR-TOOLBOX>.
