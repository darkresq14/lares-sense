#!/usr/bin/env python3
"""
Seam tests for the two-stage machine, UDP fan-out and boot self-recovery
(issue #12).

The Setup Tool's stages (ADR-0004): **Config Stage** — everything editable,
frames reach the browsers but **UDP to the server is suppressed**; **Running
Stage** — config read-only, frames fan out to both browsers (WS) and the
server (UDP, raw complete TI frames — the protocol `radar_sender.py` uses
today, so the persist path is untouched). Start applies the persisted
Working Config and enters Running only once frames resumed; Stop suppresses
UDP first, then issues sensorStop and unlocks the editor. On boot the
process auto-enters Running when a persisted Working Config exists
(headless self-recovery), else enters Config Stage.

All of it is driven through the one testing seam (ADR-0004): the serial
device boundary plus real loopback WS clients and a real loopback UDP
receiver — the same infrastructure a deployment uses.

Run from the radar_pi/ directory:

    python3 -m unittest tests.test_setup_tool_stages -v
"""

import contextlib
import io
import json
import socket
import tempfile
import threading
import time
import unittest

from websockets.sync.client import connect

from read_radar import CLI_BAUD, DATA_BAUD
from setup_tool.config_model import cfg_command_lines
from setup_tool.config_store import WorkingConfigStore
from setup_tool.host import SetupToolHost
from tlv_parser import parse_frame
from tests.test_fixtures import build_tracking_frame_bytes
from tests.test_setup_tool_host import FakeSerialDevice

#: The host's frames-resumed timeout in these tests (real default: 15 s).
HOST_FAST_TIMEOUT = 0.8


class FrameFeeder:
    """Pump synthetic frames into the fake device until stopped.

    A continuously-streaming sensor: bytes keep arriving no matter what the
    host does to the port, so boot/start sequences that flush the input
    buffer mid-flight still see frames afterwards.
    """

    def __init__(self, device, first_num=1, interval=0.03):
        self._device = device
        self._next = first_num
        self._interval = interval
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def _run(self):
        while not self._stop.is_set():
            self._device.feed(build_tracking_frame_bytes(
                self._next, tid=7, n_points=3))
            self._next += 1
            self._stop.wait(self._interval)

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)


