# Qwen3-MoE-Tiny inference speed study

A measurement-driven study of inference speed for [PrimeIntellect/qwen3-moe-tiny](https://huggingface.co/PrimeIntellect/qwen3-moe-tiny), kept on the dedicated branch and directory:

- Branch: experiment/qwen3-moe-tiny
- Directory: experiments/qwen3-moe-tiny/

The model card describes a roughly 670M-parameter test model with 24 layers, 16 experts, and 4 active experts per token. It lists Transformers, vLLM, and SGLang paths. This is a runtime benchmark target, not a model-quality benchmark.

A speedup is valid only when hardware, model revision, workload, runtime versions, and measurement method are comparable. Never infer a speedup from a different machine or an unrecorded configuration.

## CPU priority

The active task container exposes an Intel Xeon Platinum 8573C with 9 visible cores, AVX-512 VNNI, and AVX-512 BF16. The CPU-specific path is [cpu/README.md](cpu/README.md): native llama.cpp CPU build, GGUF quantization, 1–9 thread sweep, then Transformers FP32/BF16 comparison. The current container has no CMake, llama.cpp binary, PyTorch, Transformers, model weights, or cache, so no model throughput has been measured yet. Capability data is not a speed result.

## Environment probe

From the repository root, collect a safe, dependency-free snapshot:

~~~bash
python3 experiments/qwen3-moe-tiny/preflight.py \
  --output experiments/qwen3-moe-tiny/results/YYYY-MM-DD/preflight.json
~~~

It records CPU/memory/disk capacity, visible accelerators, selected runtime package versions, and cache presence. It does not dump environment variables or record hostnames, addresses, prompts, or credentials.

## CPU llama.cpp benchmark

After building llama.cpp and preparing the GGUF files as described in [cpu/README.md](cpu/README.md), sweep thread counts and model formats:

~~~bash
python3 experiments/qwen3-moe-tiny/cpu/run_llama_bench_matrix.py \
  --binary ~/src/llama.cpp/build/bin/llama-bench \
  --models ~/.cache/qwen3-moe-tiny-gguf/qwen3-moe-tiny-f16.gguf \
    ~/.cache/qwen3-moe-tiny-gguf/qwen3-moe-tiny-Q4_K_M.gguf \
    ~/.cache/qwen3-moe-tiny-gguf/qwen3-moe-tiny-Q4_0.gguf \
  --threads 1-9 \
  --prompt-tokens 128 \
  --generation-tokens 128 \
  --repeats 5 \
  --output experiments/qwen3-moe-tiny/results/YYYY-MM-DD/llama-cpu-matrix.jsonl
~~~

The runner saves each raw result incrementally. Compare prefill and generation separately; llama-bench excludes tokenization and sampling overhead. Include Q8_0 and Q5_K_M alongside Q4 candidates, then repeat the best configuration at different context lengths.

## Direct Transformers benchmark

The direct runner measures prefill, first-token, cached decode, and end-to-end speed with fixed-length greedy decoding:

~~~bash
python3 experiments/qwen3-moe-tiny/benchmark_hf.py \
  --model PrimeIntellect/qwen3-moe-tiny \
  --device cpu \
  --dtype float32 \
  --attention sdpa \
  --threads 1 \
  --prompt-tokens 128 \
  --new-tokens 128 \
  --batch-size 1 \
  --warmup 2 \
  --repeats 5 \
  --output experiments/qwen3-moe-tiny/results/YYYY-MM-DD/torch-cpu-fp32-t1.json
~~~

For CPU tuning, repeat with threads 1 through 9; compare BF16 as a separate setting on this AVX-512 BF16-capable CPU. Keep startup/compile costs separate from steady-state timings. The runner records the resolved model revision when available, output token hashes, and memory use. It emits a fixed token count rather than stopping at EOS.

## Serving-backend benchmark

[benchmark_api.py](benchmark_api.py) uses only the standard library to measure streaming TTFT, latency, decode rate, and aggregate throughput from an OpenAI-compatible endpoint. It works with vLLM/SGLang servers that expose Chat Completions. vLLM documents Chat Completions and streaming usage reporting in its [server guide](https://docs.vllm.ai/en/latest/serving/openai_compatible_server/) and [per-request metrics guide](https://docs.vllm.ai/en/latest/features/per_request_metrics/).

~~~bash
python3 experiments/qwen3-moe-tiny/benchmark_api.py \
  --base-url http://127.0.0.1:8000/v1 \
  --model PrimeIntellect/qwen3-moe-tiny \
  --model-revision RESOLVED_CHECKPOINT_REVISION \
  --engine vllm \
  --engine-version SERVER_VERSION \
  --requests 20 \
  --concurrency 1 \
  --warmup 2 \
  --max-tokens 128 \
  --output experiments/qwen3-moe-tiny/results/YYYY-MM-DD/vllm-c1.json
~~~

Repeat with concurrency 2, 4, and 8 while keeping server settings and workload fixed. Use --no-include-usage only when the server rejects streaming usage; server-reported token usage is the preferred count. The report stores hashes and timing/count metadata, never the prompt or generated text. --unique-prompts changes the prompt suffix and is intended for prefix-cache experiments, not the default baseline.

## Result discipline

Use at least two warmups and five measured repeats for direct comparisons. Include the model commit, CPU/runtime/build details, exact command, raw measurements, and output-hash agreement. Track single-request latency and aggregate throughput together. Keep unsuccessful outcomes and explain them. Do not store model weights, caches, API keys, prompts, generated text, or template runs/ data in Git.
