#!/usr/bin/env python3
"""
Integration tests for frame_store.FrameStore and radar_persist.py against a
real PostgreSQL + TimescaleDB.

These verify the acceptance criteria of the persistence story:

- the ``frames`` table is a Timescale hypertable partitioned on ``ts``,
- each stored frame round-trips timestamp, frame number, point-cloud arrays,
  trackData, heightData and trackIndexes,
- a sample replay query (time-range + unnest) reads back what was written,
- the UDP consumer persists frames end-to-end, and keeps buffering when the
  database connection drops.

A local test database is provided by Docker:

    docker run -d --name lares-ts-test \\
        -e POSTGRES_PASSWORD=lares -e POSTGRES_DB=lares \\
        -p 54329:5432 timescale/timescaledb:latest-pg16

Tests auto-skip when neither ``TEST_DATABASE_URL`` nor the local Docker
database is reachable, so the rest of the suite runs on machines with no
database. The setup DROPS the ``frames`` table, so ``TEST_DATABASE_URL`` is
only honoured when it points at localhost. Run from the radar_pi/ directory:

    python3 -m unittest tests.test_frame_store_integration -v
"""

import datetime
import os
import socket
import subprocess
import sys
import time
import unittest
from urllib.parse import urlparse

import psycopg

from frame_store import FrameStore
from tests.test_fixtures import build_tracking_frame_bytes, oob_frame, tracking_frame

#: Local Docker database (see module docstring).
LOCAL_TEST_DSN = 'postgresql://postgres:lares@localhost:54329/lares'


def _test_dsn():
    """TEST_DATABASE_URL if it points at localhost, else the local Docker DSN.

    The setup below is destructive (DROP TABLE frames); a remote / production
    $TEST_DATABASE_URL is ignored so a stray env var cannot wipe real data.
    """
    env = os.environ.get('TEST_DATABASE_URL')
    if env:
        host = urlparse(env).hostname or 'localhost'
        if host in ('localhost', '127.0.0.1', '::1'):
            return env
    return LOCAL_TEST_DSN


TEST_DSN = _test_dsn()


def _connect_or_none():
    try:
        conn = psycopg.connect(TEST_DSN, connect_timeout=3)
        return conn
    except psycopg.OperationalError:
        return None


