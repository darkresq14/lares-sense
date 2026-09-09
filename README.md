# lares-sense

mmWave radar (IWR6843AoP) point-cloud sensing, parsed over UART and streamed as
UDP. The host code parses TI's binary TLV frames and forwards them to a server,
supporting both the **Out-of-Box Demo** and the **3D People Tracking** demo
(point cloud + tracks + heights + per-point track indexes).

## Architecture

```
   IWR6843AoP ──UART──► Host (Pi 3B / ESP32) ──UDP──► Server
   (OOB Demo or          parse TLV frames            radar_receiver.py
    3D People Tracking)  send to <server>:5005         prints point clouds
                                                         + tracks + heights
                                                        radar_persist.py
                                                          stores frames in
                                                          Postgres+Timescale
```

Two supported hosts:

- **Raspberry Pi 3B** — single-UART mode, Python reader (`radar_pi/`). See
  [`docs/hardware/pi-setup.md`](docs/hardware/pi-setup.md).
- **ESP32** — dual UART, built-in WiFi, cheaper, no host PC needed. See
  [`docs/hardware/esp32-connection.md`](docs/hardware/esp32-connection.md).

The frame parser (`radar_pi/tlv_parser.py`) is transport-agnostic and
decodes both demo formats from a raw frame buffer, so the same UDP path carries
either firmware's output.

## Repository layout

```
lares-sense/
├── radar_pi/            host-side Python tools (Pi reader + UDP sender/receiver/persister)
│   ├── setup_tool/      Pi-served Setup Tool (live point-cloud page + WS host)
│   └── tests/           unit + integration tests (run from radar_pi/, see "Running tests")
├── docs/
│   ├── hardware/        sensor reference + host setup guides
│   ├── adr/             architecture decision records
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
| [`docs/hardware/flashing.md`](docs/hardware/flashing.md) | Reflash firmware over USB (UniFlash): prep, GUI + headless, bench verify, troubleshooting |
| [`docs/hardware/esp32-connection.md`](docs/hardware/esp32-connection.md) | ESP32 wiring, dual-UART architecture, remote flashing |

## Quick start (Raspberry Pi)

1. Flash the Out-of-Box Demo firmware (`out_of_box_6843_aop.bin`) via UniFlash.
2. Wire the sensor to the Pi GPIO header (single UART) — see
   [`docs/hardware/pi-setup.md`](docs/hardware/pi-setup.md).
3. On the Pi:

   ```bash
   python3 -m venv ~/radar_env && ~/radar_env/bin/pip install pyserial
   ~/radar_env/bin/python3 radar_pi/read_radar.py
   ```

4. To stream over UDP: run `radar_pi/radar_receiver.py` on the server, then
   `radar_pi/radar_sender.py` on the Pi (see pi-setup guide for ports + firewall).

## 3D People Tracking

The same reader works with TI's **3D People Tracking** demo, which streams an
extended TLV set the parser also decodes:

| TLV  | Name          | Decoded as |
|------|---------------|------------|
| 1020 | Point cloud   | compressed spherical → cartesian x,y,z + doppler + SNR |
| 1010 | Target list   | per track: id + position/velocity/acceleration + error covariance + g + confidence |
| 1012 | Target height | per track: max_z / min_z |
| 1011 | Target index  | per detected point: which track it belongs to (253/254/255 = not associated) |

To run it, flash the 3D People Tracking firmware
(`3D_people_track_6843_demo.bin` from the Radar Toolbox) via UniFlash — a
human-performed hardware step; see
[`docs/hardware/flashing.md`](docs/hardware/flashing.md) — then on the Pi:

```bash
# receiver on the server
python3 radar_pi/radar_receiver.py --port 5005

# sender on the Pi (selects the AOP 3D People Tracking config)
python3 radar_pi/radar_sender.py --host <SERVER_IP> \
  --config radar_pi/xwr6843_aop_3d_people_track_6m.cfg
```

The receiver logs a banner on the first fully-decoded tracking frame (point
cloud + tracks + heights + indexes), proving the sensor → Pi → server pipe is
green.

## Setup Tool (Pi-served live view)

[`radar_pi/setup_tool/`](radar_pi/setup_tool/) is the **Setup Tool**
([ADR-0004](docs/adr/0004-pi-served-staged-setup-tool.md)): one Pi process
owns the UART, serves the web page over HTTP, and streams every parsed frame
over a WebSocket **on the same port**. The page auto-connects and renders the
live point cloud with three.js (`THREE.Points`, preallocated buffers), and —
since issue #10 — carries a **tiered config editor**: every parameter of the
6 m people-tracking cfg is editable, grouped into *Room & mounting*,
*Tracking* and *RF & chirp (expert)* tiers, with info text and documented
ranges per field (sourced from the TI SDK user's guide and the people-tracking
tuning guides). Stages, apply-to-sensor and overlays arrive in later tickets.

**Working Config persistence**: saving validates locally (out-of-range or
wrong-type values are rejected inline — nothing reaches the serial device) and
writes the full generated cfg to a Pi-local data dir
(`~/.local/share/lares/setup_tool/working_config.cfg`; override with
`--data-dir` or `$SETUP_TOOL_DATA_DIR`). First run seeds it from the pristine
vendor template (`radar_pi/xwr6843_aop_3d_people_track_6m.cfg`); the repo
never stores the working copy.

On the Pi (sensor already streaming — configure it once with `read_radar.py`):

```bash
~/radar_env/bin/pip install pyserial "websockets>=13"
cd radar_pi
~/radar_env/bin/python3 -m setup_tool --serial-port /dev/serial0 --port 8000
```

Then open `http://<pi-ip>:8000/` from any browser on the LAN and watch points
move. The WS messages are `{"type": "frame", "frame": {...}}` where
`frame` is exactly what `tlv_parser.parse_frame` returns — the same shape the
persist path consumes. Slow browsers can't stall the reader: per-client
queues are bounded with drop-oldest overflow. The same stream carries the
config protocol (`config_get` / `config_set` → `config` / `config_saved` /
`config_rejected`).

