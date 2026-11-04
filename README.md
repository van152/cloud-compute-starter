# Cloud Compute Starter

A small Linux starter for computational work: inspect the environment and record resource usage. Python 3 only; no dependencies, cloud services, GitHub Actions, or telemetry.

## Quick start

```bash
python3 cloudstat.py env
python3 cloudstat.py record --interval 10 -- python3 your_task.py
python3 cloudstat.py summary runs/YOUR_FILE.csv
```

`record` runs your command and writes samples until it exits, returning its exit code. Each run gets its own CSV under `runs/`. Run `python3 cloudstat.py record` without a command to monitor in the foreground; stop with Ctrl+C. Open a new session and start it again when needed. No automatic startup or persistent daemon is installed.

To save an environment report locally:

```bash
mkdir -p runs
python3 cloudstat.py env > runs/environment.txt
```

## What is measured

- CPU: cgroup v2 CPU time divided by elapsed time; 1.0 means one CPU core. Peaks are sample averages.
- Memory: cgroup v2 current memory, which can include cache and other processes in the same cgroup.
- Network: changes in non-loopback interface counters visible to the process. Counters include other visible traffic, may miss proxy or host traffic, and are not billing figures. Newly appearing interfaces start counting on the following sample.
- Environment: CPU model and affinity, cgroup limits, host-visible memory, filesystem free space, Python and kernel.

Measurements describe the visible environment, not necessarily an individual task. CPU and memory fields are blank when cgroup v2 counters are unavailable. The reported CPU affinity may differ from the CPU quota. Network counters can reset. The monitor and task must remain alive; a cloud session may terminate either process. Save any results you need before the environment is removed.

`runs/` is ignored by Git to avoid accidentally publishing machine information or logs. Review all files before sharing. Running a task through this tool does not protect against data loss or provide checkpointing.

## Using this template

Choose **Use this template** on GitHub to create a new repository. For an existing project, copy `cloudstat.py` and add `runs/` to its `.gitignore`. Templates do not update existing repositories automatically.

## License

MIT. See [LICENSE](LICENSE).
