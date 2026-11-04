"""Run locally: python3 -m unittest discover -s tests -v"""
import contextlib
import csv
from io import StringIO
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import cloudstat


class CloudstatTests(unittest.TestCase):
    def test_network_ignores_loopback_and_bad_lines(self):
        raw = ('Inter-| Receive | Transmit\n face |bytes...\n'
               ' lo: 9 0 0 0 0 0 0 0 8 0 0 0 0 0 0 0\n'
               'eth0: 120 0 0 0 0 0 0 0 70 0 0 0 0 0 0 0\n'
               'bad: invalid\n')
        self.assertEqual(cloudstat.parse_network(raw), {'eth0': (120, 70)})
        self.assertIsNone(cloudstat.parse_network(None))

    def test_network_handles_reset_and_new_interface(self):
        old = {'eth0': (100, 200)}
        now = {'eth0': (90, 250), 'eth1': (5000, 5000)}
        self.assertEqual(cloudstat.delta_net(now, old, 0), 0)
        self.assertEqual(cloudstat.delta_net(now, old, 1), 50)
        self.assertIsNone(cloudstat.delta_net(None, old, 0))

    def test_snapshot_parses_cgroup_and_network(self):
        def fake_read(path):
            return {
                '/sys/fs/cgroup/cpu.stat': 'usage_usec 123456\nuser_usec 20',
                '/sys/fs/cgroup/memory.current': '4096',
                '/proc/net/dev': ('header\nheader\n'
                    'eth0: 11 0 0 0 0 0 0 0 21 0 0 0 0 0 0 0')
            }.get(path)
        with patch.object(cloudstat, 'read', side_effect=fake_read):
            self.assertEqual(cloudstat.snapshot(), (123456, 4096, {'eth0': (11, 21)}))

    def test_summary_weights_only_valid_cpu_intervals(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'sample.csv'
            with path.open('w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=cloudstat.FIELDS)
                writer.writeheader()
                writer.writerow(dict(elapsed_s='2', cpu_cores='1',
                     cgroup_memory_bytes='1048576', received_bytes='4', sent_bytes='5'))
                writer.writerow(dict(elapsed_s='4', cpu_cores='',
                     cgroup_memory_bytes='2097152', received_bytes='', sent_bytes=''))
                writer.writerow(dict(elapsed_s='5', cpu_cores='3',
                     cgroup_memory_bytes='524288', received_bytes='6', sent_bytes='7'))
            buf = StringIO()
            with contextlib.redirect_stdout(buf):
                cloudstat.summary(path)
            self.assertIn('average 1.667', buf.getvalue())
            self.assertIn('peak 3.0', buf.getvalue())
            self.assertIn('2.0 MiB', buf.getvalue())

    def test_env_json(self):
        with patch.object(cloudstat, 'environment', return_value={'visible_cpus': 4}):
            buf = StringIO()
            with contextlib.redirect_stdout(buf):
                cloudstat.env(as_json=True)
            self.assertIn('"visible_cpus": 4', buf.getvalue())

    def test_record_preserves_exit_code_and_csv(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'record.csv'
            with contextlib.redirect_stdout(StringIO()):
                code = cloudstat.record(1, [sys.executable, '-c', 'import sys; sys.exit(7)'], path)
            self.assertEqual(code, 7)
            with path.open(newline='') as f:
                rows = list(csv.DictReader(f))
            self.assertGreaterEqual(len(rows), 1)
            self.assertEqual(set(rows[0]), set(cloudstat.FIELDS))

    def test_record_does_not_overwrite_csv(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'record.csv'
            path.write_text('important')
            with contextlib.redirect_stdout(StringIO()):
                with self.assertRaises(FileExistsError):
                    cloudstat.record(1, None, path)
            self.assertEqual(path.read_text(), 'important')

    def test_cli_rejects_bad_interval(self):
        result = subprocess.run([sys.executable, 'cloudstat.py', 'record',
                '--interval', '0'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('interval must be', result.stderr)


if __name__ == '__main__':
    unittest.main()
