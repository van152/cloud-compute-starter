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

    def test_cgroup_v1_fallback(self):
        files = {
            '/sys/fs/cgroup/cpuacct/cpuacct.usage': '7000000000',
            '/sys/fs/cgroup/memory/memory.usage_in_bytes': '8192',
            '/sys/fs/cgroup/memory/memory.limit_in_bytes': '9223372036854771712',
            '/sys/fs/cgroup/cpu/cpu.cfs_quota_us': '-1',
            '/sys/fs/cgroup/cpu/cpu.cfs_period_us': '100000',
        }
        with patch.object(cloudstat, 'read', side_effect=files.get):
            self.assertEqual(cloudstat.cpu_usage(), (7000000, 'cgroup_v1'))
            self.assertEqual(cloudstat.memory_usage(), (8192, 'cgroup_v1'))
            self.assertEqual(cloudstat.memory_limit(), 'max')
            self.assertEqual(cloudstat.cpu_quota(), 'max 100000')
        files['/sys/fs/cgroup/cpu/cpu.cfs_quota_us'] = '200000'
        files['/sys/fs/cgroup/memory/memory.limit_in_bytes'] = '1073741824'
        with patch.object(cloudstat, 'read', side_effect=files.get):
            self.assertEqual(cloudstat.cpu_quota(), '200000 100000')
            self.assertEqual(cloudstat.memory_limit(), '1073741824')

    def test_comounted_cpu_cpuacct_v1(self):
        files = {'/sys/fs/cgroup/cpu,cpuacct/cpuacct.usage': '5000'}
        with patch.object(cloudstat, 'read', side_effect=files.get):
            self.assertEqual(cloudstat.cpu_usage(), (5, 'cgroup_v1'))

    def test_host_proc_fallback_without_cgroups(self):
        files = {
            '/proc/stat': 'cpu  100 10 50 1000 40 5 5 30 0 0\ncpu0 1 1 1 1',
            '/proc/meminfo': 'MemTotal: 1000 kB\nMemFree: 100 kB\nMemAvailable: 400 kB',
        }
        with patch.object(cloudstat, 'read', side_effect=files.get), \
                patch.object(cloudstat.os, 'sysconf', return_value=100):
            # busy = user+nice+system+irq+softirq+steal = 200 ticks = 2 s.
            self.assertEqual(cloudstat.cpu_usage(), (2000000, 'host_proc'))
            self.assertEqual(cloudstat.memory_usage(), (600 * 1024, 'host_proc'))

    def test_nothing_readable_reports_unavailable(self):
        with patch.object(cloudstat, 'read', return_value=None):
            self.assertEqual(cloudstat.cpu_usage(), (None, None))
            self.assertEqual(cloudstat.memory_usage(), (None, None))
            self.assertIsNone(cloudstat.cpu_quota())
            env = cloudstat.environment()
            self.assertEqual(env['cpu_metric_source'], 'unavailable')
            self.assertEqual(env['memory_metric_source'], 'unavailable')

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



class OneCommandRunTests(unittest.TestCase):
    def test_run_creates_all_reports_from_a_short_task(self):
        import json
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'run-001'
            with contextlib.redirect_stdout(StringIO()):
                code = cloudstat.run_task(1, [sys.executable, '-c', 'print("done")'], target)
            self.assertEqual(code, 0)
            self.assertEqual(sorted(p.name for p in target.iterdir()), [
                'environment.json', 'resources.csv', 'result.json', 'summary.txt'])
            result = json.loads((target / 'result.json').read_text())
            self.assertEqual(result['exit_code'], 0)
            self.assertEqual(result['status'], 'completed')
            self.assertIn('cpu_model', json.loads((target / 'environment.json').read_text()))
            self.assertIn('Samples: 1', (target / 'summary.txt').read_text())

    def test_run_failure_still_writes_reports(self):
        import json
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'failed'
            with contextlib.redirect_stdout(StringIO()):
                code = cloudstat.run_task(1, [sys.executable, '-c', 'import sys;sys.exit(23)'], target)
            self.assertEqual(code, 23)
            result = json.loads((target / 'result.json').read_text())
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['exit_code'], 23)
            self.assertTrue((target / 'resources.csv').exists())

    def test_run_missing_binary_exits_127_with_error_report(self):
        import json
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'missing'
            with contextlib.redirect_stdout(StringIO()), contextlib.redirect_stderr(StringIO()):
                code = cloudstat.run_task(1, ['definitely_missing_cloudstat_command_12345'], target)
            self.assertEqual(code, 127)
            result = json.loads((target / 'result.json').read_text())
            self.assertEqual(result['exit_code'], 127)
            self.assertTrue(result['error'])
            self.assertIn('No samples', (target / 'summary.txt').read_text())

    def test_run_wont_overwrite_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'existing'
            target.mkdir()
            sentinel = target / 'sentinel.txt'
            sentinel.write_text('keep')
            with self.assertRaises(FileExistsError):
                cloudstat.run_task(1, [sys.executable, '-c', 'pass'], target)
            self.assertEqual(sentinel.read_text(), 'keep')

    def test_signal_exit_code_maps_to_shell_convention(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'signaled.csv'
            command = [sys.executable, '-c',
                       'import os,signal;os.kill(os.getpid(),signal.SIGTERM)']
            with contextlib.redirect_stdout(StringIO()):
                code = cloudstat.record(1, command, target)
            self.assertEqual(code, 143)

    def test_cli_run_requires_command(self):
        result = subprocess.run([sys.executable, 'cloudstat.py', 'run', '--'],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('requires a command', result.stderr)

if __name__ == '__main__':
    unittest.main()
