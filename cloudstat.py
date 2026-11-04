#!/usr/bin/env python3
"""Dependency-free Linux environment checks and resource sampling."""

import argparse
import csv
import contextlib
import io
import fcntl
import tempfile
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
    current_memory, memory_source = memory_usage()
    if memory_source == 'host_proc':
        current_memory = None  # Keep this field cgroup-only; host total is reported above.
    return {
        'system': platform.system(),
        'kernel': platform.release(),
        'architecture': platform.machine(),
        'cpu_model': cpu_model(),
        'visible_cpus': visible_cpus,
        'cgroup_cpu_quota': cpu_quota(),
        'host_visible_memory_total': memory.get('MemTotal'),
        'cgroup_memory_limit_bytes': memory_limit(),
        'cgroup_memory_current_bytes': current_memory,
        'cpu_metric_source': cpu_usage()[1] or 'unavailable',
        'memory_metric_source': memory_source or 'unavailable',
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


CG = '/sys/fs/cgroup'
# cgroup v1 controllers may be mounted separately or co-mounted as "cpu,cpuacct".
V1_CPUACCT = (CG + '/cpuacct', CG + '/cpu,cpuacct', CG + '/cpu')
V1_CPU = (CG + '/cpu', CG + '/cpu,cpuacct')
V1_UNLIMITED = 2 ** 60  # v1 reports "no limit" as a huge page-aligned number.


def as_int(text):
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def first(paths):
    for path in paths:
        value = read(path)
        if value is not None:
            return value
    return None


def cpu_usage():
    """Return (cumulative CPU microseconds, source).

    Order: cgroup v2 -> cgroup v1 cpuacct -> whole-host /proc/stat.
    """
    for line in (read(CG + '/cpu.stat') or '').splitlines():
        label, _, value = line.partition(' ')
        if label == 'usage_usec':
            usec = as_int(value)
            if usec is not None:
                return usec, 'cgroup_v2'
            break
    nsec = as_int(first(p + '/cpuacct.usage' for p in V1_CPUACCT))
    if nsec is not None:
        return nsec // 1000, 'cgroup_v1'
    for line in (read('/proc/stat') or '').splitlines():
        parts = line.split()
        if parts and parts[0] == 'cpu':
            # user nice system idle iowait irq softirq steal: busy excludes idle/iowait.
            ticks = [as_int(v) or 0 for v in parts[1:9]]
            ticks += [0] * (8 - len(ticks))
            busy = sum(ticks[:3]) + sum(ticks[5:8])
            try:
                hz = os.sysconf('SC_CLK_TCK')
            except (ValueError, OSError, AttributeError):
                hz = 100
            return busy * 1_000_000 // hz, 'host_proc'
    return None, None


def host_memory_used():
    info = {}
    for line in (read('/proc/meminfo') or '').splitlines():
        key, _, value = line.partition(':')
        info[key] = as_int(value.split()[0]) if value.split() else None
    total, available = info.get('MemTotal'), info.get('MemAvailable')
    if total is None or available is None:
        return None
    return max(0, total - available) * 1024


def memory_usage():
    """Return (current memory bytes, source); cgroup values include page cache."""
    value = as_int(read(CG + '/memory.current'))
    if value is not None:
        return value, 'cgroup_v2'
    value = as_int(read(CG + '/memory/memory.usage_in_bytes'))
    if value is not None:
        return value, 'cgroup_v1'
    value = host_memory_used()
    if value is not None:
        return value, 'host_proc'
    return None, None


def cpu_quota():
    """cgroup CPU quota in v2 "quota period" form ("max 100000" = unlimited)."""
    value = read(CG + '/cpu.max')
    if value is not None:
        return value
    quota = as_int(first(p + '/cpu.cfs_quota_us' for p in V1_CPU))
    period = as_int(first(p + '/cpu.cfs_period_us' for p in V1_CPU))
    if quota is None or period is None:
        return None
    return '%s %d' % ('max' if quota < 0 else quota, period)


def memory_limit():
    value = read(CG + '/memory.max')
    if value is not None:
        return value
    limit = as_int(read(CG + '/memory/memory.limit_in_bytes'))
    if limit is None:
        return None
    return 'max' if limit >= V1_UNLIMITED else str(limit)


def snapshot():
    return cpu_usage()[0], memory_usage()[0], parse_network(read('/proc/net/dev'))


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
        print('CPU cores: unavailable (no readable cgroup or /proc/stat counters)')
    if peak_memory is not None:
        print('Peak memory:', round(peak_memory / 1024**2, 1), 'MiB')
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



# Background recording is deliberately independent of any individual workload.
# Only one monitor is active per project working directory. All writes are local.
def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def background_root():
    return (OUT / 'background').resolve()


def process_identity(pid):
    """Return Linux /proc start ticks, or None for a missing/zombie process."""
    try:
        stat = Path('/proc') / str(pid) / 'stat'
        tail = stat.read_text().rsplit(')', 1)[1].split()
        if tail[0] in ('Z', 'X', 'x'):
            return None
        return int(tail[19])  # /proc/<pid>/stat field 22 (starttime).
    except (OSError, IndexError, ValueError):
        return None


def atomic_json(path, info):
    target = Path(path)
    # Different processes may write this on startup/shutdown. Never expose partial JSON.
    temp = target.with_name(target.name + '.' + str(os.getpid()) + '.tmp')
    try:
        with temp.open('x', encoding='utf-8') as f:
            json.dump(info, f, indent=2, sort_keys=True)
            f.write('\n')
            f.flush()
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def read_control(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return None


def is_active(info):
    if not info or info.get('state') != 'running':
        return False
    pid, start = info.get('pid'), info.get('start_ticks')
    return (isinstance(pid, int) and pid > 0 and isinstance(start, int)
            and process_identity(pid) == start)


def background_event(folder, event, **kwargs):
    data = dict(timestamp_utc=utc_now(), event=event, **kwargs)
    with (Path(folder) / 'events.jsonl').open('a', encoding='utf-8') as f:
        f.write(json.dumps(data, sort_keys=True) + '\n')
        f.flush()


def background_worker(interval, folder, control):
    """Private detached process; records until SIGTERM or cloud shutdown."""
    folder, control = Path(folder), Path(control)
    lock_path = control.parent / '.cloudstat.lock'
    with lock_path.open('a+b') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 2  # Another monitor already holds the lock.
        info = dict(state='running', pid=os.getpid(),
                    start_ticks=process_identity(os.getpid()),
                    started_utc=utc_now(), interval_s=interval,
                    directory=str(folder))
        atomic_json(control, info)
        error = None
        result_code = None
        try:
            atomic_json(folder / 'environment.json', environment())
            background_event(folder, 'started', pid=os.getpid(), interval_s=interval)
            result_code = record(interval, [], folder / 'resources.csv')
        except Exception as exc:
            error = '%s: %s' % (type(exc).__name__, exc)
            background_event(folder, 'error', message=error)
        finally:
            info['state'] = 'stopped' if result_code in (130, 143) else 'failed'
            info['finished_utc'] = utc_now()
            if error:
                info['error'] = error
            if result_code is not None:
                info['exit_code'] = result_code
            # A failure before CSV creation still leaves an actionable error message.
            if (folder / 'resources.csv').exists():
                report = io.StringIO()
                try:
                    with contextlib.redirect_stdout(report):
                        summary(folder / 'resources.csv')
                    (folder / 'summary.txt').write_text(report.getvalue(), encoding='utf-8')
                except (OSError, ValueError, KeyError) as exc:
                    info['summary_error'] = str(exc)
            atomic_json(folder / 'result.json', info)
            background_event(folder, info['state'], exit_code=result_code, error=error)
            # Only the owner holding the process lock can replace the active pointer.
            atomic_json(control, info)
        return 0 if info['state'] == 'stopped' else 1


def background_start(interval):
    root = background_root()
    root.mkdir(parents=True, exist_ok=True)
    control = root / 'current.json'
    current = read_control(control)
    if is_active(current):
        print('Already recording in background:', current['directory'])
        return 0
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ-')
    folder = Path(tempfile.mkdtemp(prefix=stamp, dir=root))
    folder.chmod(0o700)
    with (folder / 'daemon.log').open('x', encoding='utf-8') as out:
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), '_background_worker',
             str(interval), str(folder), str(control)],
            stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
            start_new_session=True, cwd=os.getcwd(), close_fds=True)
    # Startup handshake: show success only after the child has created its PID record.
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        latest = read_control(control)
        if latest and latest.get('pid') == child.pid and is_active(latest):
            print('Background recording started:', folder)
            return 0
        if child.poll() is not None:
            break
        time.sleep(0.05)
    latest = read_control(control)
    if is_active(latest):
        print('Already recording in background:', latest['directory'])
        return 0
    print('Could not start background monitor; see', folder / 'daemon.log', file=sys.stderr)
    return 1


