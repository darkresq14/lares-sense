# Flashing the IWR6843AoP Firmware

How to reflash the uRAD Industrial IWR6843AoP's firmware — e.g. to switch
between the **Out-of-Box Demo** and the **3D People Tracking** demo. This is a
**bench/lab step done over USB on a PC**, not part of the Pi deployment (the Pi
runs the sensor read-only over GPIO — see
[`pi-setup.md`](pi-setup.md) and
[ADR-0001](../adr/0001-keep-single-uart-reader-on-pi.md)).

> Vendor tooling lives under `docs_and_tools/` (gitignored, ~1.8 GB). Get it with
> `./fetch_vendor.sh`. Firmware binaries and UniFlash are **not** versioned.

---

## 1. When you need to flash

- Switching demos (OOB ↔ 3D People Tracking, or any other Radar Toolbox demo).
- The sensor won't start after `sensorStart` and you suspect a corrupt/wrong image.
- First bring-up of a sensor that shipped blank or was erased.

You do **not** flash for normal operation — once flashed, the sensor boots the
image from SPI flash in functional mode.

---

## 2. Hardware prep (do this every flash)

1. **Connect the sensor to the PC by micro-USB** (the CP2105 dual USB-to-UART
   bridge). Windows enumerates **two** COM ports — check Device Manager or:
   ```bash
   powershell.exe -NoProfile -Command "Get-CimInstance Win32_PnPEntity | Where-Object {$_.Name -match 'CP2105'} | Select-Object -ExpandProperty Name"
   ```
   - **Enhanced COM port** → the **config UART (UARTA)** → **this is the flash port**.
   - **Standard COM port** → the data UART (UARTB) → unused during flashing.

   On this machine: `COM4` = Enhanced (config/flash), `COM3` = Standard (data).
   Always confirm on yours — the numbers can change.

2. **Set the SOP2 DIP switch to ON (HIGH)** = flashing mode. The IWR6843 ROM
   bootloader then listens on the config UART for an image download.
   (Functional mode = DIP OFF; see
   [`sensor-reference.md`](sensor-reference.md) "DIP switch".)

3. **Reset the sensor** — replug the USB cable or press RESET. It now boots into
   the bootloader and waits.

> The Silicon Labs CP210x VCP driver must be installed for the COM ports to
> appear. If they don't, install "CP210x Windows VCP" from Silicon Labs.

---

## 3. Flash with TI UniFlash (GUI)

UniFlash is the supported flasher for the IWR6843. Install **TI UniFlash for
Windows** from <https://www.ti.com/tool/UNIFLASH> (free TI-account login
required for the download). This machine has it at `G:\Lares\ti\uniflash_9.6.0`
(not versioned — machine-local).

The IWR6843 has **no XDS debug probe** (unlike the TI IWR6843ISK/AOPEVM), so it
flashes over **UART**, not JTAG.

1. Launch **CCStudio UniFlash**.
2. **New Configuration**:
   - Connection: **UART Connection**
   - Device: **IWR6843AOP** (under *mmWave Sensors*)
3. In the connection settings, set the **config COM port** = the **Enhanced**
   port (e.g. `COM4`) at **115200 baud** (ROM bootloader speed).
4. Under the IWR6843AOP flash images, add the firmware `.bin` as **Meta Image 1**:
   - 3D People Tracking:
     `docs_and_tools/radar_toolbox_4_00_00_05/source/ti/examples/Industrial_and_Personal_Electronics/People_Tracking/3D_People_Tracking/prebuilt_binaries/3D_people_track_6843_demo.bin`
   - Out-of-Box (to revert):
     `docs_and_tools/radar_toolbox_4_00_00_05/source/ti/examples/Out_Of_Box_Demo/prebuilt_binaries/out_of_box_6843_aop.bin`
5. **Verify** the image is selected, then click **Load Image** (Program). Wait for
   the progress bar to finish and (recommended) run **Verify**.

### Headless re-flash with `dslite` (after the first GUI flash)

`dslite` (UniFlash's CLI) needs a **target config (`.ccxml`)** that the GUI
generates — the UART COM port/baud are not templated, so the first config must
be created and **saved/exported** in the GUI. Once you have a `.ccxml`:

```bash
cd /g/Lares/ti/uniflash_9.6.0
./dslite.bat flash --config="<path-to-your>.ccxml" -f "<firmware>.bin"
```

This makes subsequent re-flashes (OOB ↔ 3D PT) fully scriptable. You cannot
reliably hand-craft the `.ccxml` blind because the UART port is set via the GUI
connection properties, not the connection template.

---

## 4. After flashing

1. **SOP2 DIP switch → OFF (LOW)** = functional mode (boots from flash).
2. **Reset** the sensor (replug USB / press RESET).
3. Verify it boots: both **red and green LEDs ON**, and sending a config +
   `sensorStart` on the config UART produces a binary data stream on the data
   UART.

### Bench verify on the PC (no Pi needed)

Use `radar_pi/bringup_dualcom.py` over the USB dual-COM link: send the 3D People
Tracking config on the Enhanced (config) port and read/parse frames on the
Standard (data) port, confirming the extended TLVs (point cloud + tracks +
heights + indexes) decode live:

```bash
python radar_pi/bringup_dualcom.py \
  --config-port COM4 --data-port COM3 \
  --cfg radar_pi/xwr6843_aop_3d_people_track_6m.cfg --num-frames 20
```

Expect a `*** first fully-decoded 3D People Tracking frame ***` banner. This
proves the flash took and the parser handles the new firmware before you move the
sensor to the Pi.

---

## 5. Troubleshooting

- **COM port not detected / "recheck COM port selected"**: install the CP210x VCP
  driver; confirm the **Enhanced** port is the one selected for flashing; close
  any other app holding the port (putty, another reader).
- **"Initial response from the device was not received"**: SOP2 not in flash mode
  (DIP must be ON), or you didn't reset after flipping it. Re-check the DIP and
  reset.
- **Verify fails after program**: re-run Load Image; a dropped byte over UART is
  the usual cause. The ROM bootloader is in ROM — a failed flash **cannot brick**
  the device; just retry.
- **Wrong image boots / no data after `sensorStart`**: the flashed demo and the
  config file must match (OOB `.bin` ↔ OOB `.cfg`; 3D PT `.bin` ↔ 3D PT `.cfg`).

---

## References

- UniFlash Quick Start Guide (v9.6): <https://software-dl.ti.com/ccs/esd/uniflash/docs/v9_6/uniflash_quick_start_guide.html>
- UniFlash download: <https://www.ti.com/tool/UNIFLASH>
- TI mmWave flashing app note (SWRA627): <https://www.ti.com/lit/an/swra627/swra627.pdf>
- Sensor SOP modes / UART / LED states: [`sensor-reference.md`](sensor-reference.md)
- Pi deployment (functional mode, single UART): [`pi-setup.md`](pi-setup.md)