@unittest.skipUnless(_connect_or_none(), 'no test database reachable')
class FrameStoreIntegrationTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.conn = _connect_or_none()
        with cls.conn.cursor() as cur:
            cur.execute('DROP TABLE IF EXISTS frames')
        cls.conn.commit()  # release the ACCESS EXCLUSIVE lock before tests

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def setUp(self):
        self.store = FrameStore(TEST_DSN)
        self.store.connect()
        self.store.init_schema()
        with self.conn.cursor() as cur:
            cur.execute('TRUNCATE frames')
        self.conn.commit()

    def tearDown(self):
        self.store.close()

    def _query(self, sql, params=None):
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall()
        except Exception:
            self.conn.rollback()  # don't poison later tests' transactions
            raise

    def test_frames_table_is_a_hypertable_partitioned_on_ts(self):
        rows = self._query(
            "SELECT hypertable_name FROM timescaledb_information.hypertables "
            "WHERE hypertable_name = 'frames'")
        self.assertEqual(rows, [('frames',)])
        dims = self._query(
            "SELECT column_name FROM timescaledb_information.dimensions "
            "WHERE hypertable_name = 'frames'")
        self.assertEqual(dims, [('ts',)])

    def test_tracking_frame_round_trips(self):
        ts = datetime.datetime(2025, 8, 14, 12, 0, 0,
                               tzinfo=datetime.timezone.utc)
        self.store.insert(tracking_frame(), ts)
        self.store.flush()

        rows = self._query(
            'SELECT ts, frame_num, num_points, x, y, z, doppler, snr, '
            'track_data, height_data, track_indexes '
            'FROM frames ORDER BY ts')
        self.assertEqual(len(rows), 1)
        (r_ts, r_frame_num, r_num_points, r_x, r_y, r_z, r_doppler, r_snr,
         r_track_data, r_height_data, r_track_indexes) = rows[0]
        self.assertEqual(r_ts, ts)
        self.assertEqual(r_frame_num, 4242)
        self.assertEqual(r_num_points, 2)
        self.assertEqual(r_x, [1.0, 3.0])
        self.assertEqual(r_y, [2.0, 4.0])
        self.assertEqual(r_z, [0.5, 0.9])
        self.assertEqual(r_doppler, [0.2, -0.1])
        self.assertEqual(r_snr, [12.0, 9.5])
        self.assertEqual(r_track_data[0]['tid'], 3)
        self.assertEqual(r_track_data[0]['position'], [0.1, 1.2, 0.8])
        self.assertEqual(r_track_data[0]['confidence'], 0.9)
        self.assertEqual(r_height_data[0]['max_z'], 1.7)
        self.assertEqual(r_track_indexes, [3, 255])

    def test_oob_frame_stores_null_tracking_columns(self):
        self.store.insert(oob_frame())
        self.store.flush()

        rows = self._query(
            'SELECT track_data, height_data, track_indexes, x FROM frames')
        self.assertEqual(rows, [(None, None, None, [1.0])])

    def test_replay_query_recent_frames_with_tracks(self):
        """Sample query: every frame with tracks in the last minute."""
        now = datetime.datetime.now(datetime.timezone.utc)
        for fn in range(10):
            self.store.insert(tracking_frame(frame_num=fn), now)
        self.store.flush()

        rows = self._query(
            "SELECT frame_num, track_data->0->>'tid' AS first_track "
            "FROM frames "
            "WHERE ts > now() - interval '1 minute' AND track_data IS NOT NULL "
            "ORDER BY frame_num")
        self.assertEqual(len(rows), 10)
        self.assertEqual(rows[0], (0, '3'))

    def test_point_replay_query_unnests_arrays(self):
        """Sample replay: unnest point arrays into per-point rows."""
        now = datetime.datetime.now(datetime.timezone.utc)
        self.store.insert(tracking_frame(frame_num=0), now)
        self.store.flush()

        rows = self._query(
            "SELECT p.x, p.y, p.z, p.doppler, p.track_id "
            "FROM frames, "
            "unnest(x, y, z, doppler, track_indexes) "
            "AS p(x, y, z, doppler, track_id) "
            "WHERE ts > now() - interval '1 minute' ORDER BY p.x")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], 1.0)
        self.assertEqual(rows[0][4], 3)   # smallint[] element
        self.assertEqual(rows[1][4], 255)

    def test_batching_writes_once_per_batch(self):
        for fn in range(5):
            self.store.insert(tracking_frame(frame_num=fn))
            self.assertEqual(self.store.pending(), fn + 1)
        self.store.flush()
        self.assertEqual(self.store.pending(), 0)
        count = self._query('SELECT count(*) FROM frames')[0][0]
        self.assertEqual(count, 5)

    def test_flush_without_connection_keeps_rows_buffered(self):
        """DB outage: flush raises a psycopg error, rows stay buffered."""
        for fn in range(2):
            self.store.insert(tracking_frame(frame_num=fn))
        self.store.disconnect()
        with self.assertRaises(psycopg.Error):
            self.store.flush()
        self.assertEqual(self.store.pending(), 2)
        # Reconnect: the buffered rows still land.
        self.store.connect()
        self.store.flush()
        count = self._query('SELECT count(*) FROM frames')[0][0]
        self.assertEqual(count, 2)


