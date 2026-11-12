# Experiment plan

## Objective

Find repeatable inference-speed improvements for PrimeIntellect/qwen3-moe-tiny in available cloud compute environments. Preserve a controlled baseline for each machine and runtime. Keep tools and safe reports in this folder; do not commit model weights, caches, credentials, or ephemeral monitor data.

## Model under test

- Repository: PrimeIntellect/qwen3-moe-tiny
- Intended use: small MoE runtime/testing workload, not a capability benchmark
- Model-card dimensions: about 670M parameters, 24 layers, 16 experts, top-4 active experts per token
- Context maximum in the current config: 32,768 tokens
- Resolve and save the model commit hash on every run; do not assume the mutable main revision stayed constant

## Workstreams

| Workstream | Controlled comparison | Main measures |
| --- | --- | --- |
| Environment | Standard-library preflight per fresh machine | CPU, memory, disk, accelerator visibility, runtime versions |
| Direct baseline | Same host, 128 input + 128 output tokens, batch 1, two warmups, five measured repeats | TTFT, prefill, cached decode, end-to-end tok/s, memory |
| CPU scheduling | 1, 2, 4, then all visible CPU threads; affinity only after thread sweep | Decode tok/s, CPU use, peak RSS |
| Numeric format | Supported FP32, BF16, FP16; valid device/kernel combinations only | Speed, memory, output token agreement |
| Attention | Eager vs SDPA; FlashAttention only when installed and supported | Prefill/decode speed, failures |
| Compilation | Eager vs supported compile modes; exclude compile time from steady-state | Compile/warmup cost, break-even, steady-state speed |
| Serving backend | Transformers vs supported vLLM/SGLang; same host, model revision, request shape | Single-request latency and concurrent aggregate throughput |
| API workload | Streaming API requests at concurrency 1/2/4/8, fixed prompt and max output | Client-observed TTFT, latency, decode rate, request/token throughput |
| Request shape | Prompt lengths 32/128/512/2048, outputs 32/128/512, batches 1/2/4/8 when memory permits | Prefill scaling, decode speed, memory |
| MoE/runtime kernels | Expert dispatch and fused-kernel options exposed by the installed runtime | Decode speed, utilization, correctness |

Direct measurements use benchmark_hf.py. Serving measurements use benchmark_api.py, which retains timing and output hashes without saving prompt or response text. Keep the runner configurations aligned enough to compare valid dimensions; the API runner includes network/client overhead, so do not compare its absolute TTFT to local direct inference.

## Rules for each iteration

1. Read the repository instructions, this plan, and prior reports before selecting work.
2. State a hypothesis and change one primary factor.
3. Run a same-machine control before or after a candidate when practical.
4. Use at least two warmups and five measured repeats for direct comparisons.
5. Record model revision, hardware/software, exact command, raw measurements, and output-hash agreement.
6. If outputs differ, report that and perform a short correctness check before accepting a faster setting.
7. Keep model downloads, caches, credentials, environment variables, prompts, and ephemeral monitor output out of Git.
8. If a dependency, accelerator, model download, or server endpoint is missing, record the observed blocker once and pursue another useful setup/tooling path. Never fabricate results or repeat an unchanged blocker as progress.
9. After a useful measured run or concrete experiment improvement, add a dated report and safe raw data, update this plan, and commit/push to the experiment branch. Keep main unchanged.

## Current status — 2026-11-13

- The branch and initial experiment folder are established.
- The first follow-up audit resolved the CPU model as Intel Xeon Platinum 8573C with 9 visible CPUs.
- This task container exposes no NVIDIA/AMD device paths and has no installed PyTorch, Transformers, vLLM, SGLang, Triton, or local model cache.
- No valid tokens-per-second result exists yet. The audit is not a performance baseline.
- Dependency-free environment and OpenAI-compatible streaming benchmark tools are now available for the next suitable runtime.
- Next useful step: run preflight on a model-capable cloud image; establish a same-machine baseline; then sweep one variable at a time.