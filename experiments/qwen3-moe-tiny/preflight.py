#!/usr/bin/env python3
"""Dependency-free, secret-safe capability snapshot for model benchmarks."""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.metadata
import json
import os
import platform
import shutil
import sys
from pathlib import Path


def read(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None


def cpu_model() -> str | None:
    for line in (read("/proc/cpuinfo") or "").splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() in ("model name", "Hardware"):
            return value.strip()
    return None


def cgroup_info() -> dict:
    cpu = read("/sys/fs/cgroup/cpu.max")
    memory = read("/sys/fs/cgroup/memory.max")
    if cpu is None:
        quota = read("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
        period = read("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
        if quota is not None and period is not None:
            cpu = f"{quota} {period}"
    if memory is None:
        memory = read("/sys/fs/cgroup/memory/memory.limit_in_bytes")
    return {"cpu_quota_raw": cpu, "memory_limit_bytes_raw": memory}


def installed_packages() -> dict:
    names = {
        "torch": "torch",
        "transformers": "transformers",
        "safetensors": "safetensors",
        "accelerate": "accelerate",
        "huggingface_hub": "huggingface-hub",
        "vllm": "vllm",
        "sglang": "sglang",
        "triton": "triton",
        "llama_cpp": "llama-cpp-python",
    }
    result = {}
    for import_name, distribution in names.items():
        try:
            result[import_name] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            result[import_name] = None
    return result


def collect() -> dict:
    try:
        visible_cpus = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        visible_cpus = os.cpu_count()
    try:
        disk = shutil.disk_usage(".")
        disk_info = {"total_bytes": disk.total, "free_bytes": disk.free}
    except OSError:
        disk_info = None
    memory_info = {}
    for line in (read("/proc/meminfo") or "").splitlines():
        key, sep, value = line.partition(":")
        if sep and key in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
            memory_info[key] = value.strip()
    return {
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "system": platform.system(),
        "kernel": platform.release(),
        "architecture": platform.machine(),
        "cpu_model": cpu_model(),
        "visible_cpus": visible_cpus,
        "memory": memory_info,
        "cgroup": cgroup_info(),
        "working_filesystem": disk_info,
        "python": sys.version.split()[0],
        "packages": installed_packages(),
        "accelerator_tools": {name: shutil.which(name) is not None
                              for name in ("nvidia-smi", "rocm-smi", "rocminfo")},
        "accelerator_device_paths": {path: Path(path).exists()
                                     for path in ("/dev/nvidia0", "/dev/kfd", "/dev/dri")},
        "model_cache_paths": {
            "huggingface_home": Path.home().joinpath(".cache", "huggingface").exists(),
            "torch_hub": Path.home().joinpath(".cache", "torch").exists(),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = collect()
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
