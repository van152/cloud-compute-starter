"""Exercises the actual detached worker, not mocked stdout or counters."""
import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import cloudstat

SCRIPT = Path(cloudstat.__file__).resolve()


class BackgroundTests(unittest.TestCase):
    def cli(self, cwd, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              cwd=cwd, text=True, capture_output=True, timeout=15)

    def test_detached_lifecycle_and_duplicate_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            started = self.cli(tmp, 'start', '--interval', '1')
            self.assertEqual(started.returncode, 0, started.stderr)
            self.assertIn('Background recording started', started.stdout)
            try:
                status = self.cli(tmp, 'status', '--json')
                self.assertEqual(status.returncode, 0)
                info = json.loads(status.stdout)
                self.assertEqual(info['state'], 'running')
                self.assertTrue(cloudstat.is_active(info))
                second = self.cli(tmp, 'start')
                self.assertEqual(second.returncode, 0, second.stderr)
                self.assertIn('Already recording', second.stdout)
                self.assertEqual(info['pid'], json.loads(self.cli(tmp, 'status', '--json').stdout)['pid'])
                time.sleep(2.2)
            finally:
                stopped = self.cli(tmp, 'stop')
                self.assertEqual(stopped.returncode, 0, stopped.stderr)
            final = json.loads(self.cli(tmp, 'status', '--json').stdout)
            self.assertEqual(final['state'], 'stopped')
            folder = Path(final['directory'])
            self.assertEqual({p.name for p in folder.iterdir()},
                             {'environment.json', 'resources.csv', 'events.jsonl',
                              'result.json', 'summary.txt', 'daemon.log'})
            with (folder / 'resources.csv').open(newline='') as f:
                rows = list(csv.DictReader(f))
            self.assertGreaterEqual(len(rows), 2)
            events = [json.loads(line) for line in (folder / 'events.jsonl').read_text().splitlines()]
            self.assertEqual([e['event'] for e in events], ['started', 'stopped'])
            self.assertIn('Samples:', (folder / 'summary.txt').read_text())
            self.assertEqual(self.cli(tmp, 'stop').returncode, 0)
            self.assertFalse(cloudstat.is_active(final))

    def test_status_without_active_monitor(self):
        with tempfile.TemporaryDirectory() as tmp:
            status = json.loads(self.cli(tmp, 'status', '--json').stdout)
            self.assertEqual(status['state'], 'not_started')
            self.assertIn('No active', self.cli(tmp, 'stop').stdout)

    def test_interrupted_process_state_and_pid_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(cloudstat, 'OUT', Path(tmp)):
                folder = cloudstat.background_root()
                folder.mkdir(parents=True)
                cloudstat.atomic_json(folder / 'current.json', {
                    'state': 'running', 'pid': 999999999, 'start_ticks': 1,
                    'directory': str(folder)})
                import io
                import contextlib
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    cloudstat.background_status(as_json=True)
                self.assertEqual(json.loads(buf.getvalue())['state'], 'interrupted')
                self.assertFalse(cloudstat.is_active({'state':'running', 'pid':-1, 'start_ticks':1}))


if __name__ == '__main__':
    unittest.main()
