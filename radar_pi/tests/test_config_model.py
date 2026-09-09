#!/usr/bin/env python3
"""
Config model tests (issue #10): schema coverage, faithful cfg round-trip,
and per-field validation.

The schema must describe *every* parameter of the shipped 6 m people-tracking
cfg, and structured values <-> cfg file text must convert into each other
faithfully — including byte-exact reproduction of the vendor template (same
comments, same CRLF endings, same vendor quirks like the trailing space on
the profileCfg line).

Run from the radar_pi/ directory:

    python3 -m unittest tests.test_config_model -v
"""

import copy
import os
import unittest

from setup_tool.config_model import (
    CONTROL_COMMANDS,
    generate_cfg,
    normalize_values,
    parse_cfg,
    schema_json,
)
from setup_tool.config_schema import SCHEMA, TIERS, defaults

#: The pristine vendor template — the single source of cfg truth in the repo.
TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'xwr6843_aop_3d_people_track_6m.cfg')


def read_template():
    with open(TEMPLATE_PATH, 'rb') as f:
        return f.read()


class SchemaCoverageTests(unittest.TestCase):
    """The schema covers every parameter in the shipped cfg."""

    def setUp(self):
        self.template = read_template().decode('ascii')
        self.parsed = parse_cfg(self.template)
        self.commands = set()
        for line in self.template.splitlines():
            line = line.strip()
            if not line or line.startswith('%') or line.split()[0] in CONTROL_COMMANDS:
                continue
            self.commands.add(line.split()[0])

    def test_every_template_command_is_in_schema(self):
        self.assertEqual(self.commands, {p.command for p in SCHEMA})

    def test_instance_counts_match_template(self):
        counts = {}
        for line in self.template.splitlines():
            line = line.strip()
            if not line or line.startswith('%') or line.split()[0] in CONTROL_COMMANDS:
                continue
            counts[line.split()[0]] = counts.get(line.split()[0], 0) + 1
        for param in SCHEMA:
            self.assertEqual(
                counts.get(param.command, 0), param.instances,
                f'{param.command}: template has {counts.get(param.command, 0)} '
                f'lines, schema says {param.instances}')

    def test_every_param_has_tier_type_range_default_info(self):
        valid_tiers = {t.id for t in TIERS}
        for param in SCHEMA:
            self.assertIn(param.tier, valid_tiers, param.command)
            self.assertTrue(param.title, param.command)
            self.assertTrue(param.info, f'{param.command}: no info text')
            for field in param.fields:
                self.assertIn(field.type, ('int', 'float'),
                              f'{param.command}.{field.key}')
                self.assertIsNotNone(field.minimum,
                                     f'{param.command}.{field.key}: no range')
                self.assertIsNotNone(field.maximum,
                                     f'{param.command}.{field.key}: no range')
                self.assertLessEqual(field.minimum, field.default,
                                     f'{param.command}.{field.key}')
                self.assertGreaterEqual(field.maximum, field.default,
                                        f'{param.command}.{field.key}')
                self.assertTrue(field.label, f'{param.command}.{field.key}')

    def test_schema_defaults_match_template_values(self):
        self.assertEqual(defaults(), self.parsed)


class RoundTripTests(unittest.TestCase):
    """Structured params -> cfg text -> parsed params stays identical."""

    def setUp(self):
        self.template_bytes = read_template()
        self.template = self.template_bytes.decode('ascii')
        self.defaults = parse_cfg(self.template)

    def test_generate_from_defaults_is_byte_exact_template(self):
        self.assertEqual(generate_cfg(self.defaults).encode('ascii'),
                         self.template_bytes)

    def test_modified_values_round_trip(self):
        values = copy.deepcopy(self.defaults)
        values['boundaryBox'] = [[-5.5, 5.5, 0.25, 9.75, 0, 3.5]]
        values['sensorPosition'] = [[2.4, 5, 15]]
        values['gatingParam'] = [[2.5, 2, 2, 3, 4.5]]
        values['chirpCfg'] = [[0, 0, 0, 0, 0, 0, 0, 1],
                              [1, 1, 0, 0, 0, 0, 0, 2],
                              [2, 2, 0, 0, 0, 0, 0, 4]]
        values['frameCfg'] = [[0, 2, 96, 0, 60.00, 1, 0]]

        text = generate_cfg(values)
        self.assertEqual(parse_cfg(text), values)

    def test_parse_rejects_unknown_command(self):
        with self.assertRaises(ValueError) as ctx:
            parse_cfg('sensorStop\nmagicWand 1 2\n')
        self.assertIn('magicWand', str(ctx.exception))

    def test_parse_rejects_wrong_arg_count(self):
        with self.assertRaises(ValueError):
            parse_cfg('channelCfg 15 7\n')


