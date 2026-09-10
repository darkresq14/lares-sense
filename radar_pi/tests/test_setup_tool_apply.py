#!/usr/bin/env python3
"""
Seam tests for the blind apply (issue #11).

The config channel has no ACK path (ADR-0001), so an apply is blind: the
host writes sensorStop → flushCfg → the full regenerated cfg → sensorStart
at CLI baud, switches the port back to data baud, and the **only** success
signal is frames resuming. These tests drive that entire flow through the
one testing seam (ADR-0004): the serial device boundary.

The fake device models the pyserial slice the apply uses — settable baud,
read timeout, input flush — and logs every operation, so the tests assert
the exact write sequence *and* the baud it ran at. Frames are fed back in
through the same fake (the "sensor resuming"), byte-exact via the shared
fixture builder.

Run from the radar_pi/ directory:

    python3 -m unittest tests.test_setup_tool_apply -v
"""

import json
import os
import tempfile
import unittest

from websockets.sync.client import connect

from read_radar import CLI_BAUD, DATA_BAUD
from setup_tool.config_model import cfg_command_lines
from setup_tool.host import SetupToolHost
from tests.test_fixtures import build_tracking_frame_bytes
from tests.test_setup_tool_host import FakeSerialDevice

#: How long the fake sensor stays "dead" after an apply before the test
#: decides the outcome itself (the host's own frames-resumed timeout is
#: shorter — 0.8 s — in these tests).
HOST_FAST_TIMEOUT = 0.8


def recv_non_frame(ws, timeout=5):
    """Next WS message that is not a frame broadcast."""
    while True:
        message = json.loads(ws.recv(timeout=timeout))
        if message.get('type') != 'frame':
            return message


def collect_apply_progress(ws, timeout=5, on_state=None):
    """All apply_progress states up to and including the terminal one.

    ``on_state`` (if given) runs for every state seen — the tests use it to
    make the fake sensor resume emitting at the right moment.
    """
    states = []
    while True:
        message = json.loads(ws.recv(timeout=timeout))
        if message.get('type') != 'apply_progress':
            continue
        states.append(message['state'])
        if on_state is not None:
            on_state(message['state'])
        if message['state'] in ('frames_resumed', 'failed'):
            return states, message


