# Raspberry Pi Setup — Point Cloud Reader

## Architecture

The uRAD IWR-AWR adapter connects the IWR6843AoP to the Raspberry Pi via the **GPIO header**, not USB. This uses a **single UART** connection:

| uRAD pin       | Raspberry Pi pin       | Purpose                         |
|----------------|------------------------|---------------------------------|
| CFG_RX         | UART_TX (pin 8)        | Pi sends config to radar CLI    |
| DATA_TX        | UART_RX (pin 10)       | Radar sends data to Pi          |
| RST            | GPIO5 (pin 29)         | Reset radar                     |
| ON/OFF         | GPIO6 (pin 31)         | Power on/off                    |
| 5V             | 5V (pin 2,4)           | Power (optional, USB also works)|
| GND            | GND (pin 6,9,14)       | Ground                          |

Key detail: config is sent at **115200 baud**, data is received at **921600 baud** on the **same serial port** (`/dev/serial0`). After sending `sensorStart`, the baud rate is switched.

In single UART mode the Pi does NOT receive config ACKs (those go out on a separate UARTA_TX line that is not connected to the Pi). Only the data stream (UARTB_TX) reaches the Pi. This is normal.

## Files

- `read_radar.py` - Local point cloud reader (single UART mode)
- `radar_sender.py` - Reads radar via UART, streams raw TI frames over UDP to a server
- `radar_receiver.py` - Listens for UDP, parses TI frames, prints point clouds (pure stdlib)
- `xwr6843_aop_out_of_box.cfg` - Radar CLI configuration for the out-of-box demo
- Firmware binary: `radar_toolbox_4_00_00_05/source/ti/examples/Out_Of_Box_Demo/prebuilt_binaries/out_of_box_6843_aop.bin`

## Prerequisites

- The Out of Box Demo firmware must already be flashed on the sensor (it ships pre-loaded)
- The DIP switch must be in **Functional mode** (OFF / low position)
- Both red and green LEDs should be ON when powered in functional mode

## Step-by-step setup

### Step 1: Copy files to the Pi

```bash
scp read_radar.py xwr6843_aop_out_of_box.cfg <PI_HOST>:~/
```

### Step 2: Install Python dependencies on the Pi

```bash
ssh <PI_HOST> "python3 -m venv ~/radar_env && ~/radar_env/bin/pip install pyserial numpy"
```

### Step 3: Check serial port availability

```bash
ssh <PI_HOST> "ls -la /dev/serial0"
```

`/dev/serial0` should exist. Verify it points to `ttyAMA0` (PL011 hardware UART), not `ttyS0` (mini UART). If it points to `ttyS0`, see Step 4.

### Step 4: Disable Bluetooth and use PL011 UART (CRITICAL for Pi 3B)

On Raspberry Pi 3B, Bluetooth uses the full PL011 hardware UART by default, leaving the unreliable mini UART (`ttyS0`) for GPIO serial. The mini UART does not work reliably for radar communication. You must disable Bluetooth to reclaim the PL011 UART:

```bash
# Add disable-bt overlay to config
echo 'dtoverlay=disable-bt' | sudo tee -a /boot/firmware/config.txt

# Disable Bluetooth UART service (if it exists)
sudo systemctl disable hciuart 2>/dev/null

# Reboot
sudo reboot
```

After reboot, verify:
```bash
ls -la /dev/serial0
# Should show: /dev/serial0 -> ttyAMA0
```

Also verify pin mux:
```bash
pinctrl get | grep -E '(14:|15:)'
# Should show: a0 (ALT0 = TXD0/RXD0 on PL011)
# NOT a5 (ALT5 = mini UART)
```

### Step 5: Enable serial port on the Pi (if not already)

```bash
ssh <PI_HOST> "sudo raspi-config"
# -> Interface Options -> Serial Port
# -> Login shell over serial: NO
# -> Serial hardware enabled: YES
```

### Step 6: Ensure user has serial port permissions

```bash
ssh <PI_HOST> "sudo usermod -aG dialout \$USER"
```

Log out and back in for the group change to take effect.

### Step 7: Run the local reader

```bash
ssh <PI_HOST> "cd ~ && ~/radar_env/bin/python3 read_radar.py"
```

Read 10 frames only:

```bash
ssh <PI_HOST> "cd ~ && ~/radar_env/bin/python3 read_radar.py --num-frames 10"
```

### Step 8: UDP streaming to a server

Copy the sender to Pi and receiver to server:

```bash
scp radar_sender.py xwr6843_aop_out_of_box.cfg <PI_HOST>:~/
scp radar_receiver.py <SERVER_HOST>:~/
```

Open the firewall on the server for UDP (default port 5005):

```bash
# On the server (e.g. your home server)
sudo ufw allow 5005/udp
# Or if not using ufw:
sudo iptables -I INPUT 1 -p udp --dport 5005 -j ACCEPT
```

Start the receiver on the server first, then the sender on the Pi:

```bash
# On server (no dependencies, pure Python stdlib)
python3 ~/radar_receiver.py --port 5005

# On Pi
~/radar_env/bin/python3 ~/radar_sender.py --host <SERVER_IP> --port 5005
```

The receiver will buffer incoming UDP datagrams, find frame boundaries using the TI magic word, and print parsed point clouds.

## Expected output

```
  Frame     1 | 4 points (4 non-zero)
    [  0] x=  0.028 y=  0.034 z= -0.003  v=  0.00 m/s  range= 0.044m  az=  39.4deg  SNR= 18.4dB
    [  1] x=  0.016 y=  0.325 z= -0.410  v=  0.00 m/s  range= 0.523m  az=   2.8deg  SNR= 15.4dB
    [  2] x= -0.018 y=  0.561 z= -0.239  v=  0.00 m/s  range= 0.610m  az=  -1.9deg  SNR= 16.7dB
    [  3] x=  1.619 y=  0.924 z=  3.354  v=  0.00 m/s  range= 3.837m  az=  60.3deg  SNR= 15.7dB
```

The garbled `RX<` lines during config sending are normal - that is binary data from the radar that was already buffered before the baud rate switch.

## Troubleshooting

- **Cannot open /dev/serial0**: Enable serial via `sudo raspi-config` (Interface Options -> Serial Port). Disable login shell, enable hardware port.
- **Permission denied**: Add user to dialout group: `sudo usermod -aG dialout $USER`, then log out/in.
- **0 bytes / no data**: On Pi 3B, you MUST disable Bluetooth (`dtoverlay=disable-bt`). The mini UART (ttyS0) does not work. Verify `/dev/serial0 -> ttyAMA0`.
- **No data after config**: Check DIP switch is in Functional mode (OFF). Both red AND green LEDs should be ON.
- **Config errors from sensor**: Make sure the firmware is `out_of_box_6843_aop.bin` (SDK 3.6). Re-flash if needed.
- **Firmware verification**: If the radar has the Out of Box Demo firmware, sending the config + sensorStart via UART will produce point cloud data. No data = firmware issue or UART issue.
- **UDP packets not reaching server**: Check firewall on the server. UFW default is DROP incoming. Run `sudo ufw allow 5005/udp`. Verify with `sudo iptables -L INPUT -n` (look for policy DROP and no UDP allow rule).
- **Pi can ping server but UDP fails**: ICMP is often allowed through firewalls while UDP is not. This is almost always a firewall issue on the server side.