class SetupToolStageTests(unittest.TestCase):

    def make_host(self, data_dir=None):
        """A host on loopback with a real loopback UDP receiver wired in."""
        if data_dir is None:
            data_dir = tempfile.mkdtemp(prefix='setup-tool-stage-')
        self.data_dir = data_dir
        self.device = FakeSerialDevice()
        self.device.timeout = 0.05
        self.device.baudrate = DATA_BAUD
        self.udp_rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_rx.bind(('127.0.0.1', 0))
        self.udp_rx.settimeout(5)
        self.host = SetupToolHost(
            self.device, bind='127.0.0.1', port=0, config_dir=data_dir,
            udp_target=self.udp_rx.getsockname(),
            apply_command_delay=0.0, apply_frames_timeout=HOST_FAST_TIMEOUT)
        self.host.start()
        self.addCleanup(self.host.stop)
        self.addCleanup(self.udp_rx.close)
        self.port = self.host.actual_port
        return self.host

    # ---- helpers ----------------------------------------------------------

    def ws_connect(self, port=None):
        return connect(f'ws://127.0.0.1:{port or self.port}/', open_timeout=5)

    def udp_recv(self, timeout=5):
        """Next datagram from the loopback receiver (None on timeout)."""
        self.udp_rx.settimeout(timeout)
        try:
            return self.udp_rx.recv(65536)
        except socket.timeout:
            return None

    def udp_drain(self):
        """Empty the UDP receiver; returns number of datagrams dropped."""
        self.udp_rx.settimeout(0)
        count = 0
        try:
            while True:
                self.udp_rx.recv(65536)
                count += 1
        except (BlockingIOError, socket.timeout):
            pass
        finally:
            self.udp_rx.settimeout(5)
        return count

    def recv_of_type(self, ws, type_, timeout=5):
        """Next WS message of a given type; frames and other events skipped."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            self.assertGreater(remaining, 0, f'{type_} reply never came')
            message = json.loads(ws.recv(timeout=remaining))
            if message.get('type') == type_:
                return message

    def get_values(self, ws):
        ws.send(json.dumps({'type': 'config_get'}))
        return self.recv_of_type(ws, 'config')['values']

    def wait_stage(self, ws, stage, health, timeout=5):
        """Next ``stage`` event matching both stage and health."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            self.assertGreater(remaining, 0, f'stage {stage}/{health} never came')
            message = json.loads(ws.recv(timeout=remaining))
            if (message.get('type') == 'stage'
                    and message.get('stage') == stage
                    and message.get('health') == health):
                return message

    def expected_cfg_bytes(self):
        """The full blind write sequence for the persisted Working Config."""
        text = WorkingConfigStore(data_dir=self.data_dir).load()
        return ''.join(l + '\n' for l in cfg_command_lines(text)).encode('ascii')

    def assert_full_config_sequence_written(self):
        """The exact cfg bytes left for the device, at CLI baud, in order."""
        self.assertEqual(bytes(self.device.written),
                         self.expected_cfg_bytes())
        baud_ops = [(i, op) for i, op in enumerate(self.device.ops)
                    if op[0] == 'baud']
        self.assertEqual([op for _, op in baud_ops][-2:],
                         [('baud', CLI_BAUD), ('baud', DATA_BAUD)])
        first_write = next(i for i, op in enumerate(self.device.ops)
                           if op[0] == 'write')
        last_write = max(i for i, op in enumerate(self.device.ops)
                         if op[0] == 'write')
        self.assertLess(baud_ops[-2][0], first_write)   # CLI baud first
        self.assertLess(last_write, baud_ops[-1][0])    # data baud last

    # ---- Start → Running Stage ----------------------------------------------

    def test_start_fans_out_udp_raw_frames_and_locks_editor(self):
        self.make_host()
        feeder = FrameFeeder(self.device, first_num=1000).start()
        self.addCleanup(feeder.stop)

        with self.ws_connect() as ws:
            # The connect snapshot arrives before anything else.
            snapshot = json.loads(ws.recv(timeout=5))
            self.assertEqual(snapshot['type'], 'stage')

            # Config Stage: frames reach the page but never the server.
            message = json.loads(ws.recv(timeout=5))
            self.assertEqual(message['type'], 'frame')
            self.assertEqual(self.udp_drain(), 0)

            self.device.ops.clear()
            del self.device.written[:]
            ws.send(json.dumps({'type': 'start'}))
            self.wait_stage(ws, stage='running', health='streaming')

            # UDP datagrams are raw complete TI frames — the receiver
            # parses them exactly like radar_persist does today.
            datagram = self.udp_recv()
            self.assertIsNotNone(datagram)
            self.assertIsNotNone(parse_frame(datagram))

            # The blind apply ran: full cfg at CLI baud, port back at data.
            self.assert_full_config_sequence_written()

            # The editor is read-only in Running Stage.
            ws.send(json.dumps({'type': 'config_set', 'values': {}}))
            locked = self.recv_of_type(ws, 'stage', timeout=5)
            self.assertEqual(locked['stage'], 'running')
            self.assertTrue(locked.get('locked'))
            self.assertEqual(bytes(self.device.written),
                             self.expected_cfg_bytes())

    def test_start_failure_keeps_config_stage_and_editor_usable(self):
        self.make_host()      # no feeder: the "sensor" never emits
        with self.ws_connect() as ws:
            ws.send(json.dumps({'type': 'start'}))
            failed = self.wait_stage(ws, stage='config', health='error')
            self.assertIn('frame', failed.get('reason', '').lower())

            # The editor still works; the port sits at data baud.
            values = self.get_values(ws)
            ws.send(json.dumps({'type': 'config_set', 'values': values}))
            self.assertEqual(self.recv_of_type(ws, 'config_saved')['type'],
                             'config_saved')
            self.assertEqual(self.device.baudrate, DATA_BAUD)

    # ---- Stop → Config Stage --------------------------------------------------

    def test_stop_suppresses_udp_stops_sensor_unlocks_editor(self):
        self.make_host()
        feeder = FrameFeeder(self.device, first_num=2000).start()
        self.addCleanup(feeder.stop)

        with self.ws_connect() as ws:
            ws.send(json.dumps({'type': 'start'}))
            self.wait_stage(ws, stage='running', health='streaming')
            self.udp_drain()

            ws.send(json.dumps({'type': 'stop'}))
            self.wait_stage(ws, stage='config', health='stopped')

            # The sensor was stopped: sensorStop at CLI baud, then the
            # port went back to data baud.
            self.assertTrue(self.device.written.endswith(b'sensorStop\n'))
            bauds = [op[1] for op in self.device.ops if op[0] == 'baud']
            self.assertEqual(bauds[-2:], [CLI_BAUD, DATA_BAUD])

            # Frames (if any) reach the page but never the server again.
            # One straggler datagram may still land — a frame already
            # parsed while Running — so settle first, then demand silence.
            self.udp_drain()
            time.sleep(0.3)
            self.assertEqual(self.udp_drain(), 0)

            # The editor is unlocked: a save round-trips.
            values = self.get_values(ws)
            ws.send(json.dumps({'type': 'config_set', 'values': values}))
            self.assertEqual(self.recv_of_type(ws, 'config_saved')['type'],
                             'config_saved')

    # ---- boot self-recovery (issue #12's headline) ---------------------------

    def test_boot_with_persisted_config_auto_runs_udp_no_browser(self):
        # A previous session left a Working Config behind (power blip).
        data_dir = tempfile.mkdtemp(prefix='setup-tool-boot-')
        WorkingConfigStore(data_dir=data_dir).load()

        self.make_host(data_dir=data_dir)
        feeder = FrameFeeder(self.device, first_num=3000).start()
        self.addCleanup(feeder.stop)

        # No browser ever connects: UDP flowing is the observable.
        datagram = self.udp_recv()
        self.assertIsNotNone(datagram)
        self.assertIsNotNone(parse_frame(datagram))

        # Boot applied the persisted Working Config, full blind sequence.
        self.assertEqual(bytes(self.device.written),
                         self.expected_cfg_bytes())

    def test_boot_auto_start_failure_logs_and_stays_config_stage(self):
        # Persisted Working Config but the sensor never emits (loose
        # connector, dead sensor): the headless auto-enter must fail
        # loudly in the journal — no browser exists to see the WS error.
        data_dir = tempfile.mkdtemp(prefix='setup-tool-bootfail-')
        WorkingConfigStore(data_dir=data_dir).load()

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.make_host(data_dir=data_dir)   # no feeder: silent sensor
            time.sleep(HOST_FAST_TIMEOUT + 1.0)

        self.assertIn('boot auto-enter of Running Stage failed',
                      stderr.getvalue())
        with self.ws_connect() as ws:
            snapshot = json.loads(ws.recv(timeout=5))
        self.assertEqual(snapshot['stage'], 'config')
        self.assertEqual(snapshot['health'], 'error')
        # Nothing was ever recorded.
        self.assertEqual(self.udp_drain(), 0)

    def test_boot_without_persisted_config_enters_config_stage(self):
        self.make_host()      # fresh data dir: nothing persisted yet

        with self.ws_connect() as ws:
            # The first message is the stage snapshot.
            snapshot = json.loads(ws.recv(timeout=5))
            self.assertEqual(snapshot['type'], 'stage')
            self.assertEqual(snapshot['stage'], 'config')
            self.assertEqual(snapshot['health'], 'stopped')

        # The sensor was not touched at boot.
        self.assertEqual(bytes(self.device.written), b'')

        # Frames still reach browsers (tuning view), never the server.
        feeder = FrameFeeder(self.device, first_num=4000).start()
        self.addCleanup(feeder.stop)
        with self.ws_connect() as ws:
            ws.recv(timeout=5)                      # snapshot
            message = json.loads(ws.recv(timeout=5))
            self.assertEqual(message['type'], 'frame')
        self.assertEqual(self.udp_drain(), 0)

    # ---- stage + sensor health ride the WS stream ----------------------------

    def test_stage_and_health_events_reach_connected_page(self):
        self.make_host()

        with self.ws_connect() as ws:
            snapshot = json.loads(ws.recv(timeout=5))
            self.assertEqual(snapshot, {'type': 'stage',
                                        'stage': 'config',
                                        'health': 'stopped'})

            feeder = FrameFeeder(self.device, first_num=5000).start()
            self.addCleanup(feeder.stop)

            ws.send(json.dumps({'type': 'start'}))
            self.wait_stage(ws, stage='running', health='streaming')

            ws.send(json.dumps({'type': 'stop'}))
            self.wait_stage(ws, stage='config', health='stopped')

    def test_page_carries_stage_controls(self):
        """ADR-0004: browser UI is manual-verified; the seam asserts serving."""
        import urllib.request
        self.make_host()
        with urllib.request.urlopen(
                f'http://127.0.0.1:{self.port}/', timeout=5) as resp:
            page = resp.read().decode('utf-8')
        with urllib.request.urlopen(
                f'http://127.0.0.1:{self.port}/app.js', timeout=5) as resp:
            app = resp.read().decode('utf-8')

        for marker in ('id="start"', 'id="stop"', 'id="stage-line"'):
            self.assertIn(marker, page)
        self.assertIn("'stage'", app)
        for health in ('streaming', 'stopped', 'reconfiguring', 'error'):
            self.assertIn(f"'{health}'", app)


if __name__ == '__main__':
    unittest.main()
