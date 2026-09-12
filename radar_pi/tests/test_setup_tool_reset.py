#!/usr/bin/env python3
"""Seam tests for the sensor reset line — stop/start robustness.

Grounded in measurements on the real rig (2026-09-12, ADR-0004
amendment): the IWR6843AOP on the uRAD single-UART adapter never
restarts after a ``sensorStop`` issued **while streaming** — a bare
``sensorStart``, a full ``flushCfg`` reconfig and 16 s of patience all
stay silent. Only pulsing the RST line (the uRAD adapter wires it to
GPIO5) revives it, after which the full cfg streams again.

So the host pulses reset instead of ever sending ``sensorStop`` when a
reset line is injected; without one it keeps the legacy sequence — and
the known wedge, documented by a test.

Run from the radar_pi/ directory:

    python3 -m unittest tests.test_setup_tool_reset -v
"""

import json
import socket
import tempfile
import threading
import time
import unittest

from websockets.sync.client import connect

from setup_tool.config_store import WorkingConfigStore
from setup_tool.host import SetupToolHost
from tests.test_fixtures import build_tracking_frame_bytes
from tests.test_setup_tool_host import FakeSerialDevice

#: Host timeouts shrunk for tests (real defaults: 15 s / 2.5 s).
FRAMES_TIMEOUT = 1.5
BOOT_SETTLE = 0.05


class WedgingSensorDevice(FakeSerialDevice):
    """The measured IWR6843AOP behavior, as a device.

    - fresh (power-up or after ``revive``): stopped, unconfigured —
      accepts a full cfg and streams on ``sensorStart``;
    - ``sensorStop`` **while streaming**: stops streaming and wedges —
      from here no command sequence revives it (all ignored) until
      ``revive`` (the RST pulse);
    - ``sensorStop`` while already stopped: harmless no-op;
    - ``flushCfg`` drops the loaded config; parameter lines arm it.
    """

    def __init__(self):
        super().__init__()
        self.streaming = False
        self.wedged = False
        self.configured = False
        self._line_buf = bytearray()
        self._stop_evt = threading.Event()
        self._feeder = threading.Thread(target=self._feed_loop,
                                        daemon=True)
        self._feeder.start()

    def _feed_loop(self):
        n = 0
        while not self._stop_evt.is_set():
            if self.streaming:
                self.feed(build_tracking_frame_bytes(n, tid=7, n_points=3))
                n += 1
            self._stop_evt.wait(0.03)

    def revive(self):
        """The RST pulse lands here: back to fresh boot state."""
        self.wedged = False
        self.streaming = False
        self.configured = False

    def write(self, data):
        super().write(data)
        self._line_buf.extend(bytes(data))
        while b'\n' in self._line_buf:
            line, _, rest = bytes(self._line_buf).partition(b'\n')
            self._line_buf = bytearray(rest)
            self._handle(line.decode('ascii', 'replace').strip())

    def _handle(self, line):
        if line == 'sensorStop':
            if self.streaming:
                self.streaming = False
                self.wedged = True       # the one-way door (measured)
        elif line == 'flushCfg':
            self.configured = False
        elif line == 'sensorStart':
            if not self.wedged and self.configured:
                self.streaming = True
        elif line:
            self.configured = True

    def shutdown(self):
        self._stop_evt.set()
        self._feeder.join(timeout=5)
        self.close()


class FakeSensorReset:
    """The reset line seam: ``pulse()`` revives the wedging device."""

    def __init__(self, device):
        self._device = device
        self.pulses = 0

    def pulse(self):
        self.pulses += 1
        self._device.revive()

    def close(self):
        pass


