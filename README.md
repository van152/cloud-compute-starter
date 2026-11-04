# Cloud Compute Starter

A tiny, **dependency-free Linux template** for AI agents and scientific computing on temporary cloud machines. It records a machine snapshot, CPU, memory and visible network usage, plus task outcome. **Python 3 standard library only.** No GitHub Actions, cron, daemon, third-party cloud services or outgoing telemetry.

## One command for AI agents (recommended)

From this repository:

```bash
python3 cloudstat.py run -- python3 your_task.py
```

Replace `python3 your_task.py` with your real command. `run` starts it, samples resource use every 10 seconds by default (including a last sample on exit), prints a concise summary, **preserves the task's exit status**, and creates a new directory:

```text
runs/20261104T070126.927347Z-1095/
  environment.json   # Machine properties, quotas and disk free space
  resources.csv       # UTC time, CPU cores, cgroup memory, RX/TX bytes
  summary.txt         # Duration, average/sample-peak CPU, memory peak, traffic
  result.json         # Started/finished timestamps, duration, exit code and status
```

**Run a 3-second offline demonstration:**

```bash
python3 cloudstat.py run --interval 1 -- python3 examples/demo_workload.py
```

For a longer experiment or a stable output path:

```bash
python3 cloudstat.py run --interval 5 --output-dir runs/experiment-001 -- python3 experiment.py
```

The output directory must **not** exist already. Neither results nor machine reports are automatically committed or uploaded.

## Use in a different project / new cloud session

From the **project's working directory**, clone this repository once in that environment, then execute the tool by path:

```bash
git clone --depth 1 https://github.com/van152/cloud-compute-starter.git /tmp/cloud-compute-starter
python3 /tmp/cloud-compute-starter/cloudstat.py run -- python3 your_task.py
```

This leaves the monitoring outputs in the current project's `runs/` directory. Add `runs/` to the **project's** `.gitignore` to prevent accidentally committing machine information. An already-existing `/tmp/cloud-compute-starter` can be reused. If the VM is replaced, clone again. The template does **not** install itself in unrelated repositories or start automatically in later sessions.

For an AI coding agent, point it at this repository and ask it to follow [AGENTS.md](AGENTS.md). Agents working directly from this GitHub template can read those instructions automatically if their runtime supports `AGENTS.md`.

**中文快速使用：** 新云端机器上先克隆仓库，再用 `python3 /tmp/cloud-compute-starter/cloudstat.py run -- 实际运行命令` 包装计算任务。完成后查看 `runs/` 下的 `summary.txt` 和 `result.json`。不需要安装 Python 依赖，也不消耗 GitHub Actions 额度。

## Other commands

```bash
python3 cloudstat.py env                  # Environment report on screen
python3 cloudstat.py env --json           # Machine-readable report
python3 cloudstat.py record --interval 10 # Monitor visible environment until Ctrl+C
python3 cloudstat.py record -- python3 your_task.py  # CSV only, no report bundle
python3 cloudstat.py summary runs/YOUR_FILE.csv     # Summarize a CSV manually
python3 -m unittest discover -s tests -v   # Local tests, no Actions
```

## What the numbers mean

| Metric | Meaning | Limitation |
| --- | --- | --- |
| `cpu_cores` | Delta of cgroup v2 CPU time divided by sample interval; `1.0` ~ one fully used core | Includes other processes in the visible cgroup. Reported peak is the **highest sample average**, not an instantaneous peak |
| `cgroup_memory_bytes` | Current cgroup v2 memory use | Includes other processes and page cache; not task-private RSS |
| `received_bytes`, `sent_bytes` | Deltas from visible non-loopback network interface counters | Includes unrelated visible traffic, omits host/proxy traffic; **not billable cloud traffic** |
| `elapsed_s` | Monotonic time since recording started | Wall-clock timestamps are separate |
| `env` / `environment.json` | CPU model/affinity, cgroup quota, kernel, memory, disk free space, Python | CPU affinity and cgroup CPU quotas can disagree |

Missing or inaccessible cgroup v2 values are left blank, not invented. Network resets contribute zero rather than negative traffic; a new interface begins contributing on the next sample. GPU utilization, per-process network accounting, cloud billing, job checkpointing and artifact backups are **not** provided.

The recorder flushes its CSV after each sample, but a vanished cloud machine can still lose local files. For long-running research, save important checkpoints and results to durable storage independently.

`runs/` is ignored by this repository's `.gitignore`. Review environment reports before public sharing. The CLI intentionally does not record the task's command-line arguments or environment variables, as they may contain secrets.

## Reuse and license

This is a public GitHub **template repository**. Select **Use this template** to start a new repo, or copy `cloudstat.py` and its `AGENTS.md` guidance into an existing one. Existing projects do not receive template changes automatically.

If you need full dashboards, see [Glances](https://github.com/nicolargo/glances), [sysstat](https://github.com/sysstat/sysstat), or [btop](https://github.com/aristocratos/btop). This project deliberately keeps a smaller scope.

[MIT License](LICENSE).