class SetupToolApplyTests(unittest.TestCase):

    def setUp(self):
        self.data_dir = tempfile.mkdtemp(prefix='setup-tool-apply-')
        self.device = FakeSerialDevice()
        # pyserial-style read timeout: the reader wakes up regularly, so the
        # host can pause it for the reconfiguration (real port: 0.6 s).
        self.device.timeout = 0.05
        self.device.baudrate = DATA_BAUD
        self.host = SetupToolHost(
            self.device, bind='127.0.0.1', port=0, config_dir=self.data_dir,
            apply_command_delay=0.0, apply_frames_timeout=HOST_FAST_TIMEOUT)
        self.host.start()
        self.addCleanup(self.host.stop)
        self.port = self.host.actual_port

    # ---- helpers ----------------------------------------------------------

    def ws_connect(self):
        return connect(f'ws://127.0.0.1:{self.port}/', open_timeout=5)

    def get_values(self, ws):
        ws.send(json.dumps({'type': 'config_get'}))
        reply = recv_non_frame(ws)
        self.assertEqual(reply['type'], 'config')
        return reply['values']

    def feed_and_expect(self, ws, frame_nums):
        """The fake sensor emits; the client must see exactly those frames."""
        self.device.feed(b''.join(
            build_tracking_frame_bytes(n, tid=7, n_points=3)
            for n in frame_nums))
        for n in frame_nums:
            message = json.loads(ws.recv(timeout=5))
            self.assertEqual(message['type'], 'frame')
            self.assertEqual(message['frame']['frame_num'], n)

    def cfg_command_lines(self, text):
        """The command lines of a cfg text — what the sensor consumes."""
        return cfg_command_lines(text)

    # ---- the happy path -----------------------------------------------------

    def test_apply_writes_full_cfg_at_cli_baud_and_frames_resume(self):
        with self.ws_connect() as ws:
            values = self.get_values(ws)
            values['boundaryBox'] = [[-5, 5, 0, 9, 0, 3]]
            self.device.ops.clear()      # drop the setUp baud marker

            ws.send(json.dumps({'type': 'apply', 'values': values}))
            # The save half of the round trip is confirmed first...
            saved = recv_non_frame(ws)
            self.assertEqual(saved['type'], 'config_saved')

            # ...then the blind-apply progress. The fake sensor restarts
            # emitting only once the port is back at data baud.
            def resume_sensor(state):
                if state == 'frames_lost':
                    self.device.feed(build_tracking_frame_bytes(201, n_points=2))

            states, _ = collect_apply_progress(ws, on_state=resume_sensor)
            self.assertEqual(states,
                             ['reconfiguring', 'frames_lost', 'frames_resumed'])

            # Frames keep flowing to the page after the apply.
            self.feed_and_expect(ws, [202, 203])

        # --- the seam: exact bytes, at CLI baud, in cfg order -------------
        expected_lines = self.cfg_command_lines(saved['text'])
        self.assertEqual(expected_lines[0], 'sensorStop')
        self.assertEqual(expected_lines[1], 'flushCfg')
        self.assertEqual(expected_lines[-1], 'sensorStart')
        expected = ''.join(l + '\n' for l in expected_lines).encode('ascii')
        self.assertEqual(bytes(self.device.written), expected)
        self.assertIn(b'boundaryBox -5 5 0 9 0 3', self.device.written)

        baud_ops = [(i, op) for i, op in enumerate(self.device.ops)
                    if op[0] == 'baud']
        self.assertEqual([op for _, op in baud_ops],
                         [('baud', CLI_BAUD), ('baud', DATA_BAUD)])
        first_write = next(i for i, op in enumerate(self.device.ops)
                           if op[0] == 'write')
        last_write = max(i for i, op in enumerate(self.device.ops)
                         if op[0] == 'write')
        self.assertLess(baud_ops[0][0], first_write)    # CLI baud first
        self.assertLess(last_write, baud_ops[1][0])     # data baud last

        # The applied values are the persisted Working Config now.
        with self.ws_connect() as ws2:
            self.assertEqual(self.get_values(ws2)['boundaryBox'],
                             [[-5, 5, 0, 9, 0, 3]])

    def test_apply_progress_reaches_every_connected_client(self):
        import threading

        editor_done = None

        def watch_viewer():
            nonlocal editor_done
            editor_done = collect_apply_progress(viewer)

        with self.ws_connect() as editor, self.ws_connect() as viewer:
            values = self.get_values(editor)
            values['boundaryBox'] = [[-4.5, 4.5, 0, 8.5, 0, 3]]
            watcher = threading.Thread(target=watch_viewer)
            watcher.start()
            self.addCleanup(watcher.join, timeout=5)

            editor.send(json.dumps({'type': 'apply', 'values': values}))

            # No frames ever resume: the failure path broadcasts too.
            states, final = collect_apply_progress(editor)
            self.assertEqual(states,
                             ['reconfiguring', 'frames_lost', 'failed'])
            self.assertIn('frame', final.get('reason', '').lower())
            watcher.join(timeout=5)
            self.assertIsNotNone(editor_done)
            self.assertEqual(editor_done[0], states)

    # ---- failure: frames never resume ---------------------------------------

    def test_frames_resume_timeout_is_explicit_failure_editor_usable(self):
        with self.ws_connect() as ws:
            values = self.get_values(ws)
            values['sensorPosition'] = [[2.5, 10, 20]]
            ws.send(json.dumps({'type': 'apply', 'values': values}))

            states, final = collect_apply_progress(ws)
            self.assertEqual(states,
                             ['reconfiguring', 'frames_lost', 'failed'])
            self.assertIn('frame', final['reason'].lower())

            # The port went back to data baud even on failure (the reader
            # must be able to see the sensor come back).
            self.assertEqual(self.device.baudrate, DATA_BAUD)

            # The editor is still usable: config traffic works...
            values['boundaryBox'] = [[-3, 3, 0, 7, 0, 3]]
            ws.send(json.dumps({'type': 'config_set', 'values': values}))
            self.assertEqual(recv_non_frame(ws)['type'], 'config_saved')

            # ...and a retry succeeds once the sensor does resume emitting.
            def resume_sensor(state):
                if state == 'frames_lost':
                    self.device.feed(build_tracking_frame_bytes(301, n_points=1))

            ws.send(json.dumps({'type': 'apply', 'values': values}))
            retry, _ = collect_apply_progress(ws, on_state=resume_sensor)
            self.assertEqual(retry[-1], 'frames_resumed')

    # ---- failure: the device itself dies mid-sequence -----------------------

    def test_write_error_restores_data_baud_and_tool_recovers(self):
        with self.ws_connect() as ws:
            values = self.get_values(ws)

            # The port dies on the second command (USB hiccup-class fault).
            real_write = self.device.write
            count = {'n': 0}

            def flaky_write(data):
                count['n'] += 1
                if count['n'] > 1:
                    raise OSError('usb hiccup')
                real_write(data)

            self.device.write = flaky_write
            ws.send(json.dumps({'type': 'apply', 'values': values}))
            states, final = collect_apply_progress(ws)
            self.device.write = real_write

            self.assertEqual(states, ['reconfiguring', 'failed'])
            self.assertIn('usb hiccup', final['reason'])

            # The port was put back to data baud — not stranded at CLI baud.
            self.assertEqual(self.device.baudrate, DATA_BAUD)

            # The tool recovered: frames flow again, and a later apply
            # completes normally.
            self.feed_and_expect(ws, [501])

            def resume_sensor(state):
                if state == 'frames_lost':
                    self.device.feed(build_tracking_frame_bytes(502, n_points=1))

            ws.send(json.dumps({'type': 'apply', 'values': values}))
            retry, _ = collect_apply_progress(ws, on_state=resume_sensor)
            self.assertEqual(retry[-1], 'frames_resumed')

    # ---- the gate: invalid config never touches the serial device -----------

    def test_invalid_apply_is_rejected_with_zero_serial_writes(self):
        with self.ws_connect() as ws:
            values = self.get_values(ws)
            values['sensorPosition'] = [[99, 0, 15]]    # height out of range

            self.device.ops.clear()
            del self.device.written[:]

            ws.send(json.dumps({'type': 'apply', 'values': values}))
            reply = recv_non_frame(ws)
            self.assertEqual(reply['type'], 'config_rejected')
            fields = {e['field'] for e in reply['errors']}
            self.assertIn('z', fields)

            # Not one byte, not one baud change, not one flush.
            self.assertEqual(bytes(self.device.written), b'')
            self.assertEqual(self.device.ops, [])

            # And frames keep flowing: the tool never left the streaming
            # state it was in.
            self.feed_and_expect(ws, [401])


    # ---- the page carries the apply controls -------------------------------

    def test_page_contains_apply_controls(self):
        """ADR-0004: browser UI is manual-verified; the seam asserts serving."""
        import urllib.request
        with urllib.request.urlopen(
                f'http://127.0.0.1:{self.port}/', timeout=5) as resp:
            page = resp.read().decode('utf-8')
        with urllib.request.urlopen(
                f'http://127.0.0.1:{self.port}/app.js', timeout=5) as resp:
            app = resp.read().decode('utf-8')

        self.assertIn('id="apply"', page)
        self.assertIn('id="apply-status"', page)
        for state in ('reconfiguring', 'frames_lost', 'frames_resumed',
                      'failed'):
            self.assertIn(f"'{state}'", app)


if __name__ == '__main__':
    unittest.main()
