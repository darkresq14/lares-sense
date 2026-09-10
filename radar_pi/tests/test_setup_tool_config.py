#!/usr/bin/env python3
"""
Seam tests for the Setup Tool config editor backend (issue #10).

Per ADR-0004's testing decision, the whole Setup Tool is exercised through
**one seam**: the serial device boundary. These tests drive the real host
(WS clients against the real loopback server) through the config API —
fetch schema/values, save valid edits, reject invalid edits — while a fake
serial device records every byte ever written to it. The config path must
leave the device completely untouched: validation failures are provable
without a single byte reaching the serial port.

Working Config persistence is checked against a throwaway data dir (never
the repo): first run seeds from the pristine vendor template, saves persist
across process restarts (a second host instance), and the repo template
file stays byte-identical throughout.

Run from the radar_pi/ directory:

    python3 -m unittest tests.test_setup_tool_config -v
"""

import json
import os
import tempfile
import unittest
import urllib.request

from websockets.sync.client import connect

from setup_tool.host import SetupToolHost
from tests.test_setup_tool_host import FakeSerialDevice

#: The pristine vendor template in the repo — must stay untouched.
TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'xwr6843_aop_3d_people_track_6m.cfg')


def read_template():
    with open(TEMPLATE_PATH, 'rb') as f:
        return f.read()