@unittest.skipUnless(_connect_or_none(), 'no test database reachable')
class RadarPersistEndToEndTests(unittest.TestCase):
    """Feed the real UDP consumer, read rows back from Postgres."""

    @classmethod
    def setUpClass(cls):
        cls.conn = _connect_or_none()
        with cls.conn.cursor() as cur:
            cur.execute('DROP TABLE IF EXISTS frames')
        cls.conn.commit()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def _start_consumer(self, port):
        here = os.path.dirname(os.path.abspath(__file__))          # tests/
        radar_pi = os.path.dirname(here)                           # radar_pi/
        return subprocess.Popen(
            [sys.executable, os.path.join(radar_pi, 'radar_persist.py'),
             '--port', str(port), '--dsn', TEST_DSN,
             '--batch-size', '5', '--flush-interval', '0.5'],
            cwd=radar_pi,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def _wait_for_listener(self, port):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.sendto(b'', ('127.0.0.1', port))
                return
            except OSError:
                time.sleep(0.2)

    def _free_udp_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind(('127.0.0.1', 0))
            return s.getsockname()[1]

    def _wait_for_db_up(self, timeout=30):
        """Reconnect the class-level conn after the database restarts."""
        cls = type(self)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                cls.conn.close()
            except Exception:
                pass
            try:
                cls.conn = psycopg.connect(TEST_DSN, connect_timeout=2)
                with cls.conn.cursor() as cur:
                    cur.execute('SELECT 1')
                return
            except psycopg.Error:
                time.sleep(1.0)
        self.fail('database did not come back up')

    def _bootstrap_and_truncate(self):
        """Ensure the schema exists and the table is empty for this test."""
        bootstrap = FrameStore(TEST_DSN)
        bootstrap.connect()
        bootstrap.init_schema()
        bootstrap.close()
        with self.conn.cursor() as cur:
            cur.execute('TRUNCATE frames')
        self.conn.commit()

    def test_udp_frames_persist_to_database(self):
        self._bootstrap_and_truncate()
        port = self._free_udp_port()
        proc = self._start_consumer(port)
        try:
            self._wait_for_listener(port)
            time.sleep(0.5)

            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                for fn in (100, 101, 102, 103, 104, 105):
                    s.sendto(build_tracking_frame_bytes(fn),
                             ('127.0.0.1', port))
                    time.sleep(0.05)

            # Batch (5) + timer (0.5s) flushes must have landed the rows.
            deadline = time.monotonic() + 10
            rows = []
            while time.monotonic() < deadline:
                with self.conn.cursor() as cur:
                    cur.execute(
                        'SELECT frame_num, num_points, '
                        'track_data->0->>\'tid\' AS tid, '
                        'jsonb_array_length(track_data) AS n_tracks '
                        'FROM frames ORDER BY frame_num')
                    rows = cur.fetchall()
                if len(rows) == 6:
                    break
                time.sleep(0.5)
        finally:
            proc.terminate()
            try:
                out = proc.communicate(timeout=5)[0]
            except subprocess.TimeoutExpired:
                proc.kill()
                out = proc.communicate()[0]
            if proc.returncode not in (0, None, 1):
                self.fail(f'radar_persist exited {proc.returncode}:\n'
                          f'{out.decode(errors="replace")}')

        self.assertEqual([r[0] for r in rows], [100, 101, 102, 103, 104, 105])
        for r in rows:
            self.assertEqual(r[1], 3)    # num_points
            self.assertEqual(r[2], '7')  # track id as JSONB text
            self.assertEqual(r[3], 1)    # one track

    def test_consumer_survives_database_outage(self):
        """Frames keep buffering while the DB is down, land after restart."""
        self._bootstrap_and_truncate()
        port = self._free_udp_port()
        proc = self._start_consumer(port)
        try:
            self._wait_for_listener(port)
            time.sleep(0.5)

            # Kill the database from under the consumer.
            subprocess.run(['docker', 'stop', 'lares-ts-test'],
                           capture_output=True)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.sendto(build_tracking_frame_bytes(200),
                         ('127.0.0.1', port))
            time.sleep(2.0)  # consumer notices + buffers, must not crash

            self.assertIsNone(proc.poll(),
                              'consumer crashed during DB outage')

            subprocess.run(['docker', 'start', 'lares-ts-test'],
                           capture_output=True)
            self._wait_for_db_up()
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.sendto(build_tracking_frame_bytes(201),
                         ('127.0.0.1', port))

            deadline = time.monotonic() + 30
            count = 0
            while time.monotonic() < deadline:
                try:
                    with self.conn.cursor() as cur:
                        cur.execute('SELECT count(*) FROM frames')
                        count = cur.fetchone()[0]
                    self.conn.commit()
                except psycopg.Error:
                    self.conn.rollback()
                    time.sleep(1.0)
                    continue
                if count == 2:
                    break
                time.sleep(1.0)
        finally:
            proc.terminate()
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()

        self.assertEqual(count, 2,
                         'both frames should persist after DB restart')


if __name__ == '__main__':
    unittest.main()
