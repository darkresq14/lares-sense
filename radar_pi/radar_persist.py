#!/usr/bin/env python3
"""
Server-side UDP consumer that durably stores every parsed radar frame in
PostgreSQL + TimescaleDB.

Listens on the same UDP stream the Pi's ``radar_sender.py`` publishes
(one datagram per raw TI frame), carves frames out with ``frame_stream``,
decodes them with ``tlv_parser`` and batches them into the ``frames``
hypertable via ``frame_store.FrameStore`` (see that module for the schema and
the NULL/empty column semantics). Frames keep flowing in while batches are
written, so the consumer keeps up with the live frame rate (10 fps default).

Replay / query the stored history with SQL, e.g.::

    -- every track in the last minute
    SELECT ts, frame_num, track_data
    FROM frames
    WHERE ts > now() - interval '1 minute' AND track_data IS NOT NULL
    ORDER BY ts;

    -- 1-second point-cloud buckets
    SELECT time_bucket('1 s', ts) AS bucket,
           avg(array_length(x, 1))                AS avg_points,
           max((track_data->0->>'tid')::int)      AS a_track_id
    FROM frames
    WHERE ts > now() - interval '5 minutes'
    GROUP BY bucket ORDER BY bucket;

Requires psycopg 3: ``pip install "psycopg[binary]"``.

Configuration comes from four layers, most specific first:

    CLI argument > environment variable > .env file > built-in default

| Option           | Environment variable     | Default |
|------------------|--------------------------|---------|
| ``--dsn``        | ``DATABASE_URL``         | (required) |
| ``--port``       | ``RADAR_PORT``           | 5005    |
| ``--bind``       | ``RADAR_BIND``           | 0.0.0.0 |
| ``--batch-size`` | ``RADAR_BATCH_SIZE``     | 50      |
| ``--flush-interval`` | ``RADAR_FLUSH_INTERVAL`` | 1.0  |

A ``.env`` file in the working directory is loaded automatically (missing is
fine); an explicit ``--env-file PATH`` that does not exist is an error.
Existing environment variables always win over ``.env`` values.

Usage:
    python3 radar_persist.py --dsn postgresql://user:pass@host/db [--port 5005]
"""

import argparse
import os
import socket
import sys
import time

try:
    import psycopg
except ImportError:  # pragma: no cover - plain usage error
    print("ERROR: psycopg is required: pip install 'psycopg[binary]'")
    sys.exit(1)

from frame_stream import FrameStream
from frame_store import FrameStore
from tlv_parser import parse_frame

# ---------- constants ----------

DEFAULT_PORT = 5005
DEFAULT_BIND = '0.0.0.0'
DEFAULT_BATCH_SIZE = 50
DEFAULT_FLUSH_INTERVAL = 1.0

#: Environment variable names for every option (see module docstring table).
ENV_DSN = 'DATABASE_URL'
ENV_PORT = 'RADAR_PORT'
ENV_BIND = 'RADAR_BIND'
ENV_BATCH_SIZE = 'RADAR_BATCH_SIZE'
ENV_FLUSH_INTERVAL = 'RADAR_FLUSH_INTERVAL'

#: Auto-loaded when present in the working directory.
DEFAULT_ENV_FILE = '.env'

RECV_BUFFER_SIZE = 65536

#: How long a recv may block; also drives the periodic flush timer.
SOCK_TIMEOUT = 0.5

#: Seconds to wait before retrying a failed database connection.
RECONNECT_DELAY = 2.0


def _unquote(value):
    """Strip one pair of matching surrounding quotes from a .env value."""
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
        return v[1:-1]
    return v


