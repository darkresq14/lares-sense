#!/usr/bin/env python3
"""
Seam tests for the Setup Tool host skeleton (issue #9).

Per ADR-0004's testing decision, the Setup Tool is exercised as external
behavior through **one seam**: the serial device boundary. A fake in-memory
device (synthetic TLV frames in, the tool's writes observable) drives the real
host, while real loopback HTTP and WebSocket clients consume what it serves.
No internals are unit-tested here.

Frames are byte-exact via the shared fixture builder (``build_frame`` reuse),
so no new fixture machinery exists.

Run from the radar_pi/ directory:

    python3 -m unittest tests.test_setup_tool_host -v
"""

import json
import threading
import unittest
import urllib.error
import urllib.request

from websockets.sync.client import connect

from setup_tool.host import SetupToolHost
from tlv_parser import parse_frame
from tests.test_fixtures import build_tracking_frame_bytes

#: The keys every parsed frame carries (tlv_parser.parse_frame output shape —
#: the same keys the persist path consumes).
PARSER_KEYS = {
    'frame_num', 'num_points', 'points', 'snr', 'noise', 'range_profile',
    'tracks', 'track_heights', 'track_indexes',
}


class FakeSerialDevice:
    """In-memory stand-in for the pyserial port the host reads frames from.

    Bytes appended with :meth:`feed` come back from :meth:`read` in order.
    ``read`` blocks while the buffer is empty (a serial port waiting for data)
    and raises once the device is closed — which is how the reader loop learns
    to stop, mirroring a closed pyserial port.
    """

    def __init__(self):
        self._buf = bytearray()
        self._cond = threading.Condition()
        self._closed = False

    def feed(self, data):
        with self._cond:
            self._buf.extend(data)
            self._cond.notify_all()

    def read(self, size=1):
        with self._cond:
            while not self._buf and not self._closed:
                self._cond.wait(timeout=0.1)
            if not self._buf:
                raise OSError('device closed')
            chunk = self._buf[:size]
            del self._buf[:size]
            return bytes(chunk)

    def close(self):
        with self._cond:
            self._closed = True
            self._cond.notify_all()


class SetupToolHostTests(unittest.TestCase):

    def setUp(self):
        self.device = FakeSerialDevice()
        self.host = SetupToolHost(self.device, bind='127.0.0.1', port=0)
        self.host.start()
        self.addCleanup(self.host.stop)
        self.port = self.host.actual_port
        self.assertIsNotNone(self.port)

    # ---- helpers ----------------------------------------------------------

    def ws_connect(self, port=None):
        return connect(f'ws://127.0.0.1:{port or self.port}/', open_timeout=5)

    def http_get(self, path):
        url = f'http://127.0.0.1:{self.port}{path}'
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, resp.headers.get('Content-Type'), resp.read()

    # ---- HTTP: the page is served -----------------------------------------

    def test_serves_setup_page_over_http(self):
        status, ctype, body = self.http_get('/')
        self.assertEqual(status, 200)
        self.assertIsNotNone(ctype)
        self.assertIn('text/html', ctype)
        self.assertIn(b'vendor/three.min.js', body)
        self.assertIn(b'app.js', body)

    def test_serves_page_assets(self):
        status, ctype, _ = self.http_get('/app.js')
        self.assertEqual(status, 200)
        self.assertIn('javascript', ctype)

        status, ctype, _ = self.http_get('/vendor/three.min.js')
        self.assertEqual(status, 200)
        self.assertIn('javascript', ctype)

    def test_unknown_path_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.http_get('/etc/passwd')
        self.assertEqual(ctx.exception.code, 404)

    # ---- WS: parsed frames in the parser output shape ----------------------

    def test_frames_from_fake_device_reach_ws_in_parser_shape(self):
        raws = [build_tracking_frame_bytes(n, tid=7, n_points=3)
                for n in (101, 102, 103)]

        with self.ws_connect() as ws:
            # Garbage before the first magic word must be discarded, exactly
            # like on the wire.
            self.device.feed(b'\x00\x01 garbage ' + b''.join(raws))
            messages = [json.loads(ws.recv(timeout=5)) for _ in raws]

        self.assertTrue(all(m['type'] == 'frame' for m in messages))
        frames = [m['frame'] for m in messages]

        self.assertEqual([f['frame_num'] for f in frames], [101, 102, 103])
        for frame in frames:
            self.assertEqual(set(frame.keys()), PARSER_KEYS)
            self.assertEqual(frame['num_points'], 3)
            self.assertEqual(len(frame['points']), 3)
            self.assertEqual(len(frame['tracks']), 1)
            self.assertEqual(len(frame['track_indexes']), 3)

        # Byte-exact identity with what parse_frame returns for the same
        # bytes (json round-trip normalizes tuples to lists).
        expected = json.loads(json.dumps(parse_frame(raws[0])))
        self.assertEqual(frames[0], expected)

    def test_multiple_ws_clients_each_receive_all_frames(self):
        raws = [build_tracking_frame_bytes(n, tid=3, n_points=2)
                for n in (1, 2)]

        with self.ws_connect() as ws1, self.ws_connect() as ws2:
            self.device.feed(b''.join(raws))
            got1 = [json.loads(ws1.recv(timeout=5)) for _ in raws]
            got2 = [json.loads(ws2.recv(timeout=5)) for _ in raws]

        for got in (got1, got2):
            self.assertEqual([m['frame']['frame_num'] for m in got], [1, 2])

    # ---- lifecycle ---------------------------------------------------------

    def test_stop_closes_the_server(self):
        device = FakeSerialDevice()
        host = SetupToolHost(device, bind='127.0.0.1', port=0)
        host.start()
        port = host.actual_port
        host.stop()

        # The listener socket is gone: new connections are refused.
        with self.assertRaises(OSError):
            self.ws_connect(port).recv(timeout=1)


if __name__ == '__main__':
    unittest.main()
