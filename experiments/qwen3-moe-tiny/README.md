# Qwen3-MoE-Tiny inference speed study

This is a long-running, measurement-driven study of inference speed for
PrimeIntellect's Qwen3-MoE-Tiny in the available cloud compute environment.
The exact target is [PrimeIntellect/qwen3-moe-tiny](https://huggingface.co/PrimeIntellect/qwen3-moe-tiny).
Its model card describes a roughly 670M-parameter test model with 24 layers,
16 experts, and 4 active experts per token. The card lists Transformers, vLLM,
and SGLang usage; this folder starts with a controlled Transformers benchmark
and will add backend comparisons as runnable environments permit.

The goal is inference speed, not model quality. A result is comparable only
when hardware, model revision, prompt/output lengths, batch size, software
versions, and measurement method are recorded. Never infer a speedup from a
different machine or an unrecorded package change.

## Current status

The first environment audit is in
[results/2026-11-13/session-capability.md](results/2026-11-13/session-capability.md).
It found no installed inference runtime or model cache in the active Codex
container, so there is not yet a valid tokens-per-second baseline. This is an
environment finding, not a model benchmark. Continue by getting a runnable
model/runtime combination and measuring a baseline before tuning.

## Start a measured run

Use a cloud image with a working PyTorch build for its accelerator. Do not
replace a preinstalled CUDA/ROCm PyTorch build with a generic wheel just to
match a package list.

From the repository root:

~~~bash
python3 cloudstat.py start
python3 -m pip install transformers safetensors accelerate
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

Install or select PyTorch separately to match the actual CPU/GPU image. The
first run needs network access to Hugging Face or an existing model cache. The
script records the resolved model revision when the runtime exposes it. It uses
deterministic greedy decoding and emits one JSON record per configuration.
Startup and warmup are excluded from steady-state throughput; the report keeps
prefill, first-token, decode, and end-to-end measurements separate.

Use the same prompt length, output length, batch size, and repeated trials when
comparing configurations. Do not run another workload on the same device during
a measurement. Keep result files under this experiment folder; do not commit
generated model weights, caches, or the template monitor data.

## Optimization directions

Work through controlled comparisons in this order, then revisit the best
settings on each newly provisioned machine:

1. Establish a stable baseline and repeat it to estimate run-to-run noise.
2. CPU: thread count and affinity; accelerator: device placement and utilization.
3. Dtype and attention implementation, one variable at a time.
4. Compile and kernel paths, including compile cost and steady-state break-even.
5. Serving backends such as vLLM and SGLang, only when their Qwen3-MoE-Tiny
   support is verified on the target image.
6. Batch size, concurrent requests, prompt length, and sustained-load behavior.
7. Quantization or MoE dispatch changes, with output-quality checks kept separate
   from raw speed measurements.

For each iteration, state a hypothesis, change one primary factor, repeat the
baseline as a control, and retain unsuccessful results. Track both single
request latency and aggregate throughput so batching does not hide a latency
regression.

## Result acceptance

A reported speedup must include the raw JSON measurements, environment and
software versions, model ID plus resolved revision, benchmark command,
warmup/repeat counts, and a direct comparison to a control from the same
machine. Report medians and spread, not a single best trial. Mark output-hash
differences or failed runs explicitly. Never fill missing measurements with
estimates.

Current experiment plan and hypotheses: [EXPERIMENT_PLAN.md](EXPERIMENT_PLAN.md).
