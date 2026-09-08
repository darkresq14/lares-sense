#!/usr/bin/env python3
"""
Unit tests for radar_persist.parse_args / load_env_file (no socket, no DB).

The persister takes its configuration from four layers, most specific first:

    CLI argument > environment variable > .env file > built-in default

with ``DATABASE_URL`` (DSN), ``RADAR_PORT``, ``RADAR_BIND``,
``RADAR_BATCH_SIZE`` and ``RADAR_FLUSH_INTERVAL`` as the environment names.
These tests pin that precedence using a temp working directory, so a stray
``.env`` in the repo cannot leak in.

Run from the radar_pi/ directory:

    python3 -m unittest tests.test_radar_persist -v
"""

import os
import pathlib
import tempfile
import unittest

from radar_persist import load_env_file, parse_args

ENV_KEYS = ('DATABASE_URL', 'RADAR_PORT', 'RADAR_BIND',
            'RADAR_BATCH_SIZE', 'RADAR_FLUSH_INTERVAL')


class EnvIsolatedTest(unittest.TestCase):
    """Run each test in a temp cwd with the RADAR_*/DATABASE_URL env cleared."""

    def setUp(self):
        self._saved = {k: os.environ.pop(k, None) for k in ENV_KEYS}
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _write_env_file(self, text):
        path = pathlib.Path('.env')
        path.write_text(text, encoding='utf-8')
        return str(path)


class ParseArgsTests(EnvIsolatedTest):

    def test_defaults_when_nothing_set(self):
        args = parse_args(['--dsn', 'postgresql://x/y'])
        self.assertEqual(args.port, 5005)
        self.assertEqual(args.bind, '0.0.0.0')
        self.assertEqual(args.batch_size, 50)
        self.assertEqual(args.flush_interval, 1.0)

    def test_missing_dsn_is_an_error(self):
        with self.assertRaises(SystemExit):
            parse_args([])

    def test_environment_variables_fill_every_option(self):
        os.environ['DATABASE_URL'] = 'postgresql://u:p@db/lares'
        os.environ['RADAR_PORT'] = '6006'
        os.environ['RADAR_BIND'] = '127.0.0.1'
        os.environ['RADAR_BATCH_SIZE'] = '10'
        os.environ['RADAR_FLUSH_INTERVAL'] = '0.25'
        args = parse_args([])
        self.assertEqual(args.dsn, 'postgresql://u:p@db/lares')
        self.assertEqual(args.port, 6006)
        self.assertEqual(args.bind, '127.0.0.1')
        self.assertEqual(args.batch_size, 10)
        self.assertEqual(args.flush_interval, 0.25)

    def test_cli_argument_beats_environment(self):
        os.environ['DATABASE_URL'] = 'postgresql://from-env/db'
        os.environ['RADAR_PORT'] = '6006'
        args = parse_args(['--dsn', 'postgresql://from-arg/db',
                           '--port', '7007'])
        self.assertEqual(args.dsn, 'postgresql://from-arg/db')
        self.assertEqual(args.port, 7007)

    def test_env_file_supplies_missing_values(self):
        self._write_env_file(
            '# server config\n'
            'DATABASE_URL=postgresql://from-file/db\n'
            'RADAR_PORT=6006\n'
            'RADAR_FLUSH_INTERVAL=0.5\n')
        args = parse_args([])
        self.assertEqual(args.dsn, 'postgresql://from-file/db')
        self.assertEqual(args.port, 6006)
        self.assertEqual(args.flush_interval, 0.5)

    def test_environment_beats_env_file(self):
        os.environ['RADAR_PORT'] = '6006'
        self._write_env_file('RADAR_PORT=7007\n')
        args = parse_args(['--dsn', 'postgresql://x/y'])
        self.assertEqual(args.port, 6006)

    def test_quoted_env_file_values_are_unquoted(self):
        self._write_env_file('RADAR_BIND="0.0.0.0"\n')
        args = parse_args(['--dsn', 'postgresql://x/y'])
        self.assertEqual(args.bind, '0.0.0.0')

    def test_explicit_env_file_missing_is_an_error(self):
        with self.assertRaises(SystemExit):
            parse_args(['--env-file', 'nope.env'])

    def test_default_dotenv_absent_is_silently_skipped(self):
        # No .env in the temp cwd; plain args still parse.
        args = parse_args(['--dsn', 'postgresql://x/y'])
        self.assertEqual(args.port, 5005)

    def test_bad_numeric_env_is_an_error(self):
        os.environ['RADAR_PORT'] = 'not-a-port'
        with self.assertRaises(SystemExit):
            parse_args(['--dsn', 'postgresql://x/y'])


class LoadEnvFileTests(EnvIsolatedTest):

    def test_loads_keys_and_preserves_existing_env(self):
        os.environ['KEEP_ME'] = 'from-env'
        try:
            loaded = load_env_file(
                self._write_env_file('KEEP_ME=from-file\nNEW_KEY=42\n'))
            self.assertEqual(os.environ['KEEP_ME'], 'from-env')
            self.assertEqual(os.environ['NEW_KEY'], '42')
            self.assertEqual(loaded, ['NEW_KEY'])
        finally:
            del os.environ['KEEP_ME']
            del os.environ['NEW_KEY']

    def test_skips_comments_blanks_and_keyless_lines(self):
        loaded = load_env_file(
            self._write_env_file('# comment\n\n=junk\nGOOD=1\n'))
        self.assertEqual(loaded, ['GOOD'])
        self.assertEqual(os.environ['GOOD'], '1')
        del os.environ['GOOD']


if __name__ == '__main__':
    unittest.main()