class ValidationTests(unittest.TestCase):
    """Invalid values are rejected; valid ones normalize to typed values."""

    def setUp(self):
        self.defaults = parse_cfg(read_template().decode('ascii'))

    def normalize(self, values):
        return normalize_values(values)

    def test_defaults_pass_validation(self):
        values, errors = self.normalize(self.defaults)
        self.assertEqual(errors, [])
        self.assertEqual(values, self.defaults)

    def test_out_of_range_rejected(self):
        values = copy.deepcopy(self.defaults)
        values['sensorPosition'] = [[99, 0, 15]]        # height way out of range
        _, errors = self.normalize(values)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]['command'], 'sensorPosition')
        self.assertEqual(errors[0]['field'], 'z')
        self.assertIn('99', errors[0]['message'])

    def test_string_garbage_rejected(self):
        values = copy.deepcopy(self.defaults)
        values['boundaryBox'] = [['-4', 'abc', 0, 8, 0, 3]]
        _, errors = self.normalize(values)
        self.assertEqual(errors[0]['command'], 'boundaryBox')
        self.assertEqual(errors[0]['field'], 'x_max')

    def test_float_into_int_field_rejected(self):
        values = copy.deepcopy(self.defaults)
        values['stateParam'] = [[3.5, 3, 12, 500, 5, 6000]]
        _, errors = self.normalize(values)
        self.assertEqual(errors[0]['field'], 'det2act_thre')

    def test_numeric_strings_accepted_and_normalized(self):
        """The editor sends raw input strings; numbers come back."""
        values = copy.deepcopy(self.defaults)
        values['boundaryBox'] = [['-4', '4', '0', '8', '0', '3']]
        normalized, errors = self.normalize(values)
        self.assertEqual(errors, [])
        self.assertEqual(normalized['boundaryBox'], [[-4, 4, 0, 8, 0, 3]])

    def test_unknown_command_rejected(self):
        values = copy.deepcopy(self.defaults)
        values['magicWand'] = [[1]]
        _, errors = self.normalize(values)
        self.assertEqual(errors[0]['command'], 'magicWand')

    def test_missing_command_rejected(self):
        values = copy.deepcopy(self.defaults)
        del values['boundaryBox']
        _, errors = self.normalize(values)
        self.assertTrue(any(e['command'] == 'boundaryBox'
                            and 'missing' in e['message'] for e in errors))

    def test_wrong_instance_count_rejected(self):
        values = copy.deepcopy(self.defaults)
        values['chirpCfg'] = values['chirpCfg'][:2]
        _, errors = self.normalize(values)
        self.assertTrue(any(e['command'] == 'chirpCfg' for e in errors))


class SchemaJsonTests(unittest.TestCase):
    """The editor consumes a JSON description of the schema."""

    def test_json_shape(self):
        payload = schema_json()
        self.assertEqual([t['id'] for t in payload['tiers']],
                         [t.id for t in TIERS])
        self.assertTrue(all(t['label'] and t['blurb']
                            for t in payload['tiers']))

        by_command = {p['command']: p for p in payload['params']}
        self.assertIn('boundaryBox', by_command)
        box = by_command['boundaryBox']
        self.assertEqual(box['tier'], 'room_mounting')
        self.assertEqual(len(box['fields']), 6)
        field = box['fields'][0]
        for key in ('key', 'label', 'type', 'fmt', 'default',
                    'min', 'max', 'info'):
            self.assertIn(key, field)
        self.assertEqual(field['type'], 'float')

        chirp = by_command['chirpCfg']
        self.assertEqual(chirp['instances'], 3)

    def test_json_tiers_present(self):
        tiers = {p['tier'] for p in schema_json()['params']}
        self.assertEqual(tiers, {t.id for t in TIERS})


if __name__ == '__main__':
    unittest.main()
