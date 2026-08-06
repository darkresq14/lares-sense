# ESP32 Connection Guide

## Overview

The uRAD IWR6843AoP sensor can be connected directly to an ESP32 via the exposed 10-pin header, bypassing the Raspberry Pi entirely. This is arguably a better hardware match for the sensor.

## Pin Connections

The uRAD board has a 10-pin female header (2.54mm spacing) with the following pinout:

| uRAD Pin | Signal | Direction | ESP32 Pin | Notes |
|----------|--------|-----------|-----------|-------|
| 1 | GND | - | GND | |
| 2 | 5V | Power | VIN or external 5V | Sensor draws ~340mA, ensure supply can handle it |
| 3 | UART TX config | Sensor -> ESP32 | UART1 RX (GPIO18) | Config responses/ACKs, 115200 baud |
| 4 | UART RX config | ESP32 -> Sensor | UART1 TX (GPIO17) | Config commands, 115200 baud |
| 5 | UART TX data | Sensor -> ESP32 | UART2 RX (GPIO16) | Point cloud data, 921600 baud |
| 6 | UART RX data | ESP32 -> Sensor | UART2 TX (GPIO4) | Optional, not used by OOB demo |
| 7 | RESET | ESP32 -> Sensor | GPIO13 | Active pulse (pull LOW then HIGH) |
| 8 | ON/OFF | ESP32 -> Sensor | GPIO12 | HIGH=ON, LOW=OFF |
| 9 | GPIO1 | Sensor GPIO | Any GPIO | Not used by default firmware |
| 10 | GPIO0 | Sensor GPIO | Any GPIO | Not used by default firmware |

GPIO numbers above are suggestions. ESP32 allows most GPIOs to be remapped for UART.

## Wiring Diagram

```
uRAD 10-pin header          ESP32 Dev Board
┌──────────────────┐       ┌──────────────────┐
│ 1  GND           │───────│ GND              │
│ 2  5V            │───────│ VIN (or ext 5V)  │
│ 3  TX config     │───────│ GPIO18 (UART1 RX)│
│ 4  RX config     │───────│ GPIO17 (UART1 TX)│
│ 5  TX data       │───────│ GPIO16 (UART2 RX)│
│ 6  RX data       │───────│ GPIO4  (UART2 TX)│
│ 7  RESET         │───────│ GPIO13           │
│ 8  ON/OFF        │───────│ GPIO12           │
│ 9  GPIO1         │       │ (unused)         │
│ 10 GPIO0         │       │ (unused)         │
└──────────────────┘       └──────────────────┘
```

## Power Considerations

- The sensor draws **~340mA** at 5V
- ESP32 draws ~200-300mA with WiFi active
- A dedicated 5V 2A supply is recommended
- Power the sensor via pin 2 (5V) - do NOT rely on the ESP32's USB 5V line for both
- Both devices share a common GND (pin 1)

## Logic Levels

Both the IWR6843AoP and ESP32 use **3.3V logic** on their UART and GPIO pins. No level shifters needed.

## Advantages Over Raspberry Pi

### Dual UART (biggest advantage)

The Pi + uRAD adapter is limited to a **single UART** (Pi TX -> sensor config RX, sensor data TX -> Pi RX). You cannot receive config ACKs and data simultaneously.

The ESP32 has **two hardware UARTs** available (UART1 and UART2):
- UART1: full bidirectional config channel (send commands AND receive ACKs)
- UART2: receive point cloud data stream at 921600 baud

This means proper error handling on config commands and no baud-rate-switching hack.

### Remote Firmware Flashing

The 10-pin header does NOT expose the SOP2 line (boot mode select), which is needed to enter flashing mode. However, you can add **one wire** from the SOP2 pad on the uRAD PCB to an ESP32 GPIO:

```
uRAD PCB SOP2 pad ──── [1k resistor] ──── ESP32 GPIO14
```

With this, the ESP32 can:
1. Set SOP2 GPIO HIGH (enter flashing mode)
2. Pulse RESET LOW then HIGH
3. Flash firmware via UART1 (using TI's UniFlash CLI equivalent or custom XMODEM)
4. Set SOP2 GPIO LOW (back to functional mode)
5. Pulse RESET LOW then HIGH
6. Send config and start receiving data

### Built-in WiFi

The ESP32 has WiFi (and BLE), so it can:
- Stream UDP point cloud data directly to your server
- Be controlled remotely over the network
- Act as a self-contained radar node with no host computer needed

### Size and Cost

| | Raspberry Pi 3B | ESP32 |
|--|-----------------|-------|
| Cost | ~35 EUR | ~5 EUR |
| Size | 85x56mm | ~55x28mm |
| Power consumption | ~1.5W idle | ~0.5W |
| Boots from SD | Yes (slow) | No (instant) |

### UART Speed

The ESP32 UART supports up to **5 Mbps**, so the 921600 baud data stream is well within spec with plenty of headroom.

## Limitations

- **Processing power**: ESP32 is dual-core 240MHz with ~520KB RAM. Fine for reading/parsing/forwarding point cloud data, but not for heavy signal processing or visualization.
- **Python ecosystem**: ESP32 typically runs C/C++ (ESP-IDF) or MicroPython. The current Python scripts would need to be rewritten. MicroPython has `machine.UART` but may struggle at 921600 baud - C/ESP-IDF is recommended.
- **No display**: If you want local visualization, the Pi is better.

## Software Architecture (ESP-IDF)

```
┌─────────────────────────────────────────────┐
│                  ESP32                       │
│                                              │
│  UART1 (115200)          UART2 (921600)     │
│  Send config cmds        Receive raw frames  │
│  Receive ACKs            Parse TLVs          │
│                                              │
│           ┌─────────────────┐               │
│           │   Main Task     │               │
│           │   - State mgmt  │               │
│           │   - Frame parse │               │
│           │   - UDP send    │               │
│           └─────────────────┘               │
│                    │                         │
│              WiFi / UDP                      │
│           Send to <SERVER_IP>:5005           │
└─────────────────────────────────────────────┘
```

## Optional: Remote Flashing Setup

To enable full remote firmware updates, solder a 1kΩ resistor from the SOP2 test point on the uRAD PCB to an ESP32 GPIO. The SOP2 pad location can be identified from the uRAD schematic or by tracing from the DIP switch.

The flash sequence over WiFi/UART:
1. Receive new `.bin` firmware over WiFi (HTTP or MQTT)
2. Store in ESP32 flash partition
3. Set SOP2 GPIO HIGH
4. Pulse RESET (LOW for 50ms, then HIGH)
5. Wait for sensor to enter boot mode (~2s)
6. Send firmware via UART1 using XMODEM protocol
7. Set SOP2 GPIO LOW
8. Pulse RESET
9. Sensor boots with new firmware