## Server consumers: either/or

Two server scripts listen on the same UDP stream — **only one of them can own
a given port at a time** (UDP delivers each datagram to one socket; the second
process fails to bind). `radar_sender.py` on the Pi targets a single
`host:port`, so per port you pick one:

| Script | What it does | When to use |
|--------|--------------|-------------|
| `radar_pi/radar_receiver.py` | Decodes frames and **prints** them to the console (point cloud, tracks, heights, indexes) | Live debugging, bring-up, proving the pipe works. Stores nothing. |
| `radar_pi/radar_persist.py` | Decodes frames and **writes every one** to PostgreSQL + TimescaleDB for later query/replay | Normal operation — run this on the recording server. Prints only status lines. |

For a live view *while* persisting, run the persister on the wire and query
the database (see sample queries below), or run a second receiver on another
port with a sender that multicasts to both (not currently supported).

## Persisting frames (server)

`radar_pi/radar_persist.py` listens on the same UDP stream and durably stores
every parsed frame in **PostgreSQL + TimescaleDB** (hypertable on the ingest
timestamp), so sessions can be queried and replayed later. Frames are batched
in memory (default 50 rows / 1 s) before each write, so a crash can lose the
few frames still in flight; everything flushed is durable. Schema decisions:
[`docs/adr/0002-one-row-per-frame-timescale-hypertable.md`](docs/adr/0002-one-row-per-frame-timescale-hypertable.md).

```bash
# server: needs PostgreSQL with the TimescaleDB extension + psycopg 3
pip install "psycopg[binary]"
python3 radar_pi/radar_persist.py \
    --dsn postgresql://user:pass@localhost/lares --port 5005
```

Configuration resolves as **CLI argument > environment variable > `.env` file > default**. A `.env` in the working directory is picked up automatically ([`.env.example`](.env.example)
is the documented template; the real `.env` is gitignored — keep credentials
out of git):

```bash
cp .env.example .env   # then edit DATABASE_URL
python3 radar_pi/radar_persist.py   # everything from .env
```

| Option | Env var | Default | What it controls |
|--------|---------|---------|------------------|
| `--dsn` | `DATABASE_URL` | *required* | PostgreSQL connection string (`postgresql://user:pass@host:5432/db`). Must be a server with the TimescaleDB extension. |
| `--port` | `RADAR_PORT` | `5005` | UDP port to listen on. Must match the port the Pi's `radar_sender.py` sends to. |
| `--bind` | `RADAR_BIND` | `0.0.0.0` | Local network interface the listener attaches to. `0.0.0.0` = listen on **all interfaces** (needed when frames arrive from another machine, e.g. the Pi). Use `127.0.0.1` to accept local traffic only (test setups). |
| `--batch-size` | `RADAR_BATCH_SIZE` | `50` | Rows (frames) per INSERT transaction. Bigger = fewer round-trips, more frames lost on a crash. |
| `--flush-interval` | `RADAR_FLUSH_INTERVAL` | `1.0` | Max seconds a received frame may sit in the write buffer before it must be written, even when the batch isn't full. |
| `--env-file` | — | `.env` | Path to a `KEY=VALUE` config file. The implicit `.env` may be absent; an explicit `--env-file` that doesn't exist is an error. |

Writes are batched (default 50 rows / 1 s, both tunable); the schema is created
idempotently on start. Sample replay queries:

```sql
-- every track in the last minute
SELECT ts, frame_num, track_data FROM frames
WHERE ts > now() - interval '1 minute' AND track_data IS NOT NULL
ORDER BY ts;

-- 1-second point-cloud buckets over the last 5 minutes
SELECT time_bucket('1 s', ts) AS bucket, count(*) AS frames,
       avg(array_length(x, 1)) AS avg_points
FROM frames WHERE ts > now() - interval '5 minutes'
GROUP BY bucket ORDER BY bucket;

-- per-point replay of one frame (parallel arrays -> rows)
SELECT p.x, p.y, p.z, p.doppler, p.track_id
FROM frames, unnest(x, y, z, doppler, track_indexes)
     AS p(x, y, z, doppler, track_id)
WHERE frame_num = 100;
```

A local throwaway database for development:

```bash
docker run -d --name lares-ts -e POSTGRES_PASSWORD=lares -e POSTGRES_DB=lares \
    -p 5432:5432 timescale/timescaledb:latest-pg16
```

## Running tests

Tests live in [`radar_pi/tests/`](radar_pi/tests) — unit tests build
byte-exact frames in memory, no hardware needed. Run from `radar_pi/`:

```bash
python3 -m unittest discover -s tests -t .   # everything
python3 -m unittest tests.test_tlv_parser -v  # one module
```

The integration tests (`tests/test_frame_store_integration.py`) need a real
PostgreSQL + TimescaleDB. **The suite manages it for you**: if Docker is
available it creates/starts/reuses the `lares-ts-test` container
(`timescale/timescaledb:latest-pg16` on port 54329) automatically, and leaves
it running for fast reruns. On machines without Docker the integration tests
auto-skip — only the unit tests run.

To point the integration tests at a database you already run, set
`TEST_DATABASE_URL` — honoured **only when it points at localhost** (the
setup drops the `frames` table, so a stray env var aimed at a remote/prod
database is ignored).

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