def load_env_file(path):
    """Set unset environment variables from a ``KEY=VALUE`` file.

    Blank lines, ``#`` comments and lines without ``=`` are skipped. Keys
    already present in the environment keep their value, so real environment
    variables always beat the file. Returns the list of keys loaded.
    """
    loaded = []
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, raw = line.partition('=')
            key = key.strip()
            if key and key not in os.environ:
                os.environ[key] = _unquote(raw)
                loaded.append(key)
    return loaded


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description='Receive radar frames over UDP and persist them to '
                    'PostgreSQL + TimescaleDB')
    parser.add_argument('--port', type=int, default=None,
                        help=f'UDP port to listen on '
                             f'(default: ${ENV_PORT} or {DEFAULT_PORT})')
    parser.add_argument('--bind', default=None,
                        help=f'Bind address '
                             f'(default: ${ENV_BIND} or {DEFAULT_BIND})')
    parser.add_argument('--dsn', default=None,
                        help=f'PostgreSQL DSN '
                             f'(default: ${ENV_DSN}; required from somewhere)')
    parser.add_argument('--batch-size', type=int, default=None,
                        help=f'Rows per INSERT transaction '
                             f'(default: ${ENV_BATCH_SIZE} or '
                             f'{DEFAULT_BATCH_SIZE})')
    parser.add_argument('--flush-interval', type=float, default=None,
                        help=f'Max seconds a complete frame may sit '
                             f'unwritten (default: ${ENV_FLUSH_INTERVAL} or '
                             f'{DEFAULT_FLUSH_INTERVAL})')
    parser.add_argument('--env-file', default=None,
                        help=f'KEY=VALUE config file to load before resolving '
                             f'defaults (default: {DEFAULT_ENV_FILE} in the '
                             f'working directory, if present)')
    args = parser.parse_args(argv)

    # .env: the implicit DEFAULT_ENV_FILE is optional, an explicit
    # --env-file that cannot be read is a usage error.
    env_path = args.env_file
    if env_path is None and os.path.exists(DEFAULT_ENV_FILE):
        env_path = DEFAULT_ENV_FILE
    if env_path is not None:
        try:
            load_env_file(env_path)
        except OSError as e:
            parser.error(f'cannot read --env-file {env_path!r}: {e}')

    def resolve(cli_value, env_key, default, cast=str):
        """CLI arg > environment variable > built-in default."""
        raw = cli_value if cli_value is not None else os.environ.get(env_key)
        if raw is None:
            return default
        if cast is not str:
            try:
                return cast(raw)
            except ValueError:
                parser.error(f'invalid ${env_key}={raw!r}')
        return raw

    args.port = resolve(args.port, ENV_PORT, DEFAULT_PORT, int)
    args.bind = resolve(args.bind, ENV_BIND, DEFAULT_BIND)
    args.batch_size = resolve(args.batch_size, ENV_BATCH_SIZE,
                              DEFAULT_BATCH_SIZE, int)
    args.flush_interval = resolve(args.flush_interval, ENV_FLUSH_INTERVAL,
                                  DEFAULT_FLUSH_INTERVAL, float)
    args.dsn = resolve(args.dsn, ENV_DSN, None)
    if not args.dsn:
        parser.error(f'--dsn, ${ENV_DSN} or a .env file is required')
    return args


def main(argv=None):
    args = parse_args(argv)

    store = FrameStore(args.dsn, batch_size=args.batch_size)
    try:
        store.connect()
    except psycopg.OperationalError as e:
        print(f"ERROR: cannot connect to database: {e}")
        return 1
    store.init_schema()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.bind, args.port))
    sock.settimeout(SOCK_TIMEOUT)

    print(f"Radar UDP persister listening on {args.bind}:{args.port}")
    print(f"  storing frames to {args.dsn.split('@')[-1]}")
    print("  (Ctrl+C to stop)\n")

    stream = FrameStream()
    frame_count = 0
    written = 0
    db_down = False
    last_flush = time.monotonic()

    try:
        while True:
            # Periodic flush: never hold a complete frame longer than
            # --flush-interval, even when the batch is not full yet.
            if (store.pending()
                    and time.monotonic() - last_flush >= args.flush_interval):
                try:
                    store.flush()
                    written += 1
                    last_flush = time.monotonic()
                    if db_down:
                        db_down = False
                        print("  database reconnected")
                except psycopg.Error as e:
                    if not db_down:
                        print(f"  database write failed ({e}); buffering, "
                              f"will retry every {RECONNECT_DELAY:.0f}s")
                        db_down = True
                    last_flush = time.monotonic()
                    store.disconnect()
                    time.sleep(RECONNECT_DELAY)
                    try:
                        store.connect()
                        store.init_schema()
                    except psycopg.Error:
                        pass  # retry on the next timer tick

            try:
                data, addr = sock.recvfrom(RECV_BUFFER_SIZE)
            except socket.timeout:
                continue

            for frame_data in stream.feed(data):
                parsed = parse_frame(frame_data)
                if parsed is None:
                    continue
                frame_count += 1
                if frame_count == 1:
                    print(f"*** First frame received (frame_num="
                          f"{parsed['frame_num']}, {parsed['num_points']} "
                          f"points) — persisting ***")
                try:
                    store.insert(parsed)
                except psycopg.Error as e:
                    # Buffering raced a dropped connection; reconnect path
                    # runs on the next timer tick.
                    if not db_down:
                        print(f"  database write failed ({e}); buffering")
                        db_down = True
                    store.disconnect()
                # No last_flush reset here: the timer must still be able to
                # flush a trickle of frames before the batch fills.

    except KeyboardInterrupt:
        print(f"\nStopping: {frame_count} frames received, "
              f"{written} batches written, {store.pending()} buffered.")
    finally:
        sock.close()
        try:
            store.close()
        except psycopg.Error as e:
            print(f"WARNING: final flush failed: {e}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
