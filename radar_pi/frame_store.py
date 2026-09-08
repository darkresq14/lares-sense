#!/usr/bin/env python3
"""
Durable storage for parsed radar frames in PostgreSQL + TimescaleDB.

The server-side persister (``radar_persist.py``) writes every frame decoded by
``tlv_parser.parse_frame`` into a single ``frames`` table, converted to a
Timescale **hypertable** partitioned on the ingest timestamp ``ts``. Point
clouds are a natural time-series: append-only, high rate, queried by time
range.

Schema shape (one row per frame):

    ts            TIMESTAMPTZ  ingest time (hypertable partition key)
    frame_num     INTEGER      header frame counter (wraps; not unique)
    num_points    INTEGER      header numDetectedObj
    x, y, z,
    doppler, snr  REAL[]       parallel per-point arrays
    track_data    JSONB        trackData  (TLV 1010), NULL when absent
    height_data   JSONB        heightData (TLV 1012), NULL when absent
    track_indexes SMALLINT[]   trackIndexes (TLV 1011), NULL when absent

Tracking outputs have variable structure (covariance matrices, per-track
dicts), so they land as JSONB; the point cloud is fixed-shape and stays in
typed arrays for compact storage and cheap ``unnest`` queries.

NULL semantics: a TLV absent from the frame stores NULL in its JSONB /
``track_indexes`` column. The parser reports both "TLV absent" and
"TLV present but empty" as an empty list, so NULL means "no tracking output
stored for this frame" either way; the point arrays are always present,
possibly empty, so array queries never meet NULLs.

Writes are batched: ``insert()`` buffers rows, ``flush()`` sends one
multi-row ``INSERT`` per transaction, keeping up with the live frame rate
(10 fps default) with one round-trip per batch instead of per frame.

Requires psycopg 3: ``pip install "psycopg[binary]"``.
"""

import datetime
import json

import psycopg

#: DDL applied idempotently by :meth:`FrameStore.init_schema`.
SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS frames (
    ts            TIMESTAMPTZ NOT NULL,
    frame_num     INTEGER     NOT NULL,
    num_points    INTEGER     NOT NULL,
    x             REAL[]      NOT NULL DEFAULT '{}',
    y             REAL[]      NOT NULL DEFAULT '{}',
    z             REAL[]      NOT NULL DEFAULT '{}',
    doppler       REAL[]      NOT NULL DEFAULT '{}',
    snr           REAL[]      NOT NULL DEFAULT '{}',
    track_data    JSONB,
    height_data   JSONB,
    track_indexes SMALLINT[]
);

SELECT create_hypertable('frames', 'ts',
                         if_not_exists => TRUE, migrate_data => TRUE);

CREATE INDEX IF NOT EXISTS frames_frame_num_idx ON frames (frame_num);
"""

_INSERT_SQL = """
INSERT INTO frames
    (ts, frame_num, num_points, x, y, z, doppler, snr,
     track_data, height_data, track_indexes)
VALUES (%(ts)s, %(frame_num)s, %(num_points)s, %(x)s, %(y)s, %(z)s,
        %(doppler)s, %(snr)s, %(track_data)s::jsonb,
        %(height_data)s::jsonb, %(track_indexes)s)
"""
# JSONB columns are passed as plain JSON strings cast in SQL: psycopg's
# Jsonb adapter would re-encode a pre-serialised string.


def frame_to_row(parsed, ts):
    """Convert a parsed frame dict into ``frames`` column values.

    ``parsed`` is the dict ``tlv_parser.parse_frame`` returns; ``ts`` is the
    ingest timestamp. Returns a dict keyed by column name; JSONB columns are
    plain JSON strings (cast to ``jsonb`` in ``_INSERT_SQL``) so the mapping
    stays testable without a database driver.
    """
    points = parsed.get('points') or []
    x = [p[0] for p in points]
    y = [p[1] for p in points]
    z = [p[2] for p in points]
    doppler = [p[3] for p in points]
    snr = list(parsed.get('snr') or [])

    # Absent TLV -> NULL. The parser cannot distinguish an absent TLV from a
    # present-but-empty one (both give []), so NULL means "no tracking output
    # stored for this frame", covering both cases.
    tracks = parsed.get('tracks') or None
    heights = parsed.get('track_heights') or None
    indexes = parsed.get('track_indexes') or None

    return {
        'ts': ts,
        'frame_num': parsed['frame_num'],
        'num_points': parsed['num_points'],
        'x': x, 'y': y, 'z': z, 'doppler': doppler, 'snr': snr,
        'track_data': json.dumps(tracks) if tracks is not None else None,
        'height_data': json.dumps(heights) if heights is not None else None,
        'track_indexes': list(indexes) if indexes is not None else None,
    }


class FrameStore:
    """Batched writer for parsed frames into the ``frames`` hypertable.

    ``insert(parsed, ts=None)`` buffers one row (``ts`` defaults to now, UTC).
    Rows are written in batches: either when the buffer reaches ``batch_size``
    rows or when ``flush()`` is called (the persister calls it on a timer), so
    the write rate to Postgres is one round-trip per batch regardless of the
    frame rate.
    """

    def __init__(self, dsn, batch_size=50):
        self._dsn = dsn
        self.batch_size = batch_size
        self._conn = None
        self._pending = []

    # --- lifecycle ---

    def connect(self):
        """Open the Postgres connection."""
        self._conn = psycopg.connect(self._dsn)

    def close(self):
        """Flush pending rows, then close the connection."""
        try:
            self.flush()
        finally:
            self.disconnect()

    def disconnect(self):
        """Close the connection without flushing (for reconnect paths)."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # --- schema ---

    def init_schema(self):
        """Create the TimescaleDB extension, table and hypertable."""
        with self._conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        self._conn.commit()

    # --- writes ---

    def insert(self, parsed, ts=None):
        """Buffer one parsed frame; auto-flush when the batch is full."""
        if ts is None:
            ts = datetime.datetime.now(datetime.timezone.utc)
        row = frame_to_row(parsed, ts)
        self._pending.append(row)
        if len(self._pending) >= self.batch_size:
            self.flush()

    def flush(self):
        """Write all buffered rows in one transaction.

        On failure the rows are put back at the front of the buffer and the
        exception propagates, so the caller can reconnect and retry without
        losing buffered frames.
        """
        if not self._pending:
            return
        if self._conn is None:
            # Not connected (e.g. reconnect pending): raise a psycopg error so
            # callers' psycopg.Error handlers catch it, rows stay buffered.
            raise psycopg.OperationalError('FrameStore is not connected')
        rows, self._pending = self._pending, []
        try:
            with self._conn.cursor() as cur:
                cur.executemany(_INSERT_SQL, rows)
            self._conn.commit()
        except Exception:
            self._pending = rows + self._pending
            raise

    def pending(self):
        """Number of buffered rows not yet written."""
        return len(self._pending)
