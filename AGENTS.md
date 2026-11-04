# Agent instructions: Cloud Compute Starter

When working in this repository on a Linux machine, use the standard-library
Python CLI in `cloudstat.py`. No package installation or GitHub Actions required.

**Default at the beginning of a Linux cloud computing session:**

```bash
python3 cloudstat.py start
```

This launches one **silent, detached monitor** per current working directory.
It continuously samples visible cgroup CPU/memory and network counters every
10 seconds, independently of your computation commands. Repeat `start` is safe.
Run your work normally without wrapping each command or polling the monitor.
It creates `runs/background/current.json` and a session directory with
`environment.json`, `resources.csv`, `events.jsonl`, `daemon.log`, then
`summary.txt` and `result.json` after a clean stop.

For debugging or end-of-session cleanup (not continual polling):

```bash
python3 cloudstat.py status --json
python3 cloudstat.py stop
```

**Optional per-task wrapper** when a task-specific exit code and bundle are useful:

```bash
python3 cloudstat.py run -- python3 your_task.py
```

Replace `python3 your_task.py` with the **actual** user-approved command. Do not
run example commands as real workloads. The wrapper writes its own `runs/<UTC>-<PID>/`
with `environment.json`, `resources.csv`, `summary.txt`, and `result.json`.
It prints the path and forwards the command's exit code. To sample more frequently:

```bash
python3 cloudstat.py run --interval 5 -- python3 your_task.py
```

Use `python3 cloudstat.py env --json` for environment-only inspection.
Use `python3 cloudstat.py record --interval 10` only when explicit foreground
sampling is needed (Ctrl+C stops it). To verify locally, run:

```bash
python3 -m unittest discover -s tests -v
```

For a per-task `run`, read `result.json` and `summary.txt`; never infer
that a computation succeeded merely because the background monitor is running.
For background mode, `result.json` is finalized on `stop`, so inspect
`status --json` (or `runs/background/current.json`) for the current state.
`runs/` is gitignored and should not be committed automatically. Save important results outside ephemeral
cloud storage as the user directs. The tool does not provide job checkpointing,
per-process accounting, billable network accounting or GPU utilization.

For an **existing project**, clone this public repository to a separate path,
then invoke its `cloudstat.py` by absolute/relative path from the **project's**
working directory; output stays under the project's `runs/`. There is no
cross-repository auto-installation or automatic startup across sessions.
The detached monitor survives the invoking CLI but not machine shutdown or
platform-enforced process cleanup; restart it on each fresh cloud machine.
It does not capture other tasks' stdout/stderr or private chat messages.
