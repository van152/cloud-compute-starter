#!/usr/bin/env python3
"""Small, dependency-free Linux environment and resource monitor."""
import argparse
import csv
import datetime as dt
import os
import pathlib
import platform
import shutil
import signal
import subprocess
import sys
import time

OUT = pathlib.Path('runs')

def read(path):
    try:
        return pathlib.Path(path).read_text().strip()
    except OSError:
        return None

def cpu_model():
    for line in (read('/proc/cpuinfo') or '').splitlines():
        if line.startswith('model name'):
            return line.split(':', 1)[1].strip()
    return 'unavailable'

def env():
    print('System:', platform.system(), platform.release(), platform.machine())
    print('CPU:', cpu_model())
    print('Visible CPUs:', len(os.sched_getaffinity(0)))
    quota = read('/sys/fs/cgroup/cpu.max')
    print('cgroup CPU quota:', quota or 'unavailable')
    info = dict(line.split(':', 1) for line in (read('/proc/meminfo') or '').splitlines() if ':' in line)
    print('Host-visible memory total:', info.get('MemTotal', 'unavailable').strip())
    print('cgroup memory limit (bytes):', read('/sys/fs/cgroup/memory.max') or 'unavailable')
    disk = shutil.disk_usage('.')
    print('Working filesystem free:', round(disk.free / 1024**3, 2), 'GiB')
    print('Python:', platform.python_version())

def snapshot():
    stat = read('/sys/fs/cgroup/cpu.stat') or ''
    usage = next((int(x.split()[1]) for x in stat.splitlines() if x.startswith('usage_usec ')), None)
    memory = read('/sys/fs/cgroup/memory.current')
    interfaces = {}
    for row in (read('/proc/net/dev') or '').splitlines()[2:]:
        name, data = row.split(':', 1)
        name = name.strip()
        if name == 'lo':
            continue
        values = list(map(int, data.split()))
        interfaces[name] = (values[0], values[8])
    return usage, int(memory) if memory and memory.isdigit() else None, interfaces

def delta_net(now, old, index):
    return sum(max(0, v[index] - old[name][index]) for name, v in now.items() if name in old)

def record(interval, command):
    OUT.mkdir(exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    path = OUT / (stamp + '.csv')
    print('Writing:', path, flush=True)
    proc = subprocess.Popen(command) if command else None
    previous = snapshot()
    last = start = time.monotonic()
    fields = ['timestamp_utc', 'elapsed_s', 'cpu_cores', 'cgroup_memory_bytes', 'received_bytes', 'sent_bytes']
    with path.open('x', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        f.flush()
        try:
            while True:
                if proc:
                    try:
                        proc.wait(timeout=interval)
                    except subprocess.TimeoutExpired:
                        pass
                else:
                    time.sleep(interval)
                now = time.monotonic()
                current = snapshot()
                cores = '' if current[0] is None or previous[0] is None else round(max(0, current[0] - previous[0]) / 1e6 / (now - last), 4)
                writer.writerow(dict(timestamp_utc=dt.datetime.now(dt.timezone.utc).isoformat(), elapsed_s=round(now-start, 2), cpu_cores=cores, cgroup_memory_bytes=current[1] if current[1] is not None else '', received_bytes=delta_net(current[2], previous[2], 0), sent_bytes=delta_net(current[2], previous[2], 1)))
                f.flush()
                previous, last = current, now
                if proc and proc.poll() is not None:
                    return proc.returncode
        except KeyboardInterrupt:
            if proc and proc.poll() is None:
                proc.send_signal(signal.SIGINT)
                return proc.wait()
            return 130

def summary(path):
    with pathlib.Path(path).open(newline='') as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print('No samples in', path)
        return
    cpus = [float(r['cpu_cores']) for r in rows if r['cpu_cores']]
    mem = [int(r['cgroup_memory_bytes']) for r in rows if r['cgroup_memory_bytes']]
    print('Samples:', len(rows), '| Duration:', rows[-1]['elapsed_s'], 's')
    if cpus:
        durations = [float(r['elapsed_s']) - (float(rows[i-1]['elapsed_s']) if i else 0) for i, r in enumerate(rows) if r['cpu_cores']]
        weighted = sum(c*d for c, d in zip(cpus, durations)) / sum(durations) if sum(durations) else 0
        print('CPU cores: average', round(weighted, 3), '| peak', max(cpus), '(sample average)')
    if mem:
        print('Peak cgroup memory:', round(max(mem)/1024**2, 1), 'MiB')
    print('Network received / sent:', round(sum(int(r['received_bytes']) for r in rows)/1024**2, 3), '/', round(sum(int(r['sent_bytes']) for r in rows)/1024**2, 3), 'MiB')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='action', required=True)
    subs.add_parser('env', help='print a simple environment report')
    mon = subs.add_parser('record', help='record until interrupted or the optional command exits')
    mon.add_argument('--interval', type=float, default=10)
    mon.add_argument('command', nargs=argparse.REMAINDER, help='optional command after --')
    summ = subs.add_parser('summary', help='summarize a CSV file')
    summ.add_argument('file')
    args = parser.parse_args()
    if args.action == 'env':
        env()
    elif args.action == 'record':
        if args.interval < 1:
            parser.error('interval must be at least 1 second')
        cmd = args.command[1:] if args.command and args.command[0] == '--' else args.command
        return record(args.interval, cmd)
    else:
        summary(args.file)
    return 0

if __name__ == '__main__':
    sys.exit(main())
