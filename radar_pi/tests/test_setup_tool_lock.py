#!/usr/bin/env python3
"""
Seam tests for the editing lock, WS hub hardening and read-only monitor
(issue #14).

Three behaviors (ADR-0004): **Editing lock** — the first browser to edit
owns the session; additional browsers get a read-only view with a "config
locked" banner, and the lock frees when the editing tab goes away (or its
editor panel closes). **WS hub hardening** — per-client bounded send queues
with drop-oldest overflow, so a slow or frozen browser never stalls frames
to a healthy client, the UDP forwarding, or the reader loop. **Read-only
monitor** — Running Stage hides all edit controls on every connected
browser; the page is a live monitor.

The lock and the frozen browser are driven through the one testing seam
(ADR-0004): the serial device boundary plus real loopback WS clients and a
real loopback UDP receiver. A frozen browser cannot be modeled with the
sync WS client (its background thread keeps draining the socket), so the
hardening test speaks just enough WebSocket by hand on a raw TCP socket —
handshake, then stop reading: TCP backpressure at its most literal.

Run from the radar_pi/ directory:

    python3 -m unittest tests.test_setup_tool_lock -v
"""

import base64
import json
import os
import socket
import tempfile
import time
import unittest
import urllib.request

from websockets.sync.client import connect

from setup_tool.config_store import WorkingConfigStore
from setup_tool.host import MAX_CLIENT_QUEUE, SetupToolHost
from tests.test_setup_tool_host import FakeSerialDevice
from tests.test_setup_tool_stages import FrameFeeder


