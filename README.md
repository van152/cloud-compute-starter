# Cloud Compute Starter

A tiny, **dependency-free Linux template** for AI agents and scientific computing on temporary cloud machines. It records a machine snapshot, CPU, memory and visible network usage, plus task outcome. **Python 3 standard library only.** No GitHub Actions, cron, third-party cloud services or outgoing telemetry. An optional local background process records for the lifetime of the current cloud machine.

## Silent background recording (recommended for cloud sessions)

Start **once** from your project directory, then use the terminal or AI agent normally:

```bash
python3 cloudstat.py start                  # Starts detached monitoring; samples every 10s
python3 cloudstat.py status --json          # Optional: check if it is still running
python3 cloudstat.py stop                   # Optional: finish and summarize the logs
```

This is **not** tied to a single task. It continues writing data while other shell commands and programs run. Repeating `start` in the same working directory does **not** start another worker. It writes nothing repeatedly to your terminal or chat. On each session startup, the background worker saves:

```text
runs/background/current.json                 # PID, latest status, active or last session path
runs/background/<timestamp>-<id>/
  environment.json                          # Environment snapshot at startup
  resources.csv                             # Continuous CPU, RAM and network samples
  events.jsonl                              # Background started/stopped/error events
  daemon.log                                # Local diagnostic messages (normally minimal)
  result.json                               # Final status after normal stop
  summary.txt                               # Final resource summary after normal stop
```

`start` normally prints just one confirmation. No interactive dashboard, no uploads and no periodic chat messages. `events.jsonl` records **monitor lifecycle messages**, not the full stdout/stderr from every unrelated task. `resources.csv` continuously samples visible environment counters rather than a per-process account; if the cloud runtime isolates jobs in separate cgroups, those may not appear in the monitor's cgroup. At the default 10-second interval, expect approximately 8,640 CSV rows per day; provide enough disk space for a long run.

The worker can survive the CLI and shell exiting on a normal Linux machine. It **cannot survive VM shutdown, deletion, or a cloud platform that kills all processes when a session ends**. Unflushed/ephemeral files can be lost. Each new machine needs a fresh `start`; no automatically installed service, reboot persistence, scheduling or GitHub Actions is used.

### Start from an existing project without copying files

From that project's working directory:

```bash
git clone --depth 1 https://github.com/van152/cloud-compute-starter.git /tmp/cloud-compute-starter
python3 /tmp/cloud-compute-starter/cloudstat.py start
# Then run your project tasks as usual; monitoring runs independently.
```

The output appears under your project's `runs/background/`. Add `runs/` to the project `.gitignore` to avoid exposing local metrics. Run `status` or `stop` from the **same working directory** as `start`.

## Per-task report mode (optional)

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

## AI agents

For an AI coding agent, point it at this repository and ask it to follow [AGENTS.md](AGENTS.md). Agents working directly from this GitHub template can read those instructions automatically if their runtime supports `AGENTS.md`. The template does **not** install itself in unrelated repositories or start automatically in later sessions; each fresh VM needs its own clone and `start`.

**中文快速使用：** 在云端项目目录中克隆仓库，然后运行 `python3 /tmp/cloud-compute-starter/cloudstat.py start`。后台每 10 秒静默保存 CPU、内存、网络采样（自动兼容 cgroup v2、cgroup v1，都没有时退回 `/proc` 整机数据）；AI 正常执行其他任务即可。`status` 查看监控是否还在，`stop` 停止并生成报告。也可以使用 `run -- 任务命令` 生成单个计算任务的报告。不需要安装 Python 依赖或消耗 GitHub Actions 额度。

## Other commands

```bash
python3 cloudstat.py start                # Silent continuous background monitor
python3 cloudstat.py status --json        # Read monitoring state
python3 cloudstat.py stop                 # Stop and write final report
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
| `cpu_cores` | Delta of CPU time divided by sample interval; `1.0` ~ one fully used core | Includes other processes in the visible cgroup (or the whole host for `host_proc`). Reported peak is the **highest sample average**, not an instantaneous peak |
| `cgroup_memory_bytes` | Current memory use of the visible cgroup (column name kept for compatibility) | Includes other processes and page cache; not task-private RSS. With `host_proc` it is host-wide `MemTotal - MemAvailable` |
| `received_bytes`, `sent_bytes` | Deltas from visible non-loopback network interface counters | Includes unrelated visible traffic, omits host/proxy traffic; **not billable cloud traffic** |
| `elapsed_s` | Monotonic time since recording started | Wall-clock timestamps are separate |
| `env` / `environment.json` | CPU model/affinity, cgroup quota, kernel, memory, disk free space, Python, and `cpu_metric_source` / `memory_metric_source` | CPU affinity and cgroup CPU quotas can disagree |

### Where CPU and memory come from

Each sample tries these sources in order and `environment.json` records which one was used:

1. `cgroup_v2` – `/sys/fs/cgroup/cpu.stat` and `memory.current` (modern distros, most containers)
2. `cgroup_v1` – `/sys/fs/cgroup/cpuacct/cpuacct.usage` (or `cpu,cpuacct`) and `memory/memory.usage_in_bytes` (older hosts and many sandboxed cloud VMs)
3. `host_proc` – `/proc/stat` busy time and `/proc/meminfo`; whole-machine numbers when no cgroup counters are readable

Quota/limit fields are normalized to the v2 style (`max 100000` = unlimited CPU, `max` = no memory limit).

Values that no source can provide are left blank, not invented. Network resets contribute zero rather than negative traffic; a new interface begins contributing on the next sample. GPU utilization, per-process network accounting, cloud billing, job checkpointing and artifact backups are **not** provided.

The recorder flushes its CSV after each sample, but a vanished cloud machine can still lose local files. For long-running research, save important checkpoints and results to durable storage independently.

`runs/` is ignored by this repository's `.gitignore`. Review environment reports before public sharing. The CLI intentionally does not record the task's command-line arguments or environment variables, as they may contain secrets.

## Reuse and license

This is a public GitHub **template repository**. Select **Use this template** to start a new repo, or copy `cloudstat.py` and its `AGENTS.md` guidance into an existing one. Existing projects do not receive template changes automatically.

If you need full dashboards, see [Glances](https://github.com/nicolargo/glances), [sysstat](https://github.com/sysstat/sysstat), or [btop](https://github.com/aristocratos/btop). This project deliberately keeps a smaller scope.

[MIT License](LICENSE).
