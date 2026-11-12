#!/usr/bin/env python3
"""Run controlled CPU llama-bench sweeps over GGUF files and thread counts."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import platform
import subprocess
import sys


def read_cpu_identity() -> tuple[str | None, list[str]]:
    model = None
    flags: set[str] = set()
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            key, sep, value = line.partition(":")
            if not sep:
                continue
            if key.strip() in ("model name", "Hardware") and model is None:
                model = value.strip()
            elif key.strip() == "flags" and not flags:
                flags = set(value.split())
    except OSError:
        pass
    picked = sorted(flag for flag in flags if flag.startswith(("avx", "amx")))
    return model, picked


def visible_cpu_ids() -> list[int]:
    if hasattr(os, "sched_getaffinity"):
        try:
            return sorted(os.sched_getaffinity(0))
        except OSError:
            pass
    return list(range(os.cpu_count() or 1))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--models", required=True, nargs="+", type=Path)
    parser.add_argument("--threads", default="auto",
                        help="llama-bench thread list/range; default sweeps 1..visible CPUs")
    parser.add_argument("--prompt-tokens", type=int, default=128)
    parser.add_argument("--generation-tokens", type=int, default=128)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output", required=True, type=Path,
                        help="append-only JSONL path for resumable raw runs")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()

    if not args.binary.is_file():
        parser.error(f"llama-bench binary not found: {args.binary}")
    missing = [str(path) for path in args.models if not path.is_file()]
    if missing:
        parser.error("model file(s) not found: " + ", ".join(missing))
    if min(args.prompt_tokens, args.generation_tokens, args.repeats, args.timeout) < 1:
        parser.error("token counts, repeats, and timeout must be positive")

    cpu_ids = visible_cpu_ids()
    threads = args.threads
    if threads == "auto":
        threads = ",".join(str(n) for n in range(1, len(cpu_ids) + 1))

    cpu_model, cpu_flags = read_cpu_identity()
    host = {
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "cpu_model": cpu_model,
        "cpu_flags": cpu_flags,
        "visible_cpu_ids": cpu_ids,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    failed = False

    with args.output.open("a", encoding="utf-8") as out:
        for model in args.models:
            command = [
                str(args.binary),
                "--model", str(model),
                "--n-prompt", str(args.prompt_tokens),
                "--n-gen", str(args.generation_tokens),
                "--threads", threads,
                "--repetitions", str(args.repeats),
                "--output", "json",
                "--n-gpu-layers", "0",
            ]
            started = dt.datetime.now(dt.timezone.utc).isoformat()
            try:
                proc = subprocess.run(
                    command,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=args.timeout,
                    check=False,
                )
                try:
                    raw_results = json.loads(proc.stdout)
                    parse_error = None
                except json.JSONDecodeError as exc:
                    raw_results = None
                    parse_error = str(exc)
                record = {
                    "timestamp_utc": started,
                    "status": "completed" if proc.returncode == 0 and parse_error is None else "failed",
                    "model_path": str(model),
                    "command": command,
                    "host": host,
                    "return_code": proc.returncode,
                    "raw_results": raw_results,
                    "stdout": proc.stdout if raw_results is None else None,
                    "stderr": proc.stderr[-12000:],
                    "json_parse_error": parse_error,
                }
                failed |= record["status"] != "completed"
            except subprocess.TimeoutExpired as exc:
                record = {
                    "timestamp_utc": started,
                    "status": "timeout",
                    "model_path": str(model),
                    "command": command,
                    "host": host,
                    "timeout_s": args.timeout,
                    "stdout": exc.stdout,
                    "stderr": exc.stderr,
                }
                failed = True
            out.write(json.dumps(record, sort_keys=True) + "\n")
            out.flush()
            print(f"{record['status']}: {model}", file=sys.stderr)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
