# Qwen3-MoE-Tiny inference speed study

A measurement-driven study of inference speed for [PrimeIntellect/qwen3-moe-tiny](https://huggingface.co/PrimeIntellect/qwen3-moe-tiny), kept on the dedicated branch and directory:

- Branch: <code>experiment/qwen3-moe-tiny</code>
- Directory: <code>experiments/qwen3-moe-tiny/</code>

The model card describes a roughly 670M-parameter test model with 24 layers, 16 experts, and 4 active experts per token. It lists Transformers, vLLM, and SGLang paths. This is a runtime benchmark target, not a model-quality benchmark.

A speedup is valid only when hardware, model revision, workload, runtime versions, and measurement method are comparable. Never infer a speedup from a different machine or an unrecorded configuration.

## Current status

The follow-up capability audit is in [results/2026-11-13/session-capability-followup.md](results/2026-11-13/session-capability-followup.md), with a machine-readable snapshot in [results/2026-11-13/preflight.json](results/2026-11-13/preflight.json). The active task container exposes 9 CPU threads but no GPU device, PyTorch, Transformers, serving runtime, or model cache. No model throughput has been measured in this container. This is an environment result, not an inference-speed baseline.

## Environment probe

From the repository root, collect a safe, dependency-free snapshot:

~~~bash
python3 experiments/qwen3-moe-tiny/preflight.py \
  --output experiments/qwen3-moe-tiny/results/YYYY-MM-DD/preflight.json
~~~

It records CPU/memory/disk capacity, visible accelerators, selected runtime package versions, and cache presence. It does not dump environment variables or record hostnames, addresses, prompts, or credentials.

## Direct Transformers benchmark

Use a compute image with a working PyTorch build for its actual accelerator. Keep a preinstalled CUDA/ROCm build intact. The direct runner measures prefill, first-token, cached decode, and end-to-end speed with fixed-length greedy decoding:

~~~bash
python3 experiments/qwen3-moe-tiny/benchmark_hf.py \
  --model PrimeIntellect/qwen3-moe-tiny \
  --device auto \
  --dtype auto \
  --attention sdpa \
  --threads 1 \
  --prompt-tokens 128 \
  --new-tokens 128 \
  --batch-size 1 \
  --warmup 2 \
  --repeats 5 \
  --output experiments/qwen3-moe-tiny/results/YYYY-MM-DD/baseline-sdpa.json
~~~

Keep startup/compile costs separate from steady-state timings. The runner records the resolved model revision when available, output token hashes, and memory use. It does not use EOS as a stopping condition, so each pass emits the configured number of tokens.

## Serving-backend benchmark

[benchmark_api.py](benchmark_api.py) uses only the standard library to measure streaming TTFT, latency, decode rate, and aggregate throughput from an OpenAI-compatible endpoint. It works with vLLM/SGLang servers that expose Chat Completions. vLLM documents Chat Completions and streaming usage reporting in its [server guide](https://docs.vllm.ai/en/latest/serving/openai_compatible_server/) and [per-request metrics guide](https://docs.vllm.ai/en/latest/features/per_request_metrics/).

~~~bash
python3 experiments/qwen3-moe-tiny/benchmark_api.py \
  --base-url http://127.0.0.1:8000/v1 \
  --model PrimeIntellect/qwen3-moe-tiny \
  --engine vllm \
  --requests 20 \
  --concurrency 1 \
  --warmup 2 \
  --max-tokens 128 \
  --output experiments/qwen3-moe-tiny/results/YYYY-MM-DD/vllm-c1.json
~~~

Repeat with concurrency 2, 4, and 8 while keeping server settings and workload fixed. Use <code>--no-include-usage</code> only when the server rejects streaming usage; a matching tokenizer can re-tokenize visible output as a fallback, but server-reported token usage is the preferred count. The report stores hashes and timing/count metadata, never the prompt or generated text. <code>--unique-prompts</code> changes the prompt suffix and is intended for prefix-cache experiments, not the default baseline.

## Optimization directions

Work through controlled comparisons in this order, then revisit winning settings on each new machine:

1. Establish a stable same-machine baseline and estimate run-to-run spread.
2. CPU threads and affinity; on accelerators, device placement and utilization.
3. Dtype and attention implementation, one primary variable at a time.
4. Compile and kernel paths, reporting compile cost and break-even separately.
5. Serving backends and MoE dispatch paths supported by the target image.
6. Prompt/output lengths, batch size, request concurrency, and sustained load.
7. Quantization or router/kernel changes, with correctness checks separate from speed.

Track single-request latency and aggregate throughput together. Keep unsuccessful outcomes and explain them. Do not store model weights, caches, API keys, prompts, generated text, or template <code>runs/</code> data in Git.