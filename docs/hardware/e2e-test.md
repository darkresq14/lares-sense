# End-to-end live test — sensor → Pi → PC console

Stepwise procedure to bring the whole pipe up by hand and verify it green,
**without any database**: this PC listens for the UDP frame stream and
prints every decoded frame to the console (`radar_receiver.py`), while the
Pi runs the **Setup Tool**, which owns the sensor UART, serves the web page
on its own IP, and — in Running Stage — forwards every raw frame to the PC.

Use this page whenever you want to *see* what the sensor sees in a terminal
(instead of persisting to Postgres with `radar_persist.py`, see
[README](../../README.md)) or after any hardware/software change to prove
the pipe end-to-end.

```
IWR6843AoP ──UART──► Pi: Setup Tool ──UDP──► PC: radar_receiver.py
                     (web UI :8000)         (console print :5005)
```

## Known-good values for this setup

| What            | Value                                  |
|-----------------|----------------------------------------|
| SSH alias       | `urad-pi` (user `darky`)               |
| Pi LAN IP       | `192.168.0.102` (wlan0)                |
| This PC LAN IP  | `192.168.0.192` (same subnet)          |
| UDP port        | `5005`                                 |
| Web UI          | `http://192.168.0.102:8000`            |
| Pi repo + venv  | `~/lares-sense` + `~/lares-sense/.venv` |
| Sensor port     | `/dev/serial0` → `ttyAMA0`             |

If IPs changed, substitute everywhere. Pi IP check: `ip -4 addr show wlan0`.

## Step 0 — Pre-flight on the Pi (2 min, catches most failures)

```bash
ssh urad-pi
ls -la /dev/serial0            # must exist and point to ttyAMA0
vcgencmd get_throttled         # must be 0x0 — see warning below
pgrep -af 'setup_tool[.]host'  # no output = no stale tool holding the UART
ls ~/.local/share/lares/setup_tool/   # working_config.cfg = auto Running Stage on start
```

> **`get_throttled` ≠ `0x0` means undervoltage.** Bit 0 set = undervoltage
> *right now*. An undervolted Pi cannot feed the sensor; the sensor goes
> silent and every later step fails with "no frames". Fix the PSU first
> (proper 5V/3A adapter, short thick cable) — no software will help.

Decode: `0x50005` = undervoltage now + it happened before. Only `0x0` is green.

## Step 1 — Start the console listener on this PC

From the repo root (PC needs only stdlib Python, no pip installs):

```bash
python -u radar_pi/radar_receiver.py --port 5005
```

- `-u` = unbuffered, so the banner and frames appear immediately.
- Expected first lines:

```
Radar UDP Receiver listening on 0.0.0.0:5005
Waiting for frames... (Ctrl+C to stop)
```

To run it in the background instead and keep the log in a file:

```bash
python -u radar_pi/radar_receiver.py --port 5005 > /tmp/radar_receiver.log 2>&1 &
tail -f /tmp/radar_receiver.log
```

**Check:** port actually bound (in a second terminal):

```bash
netstat -ano | findstr :5005
```

Must show a `UDP 0.0.0.0:5005` line. If not, see Troubleshooting (port in
use / firewall).

### Port already in use? (`OSError: [WinError 10048]`)

A stale `radar_receiver.py` (or `radar_persist.py`) from an earlier session
holds UDP 5005 and every new start dies instantly with `WinError 10048`.
Kill the holder and retry — from **PowerShell**:

```powershell
Get-NetUDPEndpoint -LocalPort 5005 -ErrorAction SilentlyContinue |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

(Manual way: `netstat -ano | findstr :5005` → note last column PID →
`powershell "Stop-Process -Id <PID> -Force"`.)

> If you paste this through **git-bash**, bash eats the `$_` — the whole
> command must sit inside single quotes, or run it in a real PowerShell
> terminal. Symptom of the mangling: `Cannot bind parameter 'Id'`.

## Step 2 — Start the Setup Tool on the Pi

One SSH command; the tool daemonizes itself with `nohup`:

```bash
ssh urad-pi
cd ~/lares-sense/radar_pi
nohup ~/lares-sense/.venv/bin/python3 -m setup_tool.host \
    --serial-port /dev/serial0 \
    --port 8000 \
    --udp-host 192.168.0.192 --udp-port 5005 \
    > /tmp/setup_tool.log 2>&1 &
```

- `--udp-host` is **this PC's IP** — that is what makes the console path
  work instead of a database.
- The tool binds `0.0.0.0`, so the web page is reachable from any LAN
  device at `http://192.168.0.102:8000` — no extra setup needed.
- A persisted **Working Config** exists → the tool auto-enters Running
  Stage within ~15 s (re-applies cfg, waits for frames, starts forwarding).
  With no Working Config it boots into Config Stage; press **Start** on the
  page once to enter Running Stage manually.

## Step 3 — Check the Pi side (sensor streaming)

```bash
tail -f /tmp/setup_tool.log
pgrep -af 'setup_tool[.]host'   # must list one python3 process
```

Green: startup lines with `UDP (Running Stage): 192.168.0.192:5005` and no
errors. Red (auto-enter failed):

```
setup-tool: boot auto-enter of Running Stage failed — no frames resumed within 15s — the sensor may not be streaming
```

That line = sensor problem (power, wiring, firmware) — the UDP/console side
cannot cause it. Go to Troubleshooting.

## Step 4 — Check the PC console (frames arriving)

