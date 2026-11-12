#!/usr/bin/env python3
"""Controlled single-host Transformers benchmark for Qwen3-MoE-Tiny.

Measures prefill, first-token, cached decode, and end-to-end throughput with
fixed-length greedy decoding. It is a benchmark harness, not a serving server.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import resource
import statistics
import sys
import time
from pathlib import Path


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * p
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    fraction = position - low
    return ordered[low] * (1.0 - fraction) + ordered[high] * fraction


def sync(torch, device) -> None:
    if device.type == "cuda":
        torch.cuda.synchronize(device)


def cpu_name() -> str | None:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            key, _, value = line.partition(":")
            if key.strip() in ("model name", "Hardware"):
                return value.strip()
    except OSError:
        pass
    return None


def environment(torch, device) -> dict:
    details = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "cpu_model": cpu_name(),
        "visible_cpus": len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count(),
        "torch": torch.__version__,
        "torch_cuda": getattr(torch.version, "cuda", None),
        "torch_hip": getattr(torch.version, "hip", None),
        "device": str(device),
    }
    if device.type == "cuda":
        details["device_name"] = torch.cuda.get_device_name(device)
        details["device_capability"] = list(torch.cuda.get_device_capability(device))
        details["cuda_device_count"] = torch.cuda.device_count()
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                details["host_visible_memory_kib"] = int(line.split()[1])
                break
    except (OSError, ValueError, IndexError):
        pass
    return details


def one_pass(model, torch, input_ids, base_mask, new_tokens: int, device) -> dict:
    sync(torch, device)
    start = time.perf_counter()
    with torch.inference_mode():
        output = model(input_ids=input_ids, attention_mask=base_mask, use_cache=True)
    sync(torch, device)
    prefill_s = time.perf_counter() - start
    past = output.past_key_values
    if past is None:
        raise RuntimeError("The model returned no KV cache for use_cache=True.")

    first_start = time.perf_counter()
    next_token = output.logits[:, -1, :].argmax(dim=-1, keepdim=True)
    sync(torch, device)
    first_token_extra_s = time.perf_counter() - first_start
    generated = [next_token]
    mask = base_mask
    decode_start = time.perf_counter()
    with torch.inference_mode():
        for _ in range(new_tokens - 1):
            mask = torch.cat(
                (mask, torch.ones((mask.shape[0], 1), dtype=mask.dtype, device=device)),
                dim=1,
            )
            output = model(
                input_ids=next_token,
                attention_mask=mask,
                past_key_values=past,
                use_cache=True,
            )
            past = output.past_key_values
            next_token = output.logits[:, -1, :].argmax(dim=-1, keepdim=True)
            generated.append(next_token)
    sync(torch, device)
    decode_s = time.perf_counter() - decode_start

    token_lists = torch.cat(generated, dim=1).detach().cpu().tolist()
    hashes = [
        hashlib.sha256(",".join(str(token) for token in row).encode("ascii")).hexdigest()
        for row in token_lists
    ]
    batch = input_ids.shape[0]
    total_s = prefill_s + first_token_extra_s + decode_s
    return {
        "prefill_s": prefill_s,
        "first_token_s": prefill_s + first_token_extra_s,
        "decode_s": decode_s,
        "end_to_end_s": total_s,
        "prefill_tokens_per_s": input_ids.shape[1] * batch / prefill_s,
        "decode_tokens_per_s": batch * (new_tokens - 1) / decode_s if decode_s else None,
        "end_to_end_tokens_per_s": batch * new_tokens / total_s,
        "output_hashes": hashes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="PrimeIntellect/qwen3-moe-tiny")
    parser.add_argument("--revision", default="main")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--dtype", choices=("auto", "float32", "bfloat16", "float16"), default="auto")
    parser.add_argument("--attention", choices=("auto", "eager", "sdpa", "flash_attention_2"), default="auto")
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--interop-threads", type=int, default=1)
    parser.add_argument("--prompt-tokens", type=int, default=128)
    parser.add_argument("--new-tokens", type=int, default=128)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--compile", action="store_true")
    parser.add_argument("--compile-mode", default="default")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    if min(args.threads, args.interop_threads, args.prompt_tokens, args.batch_size) < 1:
        parser.error("threads, interop threads, prompt tokens, and batch size must be positive")
    if args.new_tokens < 2 or args.warmup < 0 or args.repeats < 1:
        parser.error("new-tokens must be >= 2, warmup >= 0, and repeats >= 1")

    import torch
    import transformers
    from transformers import AutoModelForCausalLM, AutoTokenizer

    if args.device == "cuda" and not torch.cuda.is_available():
        parser.error("--device cuda requested but CUDA/ROCm is unavailable")
    device = torch.device(
        "cuda" if args.device == "cuda" or (args.device == "auto" and torch.cuda.is_available()) else "cpu"
    )
    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(args.interop_threads)

    if args.dtype == "auto":
        if device.type == "cuda" and torch.cuda.is_bf16_supported():
            dtype_name, dtype = "bfloat16", torch.bfloat16
        elif device.type == "cuda":
            dtype_name, dtype = "float16", torch.float16
        else:
            dtype_name, dtype = "float32", torch.float32
    else:
        dtype_name = args.dtype
        dtype = {"float32": torch.float32, "bfloat16": torch.bfloat16, "float16": torch.float16}[args.dtype]

    load_args = {"revision": args.revision, "torch_dtype": dtype}
    if args.attention != "auto":
        load_args["attn_implementation"] = args.attention

    load_start = time.perf_counter()
    tokenizer = AutoTokenizer.from_pretrained(args.model, revision=args.revision)
    base_model = AutoModelForCausalLM.from_pretrained(args.model, **load_args)
    base_model.to(device)
    base_model.eval()
    load_s = time.perf_counter() - load_start
    model = torch.compile(base_model, mode=args.compile_mode) if args.compile else base_model

    phrase = "Fixed prompt for controlled autoregressive inference speed measurement. "
    prompt_ids: list[int] = []
    while len(prompt_ids) < args.prompt_tokens:
        prompt_ids.extend(tokenizer(phrase, add_special_tokens=False).input_ids)
    prompt_ids = prompt_ids[: args.prompt_tokens]
    input_ids = torch.tensor(
        [prompt_ids for _ in range(args.batch_size)],
        dtype=torch.long,
        device=device,
    )
    mask = torch.ones_like(input_ids)

    max_context = getattr(base_model.config, "max_position_embeddings", None)
    if max_context and args.prompt_tokens + args.new_tokens > max_context:
        parser.error(f"prompt plus output exceeds model context limit {max_context}")

    warmup_results = [one_pass(model, torch, input_ids, mask, args.new_tokens, device)
                      for _ in range(args.warmup)]
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)

    samples = [one_pass(model, torch, input_ids, mask, args.new_tokens, device)
               for _ in range(args.repeats)]
    fields = ("first_token_s", "prefill_tokens_per_s", "decode_tokens_per_s",
              "end_to_end_tokens_per_s")
    summary = {
        field: {
            "median": statistics.median(sample[field] for sample in samples),
            "p90": percentile([sample[field] for sample in samples], 0.90),
        }
        for field in fields
    }
    output_hashes = [sample["output_hashes"] for sample in samples]
    report = {
        "schema_version": 1,
        "status": "completed",
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "model": args.model,
        "requested_revision": args.revision,
        "resolved_revision": getattr(base_model.config, "_commit_hash", None),
        "model_config": {
            "model_type": getattr(base_model.config, "model_type", None),
            "layers": getattr(base_model.config, "num_hidden_layers", None),
            "experts": getattr(base_model.config, "num_experts", None),
            "active_experts_per_token": getattr(base_model.config, "num_experts_per_tok", None),
            "max_position_embeddings": max_context,
        },
        "configuration": {
            "device": str(device),
            "dtype": dtype_name,
            "attention": args.attention,
            "threads": args.threads,
            "interop_threads": args.interop_threads,
            "compile": args.compile,
            "compile_mode": args.compile_mode if args.compile else None,
            "prompt_tokens": args.prompt_tokens,
            "new_tokens": args.new_tokens,
            "batch_size": args.batch_size,
            "warmup": args.warmup,
            "repeats": args.repeats,
        },
        "software": {
            "torch": torch.__version__,
            "transformers": transformers.__version__,
            "torch_cuda": getattr(torch.version, "cuda", None),
            "torch_hip": getattr(torch.version, "hip", None),
        },
        "environment": environment(torch, device),
        "load_s": load_s,
        "warmup_s": [result["end_to_end_s"] for result in warmup_results],
        "samples": samples,
        "summary": summary,
        "output_hashes_match": all(hashes == output_hashes[0] for hashes in output_hashes),
    }
    if device.type == "cuda":
        report["peak_gpu_memory_allocated_bytes"] = torch.cuda.max_memory_allocated(device)
        report["peak_gpu_memory_reserved_bytes"] = torch.cuda.max_memory_reserved(device)
    try:
        report["process_peak_rss_mb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except (AttributeError, OSError):
        pass

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "model": args.model,
        "resolved_revision": report["resolved_revision"],
        "device": str(device),
        "dtype": dtype_name,
        "decode_tokens_per_s_median": summary["decode_tokens_per_s"]["median"],
        "end_to_end_tokens_per_s_median": summary["end_to_end_tokens_per_s"]["median"],
        "output_hashes_match": report["output_hashes_match"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