class SensorResetTests(unittest.TestCase):

    def make_host(self, with_reset=True, data_dir=None):
        if data_dir is None:
            data_dir = tempfile.mkdtemp(prefix='setup-tool-reset-')
        WorkingConfigStore(data_dir=data_dir).load()
        self.device = WedgingSensorDevice()
        self.device.timeout = 0.05
        self.addCleanup(self.device.shutdown)
        self.reset = FakeSensorReset(self.device) if with_reset else None
        self.udp_rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_rx.bind(('127.0.0.1', 0))
        self.udp_rx.settimeout(5)
        self.addCleanup(self.udp_rx.close)
        self.host = SetupToolHost(
            self.device, bind='127.0.0.1', port=0, config_dir=data_dir,
            udp_target=self.udp_rx.getsockname(),
            apply_command_delay=0.0, apply_frames_timeout=FRAMES_TIMEOUT,
            sensor_reset=self.reset,
            reset_boot_settle=BOOT_SETTLE)
        self.host.start()
        self.addCleanup(self.host.stop)
        self.port = self.host.actual_port
        return self.host

    # ---- helpers ----------------------------------------------------------

    def ws_connect(self):
        return connect(f'ws://127.0.0.1:{self.port}/', open_timeout=5)

    def udp_recv(self, timeout=5):
        self.udp_rx.settimeout(timeout)
        try:
            return self.udp_rx.recv(65536)
        except socket.timeout:
            return None

    def wait_host(self, stage, health, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.host.stage == stage and self.host.health == health:
                return True
            time.sleep(0.02)
        return False

    def recv_stage(self, ws, stage, health, timeout=8):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            try:
                m = json.loads(ws.recv(
                    timeout=max(0.05, deadline - time.monotonic())))
            except TimeoutError:
                break
            if m.get('type') == 'stage':
                last = m
                if m.get('stage') == stage and m.get('health') == health:
                    return m
        return last

    # ---- the reset line in the stage machine -------------------------------

    def test_boot_auto_start_resets_instead_of_sensorStop(self):
        self.make_host()
        self.assertTrue(self.wait_host('running', 'streaming'),
                        f'boot auto-start stuck at '
                        f'{self.host.stage}/{self.host.health}')
        self.assertIsNotNone(self.udp_recv(), 'UDP frames flow')
        self.assertEqual(self.reset.pulses, 1,
                         'exactly one reset pulse for boot auto-start')
        self.assertNotIn(b'sensorStop', bytes(self.device.written),
                         'the reset path never sends sensorStop')

    def test_page_stop_start_cycle_survives_the_wedge(self):
        """The user's bug: first start works, stop/start again breaks."""
        self.make_host()
        self.assertTrue(self.wait_host('running', 'streaming'))
        pulses_before = self.reset.pulses

        with self.ws_connect() as ws:
            ws.recv(timeout=5)                       # stage snapshot
            ws.send(json.dumps({'type': 'stop'}))
            settled = self.recv_stage(ws, 'config', 'stopped')
        self.assertIsNotNone(settled, f'stop never settled: {settled}')
        self.assertFalse(self.device.streaming, 'sensor is stopped')
        self.assertFalse(self.device.wedged,
                         'reset-stop leaves the sensor healthy, not wedged')

        with self.ws_connect() as ws:
            ws.recv(timeout=5)                       # stage snapshot
            ws.send(json.dumps({'type': 'start'}))
            settled = self.recv_stage(ws, 'running', 'streaming')
        self.assertIsNotNone(settled, f'start again never settled: {settled}')
        self.assertIsNotNone(self.udp_recv(),
                             'UDP frames flow after start again')
        self.assertGreater(self.reset.pulses, pulses_before)

    def test_legacy_path_without_reset_wedges_after_stop(self):
        """Documents WHY the reset line exists (the measured firmware
        behavior): without it, the legacy sensorStop sequence works from
        a fresh sensor but can never restart after a Stop."""
        self.make_host(with_reset=False)
        # First start of the day: sensor fresh, sensorStop is a no-op
        # there, the full cfg brings it up. This is the "works
        # perfectly" half of the bug report.
        self.assertTrue(self.wait_host('running', 'streaming'))
        self.assertIn(b'sensorStop', bytes(self.device.written))

        with self.ws_connect() as ws:
            ws.recv(timeout=5)
            ws.send(json.dumps({'type': 'stop'}))
            self.assertIsNotNone(self.recv_stage(ws, 'config', 'stopped'))
            # ...and the wedge: every later start times out.
            ws.send(json.dumps({'type': 'start'}))
            failed = self.recv_stage(ws, 'config', 'error')
        self.assertIsNotNone(failed, 'legacy start-after-stop must fail')
        self.assertIn('no frames resumed',
                      (failed or {}).get('reason', ''))
        self.assertTrue(self.device.wedged)


if __name__ == '__main__':
    unittest.main()
