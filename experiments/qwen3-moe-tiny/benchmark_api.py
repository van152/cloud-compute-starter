#!/usr/bin/env python3
"""Measure streaming decode through an OpenAI-compatible chat endpoint.

Uses only the Python standard library. Prompt and response text are never
written to the report; only prompt length/hash and response timing/counts are.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import os
import platform
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


DEFAULT_PROMPT = "Explain in one paragraph why measuring inference speed needs a fixed workload."


def quantile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * p
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    fraction = position - low
    return ordered[low] * (1.0 - fraction) + ordered[high] * fraction


def summarize(records: list[dict], wall_s: float) -> dict:
    good = [row for row in records if row.get("status") == "ok"]
    ttft = [row["ttft_s"] for row in good if row.get("ttft_s") is not None]
    latency = [row["latency_s"] for row in good if row.get("latency_s") is not None]
    decode = [row["decode_tokens_per_s"] for row in good
              if row.get("decode_tokens_per_s") is not None]
    e2e = [row["end_to_end_tokens_per_s"] for row in good
           if row.get("end_to_end_tokens_per_s") is not None]
    known_tokens = [row["output_tokens"] for row in good
                    if row.get("output_tokens") is not None]
    token_total = sum(known_tokens)
    return {
        "requests": len(records),
        "successful_requests": len(good),
        "failed_requests": len(records) - len(good),
        "measured_wall_s": wall_s,
        "request_throughput_per_s": len(good) / wall_s if wall_s else None,
        "output_tokens_observed": token_total if len(known_tokens) == len(good) else None,
        "aggregate_output_tokens_per_s": token_total / wall_s if wall_s and len(known_tokens) == len(good) else None,
        "ttft_s_median": statistics.median(ttft) if ttft else None,
        "ttft_s_p95": quantile(ttft, 0.95),
        "latency_s_median": statistics.median(latency) if latency else None,
        "latency_s_p95": quantile(latency, 0.95),
        "decode_tokens_per_s_median": statistics.median(decode) if decode else None,
        "end_to_end_tokens_per_s_median": statistics.median(e2e) if e2e else None,
    }


def endpoint_info(base_url: str) -> str:
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("base URL must be an http:// or https:// URL with a hostname")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("do not put credentials or query parameters in the URL; use --api-key-env")
    # Deliberately omit URL credentials, query parameters, fragments, host, and path.
    return parsed.scheme


def request_once(args, prompt: str, tokenizer, request_index: int, warmup: bool = False) -> dict:
    started = time.perf_counter()
    url = args.base_url.rstrip("/") + "/chat/completions"
    messages = []
    if args.system_prompt:
        messages.append({"role": "system", "content": args.system_prompt})
    messages.append({"role": "user", "content": prompt})
    payload = {
        "model": args.model,
        "messages": messages,
        "max_tokens": args.max_tokens,
        "temperature": 0,
        "top_p": 1,
        "stream": True,
    }
    if args.include_usage:
        payload["stream_options"] = {"include_usage": True}
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if args.api_key_env:
        api_key = os.environ.get(args.api_key_env)
        if not api_key:
            return {
                "request_index": request_index,
                "status": "error",
                "error_type": "MissingApiKeyEnvironmentVariable",
                "latency_s": time.perf_counter() - started,
            }
        headers["Authorization"] = "Bearer " + api_key
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    chunks = []
    usage = None
    http_status = None
    finish_reason = None
    first_content_at = None
    last_content_at = None
    chunk_count = 0
    error_type = None
    error_body_sha256 = None
    try:
        with urllib.request.urlopen(req, timeout=args.timeout) as response:
            http_status = response.status
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":") or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    error_type = "InvalidServerSentEventJSON"
                    break
                if isinstance(event.get("usage"), dict):
                    usage = event["usage"]
                for choice in event.get("choices") or []:
                    if choice.get("finish_reason") is not None:
                        finish_reason = choice.get("finish_reason")
                    delta = choice.get("delta") or {}
                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        now = time.perf_counter()
                        if first_content_at is None:
                            first_content_at = now
                        last_content_at = now
                        chunks.append(content)
                        chunk_count += 1
    except urllib.error.HTTPError as exc:
        http_status = exc.code
        try:
            error_body_sha256 = hashlib.sha256(exc.read(1024 * 1024)).hexdigest()
        except OSError:
            pass
        error_type = "HTTPError"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        error_type = type(exc).__name__
    finished = time.perf_counter()
    latency_s = finished - started
    if error_type:
        return {
            "request_index": request_index,
            "status": "error",
            "http_status": http_status,
            "error_type": error_type,
            "error_body_sha256": error_body_sha256,
            "latency_s": latency_s,
            "warmup": warmup,
        }

    text = "".join(chunks)
    output_tokens = None
    token_source = None
    if isinstance(usage, dict) and isinstance(usage.get("completion_tokens"), int):
        output_tokens = usage["completion_tokens"]
        token_source = "server_usage"
    elif tokenizer is not None:
        output_tokens = len(tokenizer.encode(text, add_special_tokens=False))
        token_source = "local_tokenizer_reencoded_text"
    ttft_s = first_content_at - started if first_content_at is not None else None
    decode_s = last_content_at - first_content_at if first_content_at is not None and last_content_at is not None else None
    decode_tokens_per_s = None
    if output_tokens is not None and decode_s is not None and decode_s > 0:
        decode_tokens_per_s = max(0, output_tokens - 1) / decode_s
    end_to_end_tokens_per_s = output_tokens / latency_s if output_tokens is not None and latency_s > 0 else None
    prompt_tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
    return {
        "request_index": request_index,
        "status": "ok",
        "http_status": http_status,
        "warmup": warmup,
        "ttft_s": ttft_s,
        "latency_s": latency_s,
        "decode_interval_s": decode_s,
        "output_chars": len(text),
        "output_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "output_tokens": output_tokens,
        "token_count_source": token_source,
        "prompt_tokens": prompt_tokens,
        "sse_content_chunks": chunk_count,
        "finish_reason": finish_reason,
        "decode_tokens_per_s": decode_tokens_per_s,
        "end_to_end_tokens_per_s": end_to_end_tokens_per_s,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    parser.add_argument("--model", required=True, help="model name accepted by the server")
    parser.add_argument("--engine", default="unknown", help="label such as vLLM or SGLang")
    parser.add_argument("--engine-version", default=None)
    parser.add_argument("--api-key-env", default=None, help="environment variable holding an API key")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--system-prompt", default=None)
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument("--requests", type=int, default=10, help="number of measured requests")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--include-usage", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--tokenizer", default=None, help="optional matching Hugging Face tokenizer for token counts")
    parser.add_argument("--unique-prompts", action="store_true", help="append a stable request marker to bypass exact-prefix reuse")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if min(args.max_tokens, args.requests, args.concurrency) < 1 or args.warmup < 0 or args.timeout <= 0:
        parser.error("max-tokens, requests, and concurrency must be positive; warmup must be nonnegative; timeout must be positive")
    try:
        scheme = endpoint_info(args.base_url)
    except ValueError as exc:
        parser.error(str(exc))
    if not urllib.parse.urlsplit(args.base_url).path.rstrip("/"):
        parser.error("base URL must include the API prefix, for example /v1")

    tokenizer = None
    if args.tokenizer:
        try:
            from transformers import AutoTokenizer
        except ImportError:
            parser.error("--tokenizer requires transformers; omit it to use server usage counts")
        tokenizer = AutoTokenizer.from_pretrained(args.tokenizer)
    prompt = args.prompt_file.read_text(encoding="utf-8") if args.prompt_file else args.prompt
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    def request_prompt(index: int) -> str:
        return prompt + (f"\nRequest marker {index:06d}." if args.unique_prompts else "")

    warmup_records = [request_once(args, request_prompt(-i - 1), tokenizer, -i - 1, warmup=True)
                      for i in range(args.warmup)]
    if any(row.get("status") != "ok" for row in warmup_records):
        args.output.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "schema_version": 1,
            "status": "warmup_failed",
            "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "model": args.model,
            "engine": args.engine,
            "warmup_records": warmup_records,
        }
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"status": "warmup_failed", "output": str(args.output)}, indent=2))
        return 2

    measured_start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(request_once, args, request_prompt(i), tokenizer, i)
                   for i in range(args.requests)]
        records = [future.result() for future in concurrent.futures.as_completed(futures)]
    measured_wall_s = time.perf_counter() - measured_start
    records.sort(key=lambda row: row["request_index"])
    summary = summarize(records, measured_wall_s)
    successful_hashes = [row["output_sha256"] for row in records if row.get("status") == "ok"]
    summary["output_hashes_match"] = (
        len(set(successful_hashes)) <= 1 if successful_hashes and not args.unique_prompts else None
    )
    report = {
        "schema_version": 1,
        "status": "completed" if summary["failed_requests"] == 0 else "partial_failure",
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "model": args.model,
        "engine": args.engine,
        "engine_version": args.engine_version,
        "endpoint_scheme": scheme,
        "prompt_sha256": prompt_hash,
        "prompt_chars": len(prompt),
        "software": {"python": sys.version.split()[0], "platform": platform.platform()},
        "configuration": {
            "max_tokens": args.max_tokens,
            "requests": args.requests,
            "concurrency": args.concurrency,
            "warmup": args.warmup,
            "timeout_s": args.timeout,
            "include_usage": args.include_usage,
            "tokenizer": args.tokenizer,
            "unique_prompts": args.unique_prompts,
            "temperature": 0,
            "stream": True,
        },
        "warmup_latency_s": [row.get("latency_s") for row in warmup_records],
        "samples": records,
        "summary": summary,
        "measurement_notes": [
            "TTFT and latency are client-observed and include network/client overhead.",
            "Prefer server usage for token counts; tokenizer fallback re-encodes visible text and can differ from backend token IDs.",
            "Only text content deltas are counted; prompt and completion text are not stored.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "output": str(args.output), "summary": summary}, indent=2))
    return 0 if summary["failed_requests"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