class SetupToolLockTests(unittest.TestCase):
    """The editing lock: one editing session at a time (issue #14)."""

    def setUp(self):
        # Fresh data dir per test: a Working Config persisted there by an
        # earlier session would trigger boot auto-Running (issue #12).
        self.data_dir = tempfile.mkdtemp(prefix='setup-tool-lock-')
        self.device = FakeSerialDevice()
        self.device.timeout = 0.05
        self.host = SetupToolHost(self.device, bind='127.0.0.1', port=0,
                                  config_dir=self.data_dir)
        self.host.start()
        self.addCleanup(self.host.stop)
        self.port = self.host.actual_port

    # ---- helpers ----------------------------------------------------------

    def ws_connect(self):
        return connect(f'ws://127.0.0.1:{self.port}/', open_timeout=5)

    def recv_of_type(self, ws, type_, timeout=5):
        """Next WS message of a given type; frames and events skipped."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            self.assertGreater(remaining, 0, f'{type_} reply never came')
            message = json.loads(ws.recv(timeout=remaining))
            if message.get('type') == type_:
                return message

    def begin_edit(self, ws):
        """Send edit_begin; return its reply (the lock event with a
        ``granted`` verdict — broadcasts carry no verdict and are skipped)."""
        ws.send(json.dumps({'type': 'edit_begin'}))
        deadline = time.monotonic() + 5
        while True:
            remaining = deadline - time.monotonic()
            self.assertGreater(remaining, 0, 'edit_begin reply never came')
            message = json.loads(ws.recv(timeout=remaining))
            if (message.get('type') == 'lock'
                    and 'granted' in message):
                return message

    def stored_text(self):
        """The persisted Working Config text (the source of truth)."""
        return WorkingConfigStore(data_dir=self.data_dir).load()

    def save_height(self, ws, z):
        """Submit a config change (a valid sensor height) from ``ws``."""
        ws.send(json.dumps({'type': 'config_get'}))
        values = self.recv_of_type(ws, 'config')['values']
        values['sensorPosition'] = [[2.5, 10, z]]
        ws.send(json.dumps({'type': 'config_set', 'values': values}))

    # ---- the lock ----------------------------------------------------------

    def test_second_browser_is_readonly_and_cannot_submit(self):
        with self.ws_connect() as ws1:
            # The first browser to edit owns the session.
            granted = self.begin_edit(ws1)
            self.assertTrue(granted['held'])
            self.assertTrue(granted['granted'])

            # The owner can still submit config changes.
            self.save_height(ws1, 21)
            self.assertEqual(self.recv_of_type(ws1, 'config_saved')['type'],
                             'config_saved')
            before = self.stored_text()

            with self.ws_connect() as ws2:
                # A browser connecting mid-session learns the lock state:
                # the stage snapshot, then a held lock notice.
                snapshot = json.loads(ws2.recv(timeout=5))
                self.assertEqual(snapshot['type'], 'stage')
                held = self.recv_of_type(ws2, 'lock')
                self.assertTrue(held['held'])
                self.assertNotIn('granted', held)   # a notice, not a reply

                # An explicit edit request from the second browser is denied.
                denied = self.begin_edit(ws2)
                self.assertTrue(denied['held'])
                self.assertFalse(denied['granted'])

                # A submit from the read-only browser is rejected — the
                # Working Config never changes.
                self.save_height(ws2, 99)
                rejected = self.recv_of_type(ws2, 'lock')
                self.assertTrue(rejected['held'])
                self.assertFalse(rejected['granted'])
                self.assertEqual(self.stored_text(), before)

            # The owner is unaffected by all of the above.
            self.save_height(ws1, 22)
            self.assertEqual(self.recv_of_type(ws1, 'config_saved')['type'],
                             'config_saved')

    def test_lock_releases_when_editing_client_disconnects(self):
        ws1 = self.ws_connect()
        self.addCleanup(ws1.close)
        self.assertTrue(self.begin_edit(ws1)['granted'])

        ws2 = self.ws_connect()
        self.addCleanup(ws2.close)
        self.assertTrue(self.recv_of_type(ws2, 'lock')['held'])

        # The editing tab goes away: the lock frees and everyone hears it.
        ws1.close()
        released = self.recv_of_type(ws2, 'lock')
        self.assertFalse(released['held'])

        # Another browser can then edit — and actually submit.
        self.assertTrue(self.begin_edit(ws2)['granted'])
        self.save_height(ws2, 23)
        self.assertEqual(self.recv_of_type(ws2, 'config_saved')['type'],
                         'config_saved')

    def test_read_only_viewer_leaving_keeps_the_lock(self):
        ws1 = self.ws_connect()
        self.addCleanup(ws1.close)
        self.assertTrue(self.begin_edit(ws1)['granted'])

        viewer = self.ws_connect()
        self.assertTrue(self.recv_of_type(viewer, 'lock')['held'])
        viewer.close()

        # A departing viewer never owned the lock, so its disconnect
        # cannot free it — whichever order the server processes things.
        ws2 = self.ws_connect()
        self.addCleanup(ws2.close)
        denied = self.begin_edit(ws2)
        self.assertTrue(denied['held'])
        self.assertFalse(denied['granted'])

    def test_submit_without_claim_claims_the_session(self):
        # "The first browser to edit owns the session", enforced at the
        # server even for a client that never spoke the lock protocol.
        with self.ws_connect() as ws1, self.ws_connect() as ws2:
            self.save_height(ws1, 24)
            self.assertEqual(self.recv_of_type(ws1, 'config_saved')['type'],
                             'config_saved')
            self.save_height(ws2, 25)
            rejected = self.recv_of_type(ws2, 'lock')
            self.assertFalse(rejected['granted'])

    def test_edit_end_releases_the_lock(self):
        ws1 = self.ws_connect()
        self.addCleanup(ws1.close)
        self.assertTrue(self.begin_edit(ws1)['granted'])

        ws2 = self.ws_connect()
        self.addCleanup(ws2.close)
        self.assertTrue(self.recv_of_type(ws2, 'lock')['held'])

        # The owner closes its editor panel: the lock frees without the
        # tab going away, and the other browser can take over.
        ws1.send(json.dumps({'type': 'edit_end'}))
        self.assertFalse(self.recv_of_type(ws2, 'lock')['held'])
        self.assertTrue(self.begin_edit(ws2)['granted'])

    def test_new_browser_can_edit_when_no_lock_is_held(self):
        with self.ws_connect() as ws1, self.ws_connect() as ws2:
            # Nobody claimed an editing session: either browser may.
            self.assertTrue(self.begin_edit(ws2)['granted'])
            denied = self.begin_edit(ws1)
            self.assertTrue(denied['held'])
            self.assertFalse(denied['granted'])


class FrozenBrowser:
    """A WebSocket client that completes the handshake, then stops reading.

    A frozen browser at the TCP level — the thing the hub's bounded
    drop-oldest queues exist for. The sync WS test client cannot model
    this (its background thread keeps draining the socket into memory),
    so this speaks just enough protocol by hand: the upgrade request, a
    small receive buffer, then silence.
    """

    def __init__(self, port):
        self._sock = socket.create_connection(('127.0.0.1', port), timeout=5)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        key = base64.b64encode(os.urandom(16)).decode('ascii')
        request = ('GET / HTTP/1.1\r\n'
                   f'Host: 127.0.0.1:{port}\r\n'
                   'Upgrade: websocket\r\n'
                   'Connection: Upgrade\r\n'
                   f'Sec-WebSocket-Key: {key}\r\n'
                   'Sec-WebSocket-Version: 13\r\n\r\n')
        self._sock.sendall(request.encode('ascii'))
        self._buf = self._recv_headers()
        self._sock.settimeout(0.2)

    def _recv_headers(self):
        """Read up to the end of the 101 response headers."""
        buf = b''
        while b'\r\n\r\n' not in buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        if not buf.startswith(b'HTTP/1.1 101'):
            raise AssertionError(f'handshake failed: {buf[:120]!r}')
        return buf.split(b'\r\n\r\n', 1)[1]

    def read_messages(self, duration):
        """Thaw: read for ``duration`` seconds, parse the server's text
        frames, return their JSON payloads (oldest first)."""
        deadline = time.monotonic() + duration
        messages = []
        while time.monotonic() < deadline:
            try:
                chunk = self._sock.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                break
            self._buf += chunk
            while True:
                payload, self._buf = self._parse_frame(self._buf)
                if payload is None:
                    break
                messages.append(payload)
        return messages

    @staticmethod
    def _parse_frame(buf):
        """One unmasked server frame off ``buf`` → (payload, rest).

        Returns ``(None, buf)`` when the buffer holds less than a full
        frame. Control frames (ping/pong/close) are skipped — only text
        frames carry payloads here.
        """
        if len(buf) < 2:
            return None, buf
        opcode = buf[0] & 0x0F
        if buf[1] & 0x80:           # server never masks; refuse if it did
            return None, buf
        length = buf[1] & 0x7F
        offset = 2
        if length == 126:
            if len(buf) < 4:
                return None, buf
            length = int.from_bytes(buf[2:4], 'big')
            offset = 4
        elif length == 127:
            if len(buf) < 10:
                return None, buf
            length = int.from_bytes(buf[2:10], 'big')
            offset = 10
        if len(buf) < offset + length:
            return None, buf
        payload = buf[offset:offset + length]
        if opcode != 0x1:           # control or binary: skip
            return None, buf[offset + length:]
        return json.loads(payload.decode('utf-8')), buf[offset + length:]

    def close(self):
        self._sock.close()


class HubHardeningTests(unittest.TestCase):
    """A frozen browser stalls nothing (issue #14's headline seam test)."""

    def setUp(self):
        self.data_dir = tempfile.mkdtemp(prefix='setup-tool-hub-')
        self.device = FakeSerialDevice()
        self.device.timeout = 0.05
        self.udp_rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_rx.bind(('127.0.0.1', 0))
        self.udp_rx.settimeout(5)
        self.addCleanup(self.udp_rx.close)
        self.host = SetupToolHost(
            self.device, bind='127.0.0.1', port=0, config_dir=self.data_dir,
            udp_target=self.udp_rx.getsockname(),
            apply_command_delay=0.0, apply_frames_timeout=0.8)
        self.host.start()
        self.addCleanup(self.host.stop)
        self.port = self.host.actual_port

    # ---- helpers ----------------------------------------------------------

    def ws_connect(self):
        return connect(f'ws://127.0.0.1:{self.port}/', open_timeout=5)

    def recv_of_type(self, ws, type_, timeout=5):
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            self.assertGreater(remaining, 0, f'{type_} reply never came')
            message = json.loads(ws.recv(timeout=remaining))
            if message.get('type') == type_:
                return message

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

    def wait_stage(self, ws, stage, health, timeout=10):
        """Next ``stage`` event matching both stage and health."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            self.assertGreater(remaining, 0,
                               f'stage {stage}/{health} never came')
            message = json.loads(ws.recv(timeout=remaining))
            if (message.get('type') == 'stage'
                    and message.get('stage') == stage
                    and message.get('health') == health):
                return message

    def begin_edit(self, ws):
        """Send edit_begin; return its reply (the lock event with a
        ``granted`` verdict — broadcasts carry no verdict and are skipped)."""
        ws.send(json.dumps({'type': 'edit_begin'}))
        deadline = time.monotonic() + 5
        while True:
            remaining = deadline - time.monotonic()
            self.assertGreater(remaining, 0, 'edit_begin reply never came')
            message = json.loads(ws.recv(timeout=remaining))
            if (message.get('type') == 'lock'
                    and 'granted' in message):
                return message

    def test_edit_lock_survives_a_start_stop_cycle(self):
        """Start/Stop must not hand the session to another browser: the
        owner keeps its claim across Running Stage and back."""
        feeder = FrameFeeder(self.device, first_num=8000).start()
        self.addCleanup(feeder.stop)

        with self.ws_connect() as ws1:
            self.assertTrue(self.begin_edit(ws1)['granted'])

            ws1.send(json.dumps({'type': 'start'}))
            self.wait_stage(ws1, stage='running', health='streaming')
            ws1.send(json.dumps({'type': 'stop'}))
            self.wait_stage(ws1, stage='config', health='stopped')

            # Nobody else may claim it after the cycle, and the owner can
            # still submit.
            with self.ws_connect() as ws2:
                self.assertFalse(self.begin_edit(ws2)['granted'])

            ws1.send(json.dumps({'type': 'config_get'}))
            values = self.recv_of_type(ws1, 'config')['values']
            ws1.send(json.dumps({'type': 'config_set', 'values': values}))
            self.assertEqual(self.recv_of_type(ws1, 'config_saved')['type'],
                             'config_saved')

    # ---- the frozen browser stalls nothing ---------------------------------

    def test_frozen_browser_stalls_no_frames_udp_or_reader(self):
        # Big frames (~9 KB JSON each) so the freeze genuinely clogs the
        # frozen client's socket — buffers, transport, the works. The
        # feeder runs at 200 fps: a fast host keeps up (strict gap check
        # below), while a Pi 3B cannot serialize that fast — its measured
        # healthy rate falls under 100 fps and the test takes the slow-
        # hardware path automatically (scaled freeze, weaker gap check).
        feeder = FrameFeeder(self.device, first_num=7000, interval=0.005,
                             n_points=250)
        feeder.start()
        self.addCleanup(feeder.stop)

        healthy = self.ws_connect()
        self.addCleanup(healthy.close)
        healthy.recv(timeout=5)                      # stage snapshot
        healthy.send(json.dumps({'type': 'start'}))
        deadline = time.monotonic() + 10
        while True:
            message = json.loads(healthy.recv(timeout=5))
            if (message.get('type') == 'stage'
                    and message.get('stage') == 'running'
                    and message.get('health') == 'streaming'):
                break
            self.assertLess(time.monotonic(), deadline, 'never running')

        # Frames keep flowing to the healthy browser — count them over a
        # one-second window to measure the live rate on *this* hardware
        # (a Pi 3B delivers ~25 fps where a dev box does ~200).
        healthy_frames = 0
        window = time.monotonic() + 1.0
        while time.monotonic() < window:
            message = json.loads(healthy.recv(timeout=5))
            if message.get('type') == 'frame':
                healthy_frames += 1
        self.assertGreaterEqual(healthy_frames, 20)

        # The frozen browser connects, then reads nothing at all. Freeze
        # long enough that a MAX_CLIENT_QUEUE-deep per-client queue must
        # overflow at the measured rate (2x margin): at ~25 fps one second
        # of freeze never fills a 64-deep queue, so drop-oldest is not
        # exercised at all on slow hardware.
        frozen = FrozenBrowser(self.port)
        self.addCleanup(frozen.close)
        freeze_s = max(1.0, 2 * MAX_CLIENT_QUEUE / healthy_frames)
        time.sleep(freeze_s)                         # TCP backpressure on

        # ...while the frozen browser clogs its socket, frames keep
        # flowing to the healthy browser...
        during_freeze = 0
        window = time.monotonic() + 1.0
        while time.monotonic() < window:
            message = json.loads(healthy.recv(timeout=5))
            if message.get('type') == 'frame':
                during_freeze += 1
        self.assertGreaterEqual(during_freeze, 20)

        # ...UDP forwarding keeps flowing (Running Stage)...
        self.assertGreaterEqual(self.udp_drain(), 5)

        # ...and the reader loop never stalled.
        self.assertGreaterEqual(self.host.frames_parsed, 50)

        # Drop-oldest, thawed and observable: the frozen browser sees a
        # frame-number gap — the frames dropped from its bounded queue —
        # and it is behind the live stream. The read window scales with
        # the freeze: on slow hardware the thaw drain trickles, and a flat
        # 2 s window can end before a second frame even arrives.
        messages = frozen.read_messages(duration=max(2.0, freeze_s))
        nums = [m['frame']['frame_num'] for m in messages
                if m.get('type') == 'frame']
        self.assertTrue(nums, 'frozen browser never saw a frame')
        jumps = [b - a for a, b in zip(nums, nums[1:])]
        # Strict drop-oldest gap (≥10) only where the thaw drain can
        # actually show it: on a fast host the sender fills the transport
        # pause quickly, the bounded queue overflows, and the gap is
        # visible seconds after thaw. On a Pi 3B the frozen client's tiny
        # SO_RCVBUF throttles TCP to a trickle — the drain shows long runs
        # of consecutive buffered frames inside any sane window, so only
        # "frames still flow after thaw" is assertable there.
        min_gap = 10 if healthy_frames >= 100 else 1
        self.assertGreaterEqual(max(jumps), min_gap,
                                f'no drop-oldest gap in {nums[:8]}…')


class MonitorPageTests(unittest.TestCase):
    """ADR-0004: the browser UI is manual-verified; the seam asserts
    serving. Running Stage must hide the edit controls (issue #14's
    read-only monitor) and the page must speak the lock protocol."""

    def setUp(self):
        self.data_dir = tempfile.mkdtemp(prefix='setup-tool-page-')
        self.device = FakeSerialDevice()
        self.host = SetupToolHost(self.device, bind='127.0.0.1', port=0,
                                  config_dir=self.data_dir)
        self.host.start()
        self.addCleanup(self.host.stop)
        self.port = self.host.actual_port

    def http_get(self, path):
        url = f'http://127.0.0.1:{self.port}{path}'
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.read().decode('utf-8')

    def test_page_carries_monitor_and_lock_markers(self):
        page = self.http_get('/')
        # Read-only monitor: Running Stage hides the edit controls.
        self.assertIn('body.stage-running #editor', page)
        self.assertIn('body.stage-running #editor-toggle', page)
        # The "config locked" banner and its read-only-browser state.
        self.assertIn('id="lock-banner"', page)
        self.assertIn('body.edit-locked #lock-banner', page)

        app = self.http_get('/app.js')
        # The lock protocol: request / release / state events.
        self.assertIn("'edit_begin'", app)
        self.assertIn("'edit_end'", app)
        self.assertIn("'lock'", app)
        self.assertIn('edit-locked', app)


if __name__ == '__main__':
    unittest.main()
