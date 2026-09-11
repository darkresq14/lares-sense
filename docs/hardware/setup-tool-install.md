# Setup Tool install on the Pi (systemd, auto-start)

The Setup Tool ([ADR-0004](../../adr/0004-pi-served-staged-setup-tool.md))
is the Pi's deployment reader: one process owns the radar UART, serves the
web page, and — in **Running Stage** — forwards every raw frame to the
recording server over UDP (the same wire protocol `radar_sender.py` used,
so the server side needs zero changes). This guide makes it start on boot
and survive reboots with systemd.

For bench bring-up without systemd, see the
[README's Setup Tool section](../../README.md#setup-tool-pi-served-live-view).

## 0. Prerequisites

- Raspberry Pi OS on the Pi, sensor wired to the GPIO UART
  ([Pi setup](pi-setup.md), [flashing](flashing.md)).
- Serial console **disabled**, UART **enabled**:
  `sudo raspi-config` → *Interface Options* → *Serial Port* →
  login shell over serial: **No**, serial hardware enabled: **Yes**.
  Reboot, then `/dev/serial0` must exist.
- The `pi` user can open the port (group `dialout`: `groups pi`).

## 1. Deploy the repo and the venv

```bash
sudo mkdir -p /opt/lares-sense
sudo chown pi:pi /opt/lares-sense
# from your dev machine (or git clone on the Pi):
rsync -a --exclude docs_and_tools --exclude .git \
    /path/to/lares-sense/ pi@<pi>:/opt/lares-sense/

ssh pi@<pi>
cd /opt/lares-sense
python3 -m venv .venv
.venv/bin/pip install pyserial "websockets>=13" smbus2
```

`smbus2` drives the MPU9150 on the unit's lid (Aim Assist + measured
 tilt, [`sensor-reference.md`](sensor-reference.md#mpu9150-imu--aim-assist));
without it — or without the chip — the Setup Tool simply runs without the
IMU features. Enable the Pi's I2C once (`sudo raspi-config` → Interface
Options → I2C); the chip answers on bus 1.

`docs_and_tools/` (vendor SDK, ~GB) is deliberately excluded.

## 2. Point the Running Stage at the recording server

```bash
sudo tee /etc/default/lares-setup-tool <<'EOF'
# Recording server for the Running Stage UDP fan-out.
LARES_UDP_HOST=192.168.1.10
LARES_UDP_PORT=5005
EOF
```

`LARES_UDP_PORT` is optional (default `5005`, the port `radar_persist.py`
listens on). Without `LARES_UDP_HOST` the tool still runs and streams to
browsers, but nothing is recorded — the page and logs say so.

## 3. Install and enable the unit

```bash
sudo cp /opt/lares-sense/deploy/lares-setup-tool.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lares-setup-tool
```

`enable` is one-time: the service starts now **and on every boot**.

## 4. Verify

```bash
systemctl status lares-setup-tool          # active (running)
journalctl -u lares-setup-tool -f          # watch it find the sensor
```

- Open `http://<pi>:8000/` from any LAN browser.
- With no persisted Working Config yet, the tool boots into **Config
  Stage**: tune, watch the live cloud, Apply.
- Press **Start** on the page → Running Stage: frames fan out to browsers
  and to the server. On the server, `radar_persist.py` starts writing rows.
- The page always shows stage + sensor health
  (streaming / stopped / reconfiguring / error).

## 5. Boot self-recovery (why this exists)

Once a Working Config is persisted on the Pi, every boot **auto-enters
Running Stage**: systemd starts the tool, the tool re-applies the persisted
cfg to the sensor, waits for frames, and resumes UDP recording — no
browser, no human. That is the power-blip recovery path for a headless
site.

To test it: `sudo reboot`, then check from the server side that rows resume
(or `journalctl -u lares-setup-tool` shows the auto-enter line).

A deliberate **Stop** on the page returns the tool to Config Stage for that
session; the next boot resumes Running (the persisted Working Config is
still there), matching the install-time model of
[issue #12](https://github.com/darkresq14/lares-sense/issues/12).

Note: the Working Config file exists as soon as the tool has run once
(the very first run seeds it from the vendor template), so every boot
after the first auto-enters Running with whatever the file holds. A site
with a Setup Tool installed records by default — Stop is for maintenance
sessions, not for parking the site overnight.

## 6. Troubleshooting

| Symptom | Check |
|---|---|
| Unit inactive, `ConditionPathExists=/dev/serial0` failed | Serial hardware not enabled, or sensor box unplugged. |
| `permission denied` opening the port | `pi` not in `dialout`; serial console still on. |
| Page loads, "no frames resumed" on Start | Sensor not powered/flashed ([flashing](flashing.md)); check `journalctl`. |
| No rows on the server | `LARES_UDP_HOST` unset/wrong; persister not running; firewall on UDP 5005. |
| Wrong repo path | Adjust `WorkingDirectory`/`ExecStart` in the unit copy. |

## Files

| Path | Role |
|---|---|
| `deploy/lares-setup-tool.service` | the systemd unit (copy to `/etc/systemd/system/`) |
| `/etc/default/lares-setup-tool` | env file: `LARES_UDP_HOST`, `LARES_UDP_PORT` |
| `/var/lib/lares/setup_tool/` | persisted Working Config (systemd `StateDirectory`) |
