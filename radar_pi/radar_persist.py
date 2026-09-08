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

Requires psycopg 3: ``pip install "psycopg[binary]"``. Usage:
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
RECV_BUFFER_SIZE = 65536

#: How long a recv may block; also drives the periodic flush timer.
SOCK_TIMEOUT = 0.5

#: Seconds to wait before retrying a failed database connection.
RECONNECT_DELAY = 2.0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description='Receive radar frames over UDP and persist them to '
                    'PostgreSQL + TimescaleDB')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT,
                        help=f'UDP port to listen on (default: {DEFAULT_PORT})')
    parser.add_argument('--bind', default='0.0.0.0',
                        help='Bind address (default: 0.0.0.0)')
    parser.add_argument('--dsn', default=os.environ.get('DATABASE_URL'),
                        help='PostgreSQL DSN '
                             '(default: $DATABASE_URL)')
    parser.add_argument('--batch-size', type=int, default=50,
                        help='Rows per INSERT transaction (default: 50)')
    parser.add_argument('--flush-interval', type=float, default=1.0,
                        help='Max seconds a complete frame may sit '
                             'unwritten (default: 1.0)')
    args = parser.parse_args(argv)
    if not args.dsn:
        parser.error('--dsn or $DATABASE_URL is required')
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