class SetupToolConfigTests(unittest.TestCase):

    def setUp(self):
        self.template_before = read_template()
        self.data_dir = tempfile.mkdtemp(prefix='setup-tool-cfg-')
        self.device = FakeSerialDevice()
        self.host = self.start_host()
        self.port = self.host.actual_port

    def tearDown(self):
        # Whatever the tests did: the repo template is pristine, and not a
        # single byte ever reached the serial device.
        self.assertEqual(read_template(), self.template_before,
                         'the vendor template in the repo was modified')
        self.assertEqual(bytes(self.device.written), b'',
                         'bytes reached the serial device')

    def start_host(self):
        host = SetupToolHost(self.device, bind='127.0.0.1', port=0,
                             config_dir=self.data_dir, auto_start=False)
        host.start()
        self.addCleanup(host.stop)
        return host

    # ---- helpers ----------------------------------------------------------

    def request(self, message):
        """Send one config request over WS; return the matching reply.

        Frames broadcast while we wait are skipped, as are stage
        snapshots/events (issue #12) — we only care about the config
        protocol messages.
        """
        with connect(f'ws://127.0.0.1:{self.port}/', open_timeout=5) as ws:
            ws.send(json.dumps(message))
            for _ in range(50):
                reply = json.loads(ws.recv(timeout=5))
                if reply['type'] not in ('frame', 'stage'):
                    return reply
        self.fail('no config reply arrived')

    def get_config(self):
        return self.request({'type': 'config_get'})

    # ---- first run: seeded from the vendor template -------------------------

    def test_first_run_seeds_working_config_from_template(self):
        # The data dir starts empty; loading seeded it from the template.
        working = os.listdir(self.data_dir)
        self.assertEqual(len(working), 1)
        with open(os.path.join(self.data_dir, working[0]), 'rb') as f:
            self.assertEqual(f.read(), self.template_before)

    def test_config_get_returns_schema_and_template_defaults(self):
        reply = self.get_config()

        self.assertEqual(reply['type'], 'config')
        by_command = {p['command']: p for p in reply['schema']['params']}
        self.assertIn('boundaryBox', by_command)
        self.assertEqual(by_command['boundaryBox']['tier'], 'room_mounting')
        self.assertEqual(by_command['chirpCfg']['instances'], 3)
        # Tier guidance (labels + blurbs) rides along for the editor.
        self.assertEqual([t['id'] for t in reply['schema']['tiers']],
                         ['room_mounting', 'tracking', 'rf_expert'])

        # Values are the shipped defaults, in template shape.
        self.assertEqual(reply['values']['sensorPosition'], [[2, 0, 15]])
        self.assertEqual(reply['values']['chirpCfg'],
                         [[0, 0, 0, 0, 0, 0, 0, 1],
                          [1, 1, 0, 0, 0, 0, 0, 2],
                          [2, 2, 0, 0, 0, 0, 0, 4]])

        # The full generated cfg text is what the sensor would be sent.
        self.assertIn('sensorStart', reply['text'])
        self.assertIn('boundaryBox -4 4 0 8 0 3', reply['text'])

    # ---- saving ------------------------------------------------------------

    def test_valid_save_persists_and_reports(self):
        values = self.get_config()['values']
        values['boundaryBox'] = [[-5, 5, 0, 9, 0, 3]]
        values['sensorPosition'] = [[2.5, 10, 20]]

        reply = self.request({'type': 'config_set', 'values': values})
        self.assertEqual(reply['type'], 'config_saved')
        self.assertEqual(reply['values']['boundaryBox'], [[-5, 5, 0, 9, 0, 3]])
        self.assertIn('sensorPosition 2.5 10 20', reply['text'])

        # The Working Config on disk carries the new values.
        working_path = os.path.join(self.data_dir,
                                    os.listdir(self.data_dir)[0])
        with open(working_path, 'rb') as f:
            text = f.read().decode('ascii')
        self.assertIn('boundaryBox -5 5 0 9 0 3', text)
        self.assertIn('sensorPosition 2.5 10 20', text)
        # Full generated cfg: control commands and all parameters present.
        self.assertTrue(text.startswith('% SDK Parameters'), text[:40])
        self.assertIn('sensorStart', text)

    def test_saved_values_survive_process_restart(self):
        values = self.get_config()['values']
        values['boundaryBox'] = [[-6, 6, 0, 10, 0, 4]]
        self.request({'type': 'config_set', 'values': values})

        # A second host instance (new process-equivalent) on the same data
        # dir serves the saved Working Config, not the template defaults.
        host2 = self.start_host()
        with connect(f'ws://127.0.0.1:{host2.actual_port}/', open_timeout=5) as ws:
            ws.send(json.dumps({'type': 'config_get'}))
            reply = None
            for _ in range(50):
                message = json.loads(ws.recv(timeout=5))
                if message['type'] not in ('frame', 'stage'):
                    reply = message
                    break
        self.assertEqual(reply['values']['boundaryBox'], [[-6, 6, 0, 10, 0, 4]])

        # And it did *not* re-seed over the saved file.
        files = os.listdir(self.data_dir)
        self.assertEqual(len(files), 1)

    # ---- validation: rejected with inline errors, zero bytes to the device --

    def test_invalid_save_rejected_with_field_errors(self):
        values = self.get_config()['values']
        values['sensorPosition'] = [[99, 0, 15]]           # height out of range
        values['boundaryBox'] = [['-4', 'garbage', 0, 8, 0, 3]]  # wrong type

        working_path = os.path.join(self.data_dir,
                                    os.listdir(self.data_dir)[0])
        with open(working_path, 'rb') as f:
            before = f.read()

        reply = self.request({'type': 'config_set', 'values': values})
        self.assertEqual(reply['type'], 'config_rejected')
        fields = {e['field'] for e in reply['errors']}
        self.assertIn('z', fields)          # sensorPosition height
        self.assertIn('x_max', fields)      # boundaryBox garbage

        # Nothing was written: the Working Config is unchanged.
        with open(working_path, 'rb') as f:
            self.assertEqual(f.read(), before)

        # ...and no bytes reached the serial device (tearDown asserts this
        # again for the whole test).

    def test_round_trip_and_rejection_leave_device_untouched(self):
        """The acceptance seam: config API traffic, zero serial output."""
        values = self.get_config()['values']
        values['frameCfg'] = [[0, 2, 96, 0, 65.00, 1, 0]]
        saved = self.request({'type': 'config_set', 'values': values})
        self.assertEqual(saved['type'], 'config_saved')

        values['frameCfg'] = [[0, 2, 9999, 0, 65.00, 1, 0]]  # loops > 255
        rejected = self.request({'type': 'config_set', 'values': values})
        self.assertEqual(rejected['type'], 'config_rejected')

        # The acceptance seam, stated plainly: a full config round-trip plus
        # a validation rejection, and zero bytes left the serial device.
        self.assertEqual(bytes(self.device.written), b'')

    # ---- editor page --------------------------------------------------------

    def test_page_contains_tiered_editor(self):
        url = f'http://127.0.0.1:{self.port}/'
        with urllib.request.urlopen(url, timeout=5) as resp:
            body = resp.read().decode('utf-8')
        self.assertIn('id="editor"', body)
        self.assertIn('id="save"', body)

    # ---- recovery: a corrupt persisted cfg must not brick the tool ----------

    def test_corrupt_working_config_recovers_to_template(self):
        values = self.get_config()['values']
        values['sensorPosition'] = [[3, 0, 30]]
        self.request({'type': 'config_set', 'values': values})

        # Someone hand-mangles the persisted file beyond parsing.
        working_path = os.path.join(self.data_dir,
                                    os.listdir(self.data_dir)[0])
        with open(working_path, 'w', encoding='ascii', newline='') as f:
            f.write('garbage that does not parse\r\n')

        host2 = self.start_host()
        with connect(f'ws://127.0.0.1:{host2.actual_port}/',
                     open_timeout=5) as ws:
            ws.send(json.dumps({'type': 'config_get'}))
            reply = None
            for _ in range(50):
                message = json.loads(ws.recv(timeout=5))
                if message['type'] not in ('frame', 'stage'):
                    reply = message
                    break

        # The tool serves the re-seeded template defaults instead of dying.
        self.assertEqual(reply['values']['sensorPosition'], [[2, 0, 15]])
        # The broken file was kept aside for inspection, not destroyed.
        names = os.listdir(self.data_dir)
        self.assertTrue(any(n.endswith('.corrupt') for n in names), names)


if __name__ == '__main__':
    unittest.main()
