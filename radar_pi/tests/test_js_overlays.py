"""Bridge the plain-node tests for static/overlays.js into the pytest run.

tests/js/run_tests.js is a zero-dependency node script (the Pi repo carries
no JS toolchain, so nothing installs one); when node exists on the machine
it runs as part of this suite, otherwise it skips — the same env-gated
pattern as the Docker-gated store integration tests.
"""

import shutil
import subprocess
import unittest
from pathlib import Path

JS_TESTS = Path(__file__).resolve().parent / 'js' / 'run_tests.js'


@unittest.skipUnless(shutil.which('node'), 'node not installed')
class OverlaysJsTests(unittest.TestCase):

    def test_overlays_math(self):
        result = subprocess.run(
            ['node', str(JS_TESTS)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0,
                         result.stdout + result.stderr)
        self.assertIn('passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
