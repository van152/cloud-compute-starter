# Cloud Compute Starter

A tiny **dependency-free Linux template** for scientific and AI workloads on temporary cloud machines. Inspect the runtime, record its resource usage while a task runs, and summarize the CSV. **Python 3 only**; no cloud accounts, third-party services, GitHub Actions, daemon, or outbound telemetry.

> This is a **session logger**, not a full monitoring stack. CPU and memory describe the visible cgroup rather than necessarily one isolated task; network counters describe visible interfaces, not a cloud provider bill.

## Quick start

```bash
python3 cloudstat.py env
python3 cloudstat.py record --interval 10 -- python3 your_task.py
python3 cloudstat.py summary runs/YOUR_FILE.csv
```

Each invocation creates a fresh `runs/<UTC-time>-<PID>.csv`. `record` forwards the child task's exit status, samples a final time when the task exits, and forwards Ctrl+C/SIGTERM to the launched process group. Without a command, record in the foreground until interrupted.

For predictable filenames and machine-readable environment checks:

```bash
mkdir -p runs
python3 cloudstat.py env --json > runs/environment.json
python3 cloudstat.py record --interval 5 --output runs/experiment.csv -- python3 experiment.py
python3 cloudstat.py summary runs/experiment.csv
```

**Existing CSV files are never overwritten.** No daemon or automatic cross-session process is installed: restart the recorder in each new cloud environment.

## Metrics and interpretation

| Metric | What it means | Limitations |
|---|---|---|
| `cpu_cores` | Change in cgroup v2 CPU time / elapsed time; 1.0 ≈ one fully utilized core | Includes other work in visible cgroup; peak = highest *sample average* |
| `cgroup_memory_bytes` | Current cgroup v2 memory use | Includes caches and other processes in the cgroup |
| `received_bytes`, `sent_bytes` | Interval deltas of visible non-loopback interface counters | Includes unrelated visible traffic; not billable transfer |
| `elapsed_s` | Monotonic elapsed time | Separate from wall-clock `timestamp_utc` |
| `env` | CPU model and affinity, cgroup quota and memory limits, kernel, disk free space, Python | CPU quota and CPU affinity are not equivalent |

On systems without readable cgroup v2 counters, CPU and memory fields remain blank. Unavailable network counters are blank as well. Counter resets contribute zero instead of negative values; newly seen network interfaces enter the next interval. This is **not per-process**, per-GPU, cost, or cloud-provider accounting.

The program flushes CSV after each sample, but a terminated VM, lost ephemeral storage, or power failure can still erase data. It does **not** checkpoint computational jobs or back up artifacts.

## Reuse

This is a public GitHub **template repository**: use **Use this template** to start a fresh repo, or copy `cloudstat.py` into an existing repo and add `runs/` to its `.gitignore`. Templates never update existing repositories automatically.

Runtime logs are ignored by Git to avoid accidentally publishing machine details. Review saved reports before making them public.

## Local tests (no Actions)

```bash
python3 -m unittest discover -s tests -v
```

Tests use Python's standard library only. No GitHub Actions, scheduled monitoring, automatic upload, or telemetry.

## Related open source

More comprehensive monitoring: [Glances](https://github.com/nicolargo/glances), [sysstat](https://github.com/sysstat/sysstat), [btop](https://github.com/aristocratos/btop). This project deliberately remains lighter.

## License

[MIT](LICENSE).
