#!/usr/bin/env python3
"""Dependency-free Linux environment checks and resource sampling."""

import argparse
import csv
import contextlib
import io
import datetime as dt
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import time

OUT = Path('runs')
FIELDS = ('timestamp_utc', 'elapsed_s', 'cpu_cores',
          'cgroup_memory_bytes', 'received_bytes', 'sent_bytes')


def read(path):
    try:
        return Path(path).read_text(encoding='utf-8').strip()
    except (OSError, UnicodeError):
        return None


def cpu_model():
    for line in (read('/proc/cpuinfo') or '').splitlines():
        key, _, value = line.partition(':')
        if key.strip() in ('model name', 'Hardware'):
            return value.strip()
    return 'unavailable'


def environment():
    """Only report non-secret system properties, never IPs or environment vars."""
    memory = {}
    for line in (read('/proc/meminfo') or '').splitlines():
        key, sep, value = line.partition(':')
        if sep:
            memory[key] = value.strip()
    try:
        visible_cpus = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        visible_cpus = os.cpu_count()
    try:
        free_bytes = shutil.disk_usage('.').free
    except OSError:
        free_bytes = None
    return {
        'system': platform.system(),
        'kernel': platform.release(),
        'architecture': platform.machine(),
        'cpu_model': cpu_model(),
        'visible_cpus': visible_cpus,
        'cgroup_cpu_quota': read('/sys/fs/cgroup/cpu.max'),
        'host_visible_memory_total': memory.get('MemTotal'),
        'cgroup_memory_limit_bytes': read('/sys/fs/cgroup/memory.max'),
        'cgroup_memory_current_bytes': read('/sys/fs/cgroup/memory.current'),
        'working_filesystem_free_bytes': free_bytes,
        'python': platform.python_version(),
    }


def env(as_json=False):
    info = environment()
    if as_json:
        print(json.dumps(info, indent=2, sort_keys=True))
    else:
        for key, value in info.items():
            print(key.replace('_', ' ').capitalize() + ':',
                  value if value is not None else 'unavailable')


def parse_network(raw):
    """Return non-loopback RX/TX counters; None means counters unavailable."""
    if raw is None:
        return None
    interfaces = {}
    for row in raw.splitlines()[2:]:
        name, colon, payload = row.partition(':')
        if not colon or name.strip() == 'lo':
            continue
        try:
            values = [int(value) for value in payload.split()]
        except ValueError:
            continue
        if len(values) >= 16:
            interfaces[name.strip()] = (values[0], values[8])
    return interfaces


def snapshot():
    stat = read('/sys/fs/cgroup/cpu.stat') or ''
    cpu_usec = None
    for line in stat.splitlines():
        label, _, value = line.partition(' ')
        if label == 'usage_usec':
            try:
                cpu_usec = int(value)
            except ValueError:
                pass
            break
    mem_text = read('/sys/fs/cgroup/memory.current')
    mem_bytes = int(mem_text) if mem_text and mem_text.isdigit() else None
    return cpu_usec, mem_bytes, parse_network(read('/proc/net/dev'))


def delta_net(now, old, index):
    if now is None or old is None:
        return None
    return sum(max(0, value[index] - old[name][index])
               for name, value in now.items() if name in old)


def stop_child(proc, sig):
    """Signal the full launched process group, without killing the monitor."""
    if proc is None or proc.poll() is not None:
        return
    for signum in (sig, signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, signum)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=3)
            return
        except subprocess.TimeoutExpired:
            continue


class Terminated(Exception):
    def __init__(self, signum):
        self.signum = signum


