# Agent instructions: Cloud Compute Starter

When working in this repository on a Linux machine, use the standard-library
Python CLI in `cloudstat.py`. No package installation or GitHub Actions required.

**Default for any new computation:**

```bash
python3 cloudstat.py run -- python3 your_task.py
```

Replace `python3 your_task.py` with the **actual** user-approved command. Do not
run example commands as real workloads. The tool writes one new `runs/<UTC>-<PID>/`
with `environment.json`, `resources.csv`, `summary.txt`, and `result.json`.
It prints the path and forwards the command's exit code. To sample more frequently:

```bash
python3 cloudstat.py run --interval 5 -- python3 your_task.py
```

Use `python3 cloudstat.py env --json` for environment-only inspection.
Use `python3 cloudstat.py record --interval 10` to sample an already-running
session in the foreground (Ctrl+C stops it). To verify locally, run:

```bash
python3 -m unittest discover -s tests -v
```

Always read the produced `result.json` and `summary.txt`; never report a
successful computation from monitor output alone. `runs/` is gitignored and
should not be committed automatically. Save important results outside ephemeral
cloud storage as the user directs. The tool does not provide job checkpointing,
per-process accounting, billable network accounting or GPU utilization.

For an **existing project**, clone this public repository to a separate path,
then invoke its `cloudstat.py` by absolute/relative path from the **project's**
working directory; output stays under the project's `runs/`. There is no
cross-repository auto-installation or automatic startup across sessions.