Watch the receiver output from Step 1. Green looks like:

```
  Frame     1 | 57 points (57 non-zero)
    [  0] x=  0.128 y=  0.034 z= -0.303  v=  0.12 m/s ...
    ...
*** Received first fully-decoded 3D People Tracking frame (point cloud + tracks + ...)
```

The `***` banner is the official pass signal — sensor → Pi → PC pipe green,
and the full people-tracking TLV set decoded.

No output at all = UDP not arriving: wrong `--udp-host`, PC firewall, or
Step 3 already red.

## Step 5 — Check the web UI (optional but quick)

Browser on any LAN device → `http://192.168.0.102:8000`:

- live point cloud rendering (auto-connects, WebSocket on same port),
- stage indicator shows **Running** (read-only parameters),
- stop here: press **Stop** for a Config Stage session; leave running to
  keep recording.

## Step 6 — Teardown

```bash
# Pi: stop the Setup Tool (frees the UART for read_radar.py etc.)
ssh urad-pi "pkill -f 'setup_tool[.]host'"

# PC: Ctrl+C the receiver, or:
# (Windows) find PID with netstat -ano | findstr :5005, then:
# powershell "Stop-Process -Id <PID>"
```

The `[.]` in the pkill pattern is deliberate — it stops pgrep/pkill from
matching the ssh command line itself and killing its own shell.

## Plan B — sensor check without the Setup Tool

If Step 3 fails and you want to isolate sensor vs. tool, read the sensor
directly (no web UI, no UDP; prints parsed frames locally on the Pi):

```bash
ssh urad-pi
cd ~/lares-sense/radar_pi
# (make sure no setup_tool process is holding the UART — see Step 6)
~/lares-sense/.venv/bin/python3 read_radar.py \
    --port /dev/serial0 \
    --config xwr6843_aop_3d_people_track_6m.cfg \
    --num-frames 3
```

Zero output / exit after silence = sensor-side problem (power, wiring,
firmware — see [pi-setup](pi-setup.md), [flashing](flashing.md)).

Raw byte sanity check (any output at all from the sensor). NOTE: after a
`sensorStop` this firmware never restarts (measured — see the
[sensorStop wedge](sensor-reference.md#known-firmware-behavior--the-sensorstop-wedge-measured-2026-09-12));
the check below pulses the RST line (GPIO5) first:

```bash
ssh urad-pi "~/lares-sense/.venv/bin/python3 - <<'EOF'
import serial, time
from gpiozero import OutputDevice
rst = OutputDevice(5, initial_value=True)
rst.off(); time.sleep(0.2); rst.on(); rst.close()
time.sleep(2.5)
ser = serial.Serial('/dev/serial0', 115200, timeout=0.5)
for line in open('/home/darky/lares-sense/radar_pi/xwr6843_aop_3d_people_track_6m.cfg'):
    line = line.strip()
    if not line or line.startswith('%') or line == 'sensorStop':
        continue
    ser.write((line + '\n').encode()); time.sleep(0.05)
ser.close()
ser = serial.Serial('/dev/serial0', 921600, timeout=0.5)
t0 = time.time(); n = 0
while time.time() - t0 < 6:
    n += len(ser.read(4096))
print('bytes after RST + cfg:', n)
EOF"
```

`0` = sensor silent: power/wiring first, `get_throttled` second.

## Troubleshooting (real failures seen on this setup)

| Symptom | Cause | Fix |
|---|---|---|
| PC receiver dies instantly: `OSError: [WinError 10048]` | Another process already holds UDP 5005 (stale receiver from an earlier session) | PowerShell one-liner in Step 1 ("Port already in use?") kills the holder; then restart receiver |
| Receiver banner but no frames | UDP blocked by Windows Firewall (Private profile inbound) or wrong `--udp-host` | Allow inbound UDP 5005 for python.exe; verify PC IP with `ipconfig` (use the 192.168.0.x one) |
| Pi log: "no frames resumed within 15s" | Sensor not streaming | Step 0 `get_throttled`; sensor power/wiring; Plan B raw test |
| Stop→Start on the page leaves health=error forever | Pre-2026-09-12 builds sent `sensorStop`, which wedges this firmware (see [sensor-reference](sensor-reference.md)); the sensor needs the RST pulse or a Pi reboot | Update the tool (reset-based Start/Stop, `sensor_reset.py`) and restart it |
| `vcgencmd get_throttled` shows `0x50005` etc. | Pi undervoltage — sensor starved | Swap PSU/cable (5V/3A); re-check after fix |
| `pkill` over ssh kills the ssh session itself (exit 255) | `pkill -f` pattern matches the ssh wrapper's own command line | Always write the pattern with a char class: `'setup_tool[.]host'` |
| Receiver output lags or looks empty | Python stdout buffered when redirected | Run with `python -u`, or `tail -f` the log file |
| Web page unreachable but console frames flow | Browser on another subnet; tool binds 0.0.0.0 so it's a routing/firewall thing | Open from a device on the same LAN; `curl http://192.168.0.102:8000` from the PC to test |

## Related

- [pi-setup.md](pi-setup.md) — wiring, UART config, `read_radar.py`
- [setup-tool-install.md](setup-tool-install.md) — systemd deployment (auto-start on boot) instead of this manual run
- [flashing.md](flashing.md) — firmware reflash over USB
- README — architecture, `radar_sender.py` (UDP without the Setup Tool), `radar_persist.py` (database instead of console)