def record(interval, command, output=None):
    if output:
        path = Path(output)
    else:
        stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        path = OUT / (stamp + '-' + str(os.getpid()) + '.csv')
    path.parent.mkdir(parents=True, exist_ok=True)
    print('Writing:', path, flush=True)
    proc = None
    original_term = signal.getsignal(signal.SIGTERM)

    def on_term(signum, _frame):
        raise Terminated(signum)

    with path.open('x', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        f.flush()
        previous = snapshot()
        start = last = time.monotonic()
        signal.signal(signal.SIGTERM, on_term)
        try:
            if command:
                proc = subprocess.Popen(command, start_new_session=True)
            while True:
                if proc is not None:
                    try:
                        proc.wait(timeout=interval)
                    except subprocess.TimeoutExpired:
                        pass
                else:
                    time.sleep(interval)
                now = time.monotonic()
                current = snapshot()
                elapsed = max(now - last, 1e-9)
                cpu = None
                if current[0] is not None and previous[0] is not None:
                    cpu = round(max(0, current[0] - previous[0]) / 1e6 / elapsed, 4)
                rx = delta_net(current[2], previous[2], 0)
                tx = delta_net(current[2], previous[2], 1)
                writer.writerow(dict(
                    timestamp_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                    elapsed_s=round(now - start, 3),
                    cpu_cores=cpu if cpu is not None else '',
                    cgroup_memory_bytes=current[1] if current[1] is not None else '',
                    received_bytes=rx if rx is not None else '',
                    sent_bytes=tx if tx is not None else '',
                ))
                f.flush()  # Flush userspace buffers; host storage may still be ephemeral.
                previous, last = current, now
                if proc is not None and proc.poll() is not None:
                    # Map a child terminated by signal N to conventional 128+N.
                    return 128 - proc.returncode if proc.returncode < 0 else proc.returncode
        except KeyboardInterrupt:
            stop_child(proc, signal.SIGINT)
            return 130
        except Terminated as exc:
            stop_child(proc, exc.signum)
            return 128 + exc.signum
        finally:
            signal.signal(signal.SIGTERM, original_term)


def summary(path):
    count = 0
    last_elapsed = 0.0
    core_seconds = 0.0
    measured_seconds = 0.0
    peak_cores = None
    peak_memory = None
    received = sent = 0
    network_measured = False
    with Path(path).open(newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            count += 1
            elapsed = float(row['elapsed_s'])
            duration = max(0.0, elapsed - last_elapsed)
            last_elapsed = elapsed
            if row.get('cpu_cores'):
                cores = float(row['cpu_cores'])
                measured_seconds += duration
                core_seconds += cores * duration
                peak_cores = max(peak_cores, cores) if peak_cores is not None else cores
            if row.get('cgroup_memory_bytes'):
                mem = int(row['cgroup_memory_bytes'])
                peak_memory = max(peak_memory, mem) if peak_memory is not None else mem
            if row.get('received_bytes') and row.get('sent_bytes'):
                received += int(row['received_bytes'])
                sent += int(row['sent_bytes'])
                network_measured = True
    if not count:
        print('No samples in', path)
        return
    print('Samples:', count, '| Duration:', round(last_elapsed, 2), 's')
    if measured_seconds > 0:
        print('CPU cores: average', round(core_seconds / measured_seconds, 3),
              '| peak', peak_cores, '(sample average)')
    else:
        print('CPU cores: unavailable (cgroup v2 may be inaccessible)')
    if peak_memory is not None:
        print('Peak cgroup memory:', round(peak_memory / 1024**2, 1), 'MiB')
    if network_measured:
        print('Network received / sent:', round(received / 1024**2, 3), '/',
              round(sent / 1024**2, 3), 'MiB')
    else:
        print('Network: unavailable')



def run_task(interval, command, output_dir=None):
    """One-command run: capture environment, task resources and a final summary."""
    if not command:
        raise ValueError('run requires a task command after --')
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    folder = Path(output_dir) if output_dir else OUT / (stamp + '-' + str(os.getpid()))
    folder.mkdir(parents=True, exist_ok=False)
    print('Run directory:', folder, flush=True)
    (folder / 'environment.json').write_text(
        json.dumps(environment(), indent=2, sort_keys=True) + '\n', encoding='utf-8')
    start = time.monotonic()
    started_utc = dt.datetime.now(dt.timezone.utc).isoformat()
    error = None
    try:
        code = record(interval, command, folder / 'resources.csv')
    except OSError as exc:
        # e.g. command not found. Preserve diagnosis even if the command never starts.
        code = 127
        error = str(exc)
    duration = round(time.monotonic() - start, 3)
    result = {
        'started_utc': started_utc,
        'finished_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'duration_s': duration,
        'interval_s': interval,
        'exit_code': code,
        'status': 'completed' if code == 0 else 'failed',
    }
    if error:
        result['error'] = error
    (folder / 'result.json').write_text(
        json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        summary(folder / 'resources.csv')
    report = output.getvalue()
    (folder / 'summary.txt').write_text(report, encoding='utf-8')
    print(report, end='')
    print('Task exit code:', code)
    if error:
        print('Task launch error:', error, file=sys.stderr)
    print('Saved environment.json, resources.csv, summary.txt, result.json to:', folder)
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='action', required=True)
    env_parser = subs.add_parser('env', help='print a safe environment report')
    env_parser.add_argument('--json', action='store_true', help='machine-readable report')
    mon = subs.add_parser('record', help='sample until interrupted or command exits')
    mon.add_argument('--interval', type=float, default=10, help='seconds (>= 1)')
    mon.add_argument('--output', help='CSV path; default runs/<UTC-time>-<PID>.csv')
    mon.add_argument('command', nargs=argparse.REMAINDER, help='command after --')
    one = subs.add_parser('run', help='run task and save environment, CSV, summary and result')
    one.add_argument('--interval', type=float, default=10, help='seconds (>= 1)')
    one.add_argument('--output-dir', help='new directory for this run (must not exist)')
    one.add_argument('command', nargs=argparse.REMAINDER, help='required command after --')
    summ = subs.add_parser('summary', help='summarize a saved CSV')
    summ.add_argument('file')
    args = parser.parse_args(argv)
    try:
        if args.action == 'env':
            env(args.json)
        elif args.action in ('record', 'run'):
            if not 1 <= args.interval <= 86400:
                parser.error('interval must be between 1 and 86400 seconds')
            command = args.command[1:] if args.command and args.command[0] == '--' else args.command
            if args.action == 'run' and not command:
                parser.error('run requires a command after --')
            if args.command == ['--']:
                parser.error('expected a command after --')
            if args.action == 'run':
                return run_task(args.interval, command, args.output_dir)
            return record(args.interval, command, args.output)
        else:
            summary(args.file)
    except OSError as exc:
        parser.exit(1, 'cloudstat: ' + str(exc) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