def background_status(as_json=False):
    info = read_control(background_root() / 'current.json')
    if not info:
        output = dict(state='not_started')
    else:
        output = dict(info)
        if output.get('state') == 'running' and not is_active(output):
            output['state'] = 'interrupted'
    if as_json:
        print(json.dumps(output, indent=2, sort_keys=True))
    else:
        print('Background monitor:', output['state'])
        if output.get('directory'):
            print('Log directory:', output['directory'])
    return 0


def background_stop():
    control = background_root() / 'current.json'
    info = read_control(control)
    if not is_active(info):
        print('No active background monitor.')
        return 0
    pid = info['pid']
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        print('Monitor already exited.')
        return 0
    deadline = time.monotonic() + 7
    while time.monotonic() < deadline:
        updated = read_control(control)
        if updated and updated.get('pid') == pid and updated.get('state') != 'running':
            print('Background recording stopped; logs:', info['directory'])
            return 0
        if process_identity(pid) != info['start_ticks']:
            print('Monitor exited; logs:', info['directory'])
            return 0
        time.sleep(0.1)
    print('Stop signal sent; final report is still being written:', info['directory'])
    return 0

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) == 4 and argv[0] == '_background_worker':
        return background_worker(float(argv[1]), argv[2], argv[3])
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
    back = subs.add_parser('start', help='start silent continuous background logging')
    back.add_argument('--interval', type=float, default=10, help='sample interval in seconds (>= 1)')
    status = subs.add_parser('status', help='show background monitoring state')
    status.add_argument('--json', action='store_true', help='machine-readable status')
    subs.add_parser('stop', help='stop background monitor and finalize logs')
    summ = subs.add_parser('summary', help='summarize a saved CSV')
    summ.add_argument('file')
    args = parser.parse_args(argv)
    try:
        if args.action == 'env':
            env(args.json)
        elif args.action == 'start':
            if not 1 <= args.interval <= 86400:
                parser.error('interval must be between 1 and 86400 seconds')
            return background_start(args.interval)
        elif args.action == 'status':
            return background_status(args.json)
        elif args.action == 'stop':
            return background_stop()
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
