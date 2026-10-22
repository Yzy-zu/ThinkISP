"""Smoke tests del proceso CLI con un nmcli simulado; nunca usa la red real."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent


class TestCliProcess(unittest.TestCase):
    def invoke(self, command, fail_modify=False):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            fake = path / 'nmcli'
            log = path / 'calls.jsonl'
            script = f'''#!{sys.executable}
import json, sys
from pathlib import Path
with Path({str(log)!r}).open('a') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
if {fail_modify!r} and 'modify' in sys.argv:
    print('Not authorized to modify connections', file=sys.stderr)
    sys.exit(4)
sys.exit(0)
'''
            fake.write_text(script)
            fake.chmod(0o755)
            env = {**os.environ, 'PATH': str(path), 'THINKISP_WLAN': 'wifi-test',
                   'THINKISP_ETH': 'upstream-test', 'THINKISP_PROFILE': 'process-test',
                   'XDG_STATE_HOME': str(path / 'state')}
            result = subprocess.run([sys.executable, str(ROOT / 'main.py'), *command],
                                    input='', capture_output=True, text=True, env=env, timeout=10)
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            files = list((path / 'state').rglob('state.json')) if (path / 'state').exists() else []
            return result, calls, files

    def test_noninteractive_first_run_never_creates_or_starts_an_ap(self):
        result, calls, files = self.invoke(['on'])
        self.assertEqual(result.returncode, 1)
        self.assertIn('thinkisp setup', result.stderr)
        self.assertFalse(any('add' in call or 'up' in call for call in calls))
        self.assertEqual(files, [])

    def test_permission_error_reaches_stderr_and_exit_code(self):
        result, calls, files = self.invoke(['gaming', 'on'], fail_modify=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('Not authorized', result.stderr)
        self.assertNotIn('activado', result.stdout)
        self.assertNotIn('Traceback', result.stderr)
        self.assertEqual(files, [])


if __name__ == '__main__':
    unittest.main()
