# Experiment plan

## Objective

Find repeatable inference-speed improvements for PrimeIntellect/qwen3-moe-tiny in available cloud compute environments. The current priority is CPU inference on the available Xeon node. Preserve a controlled baseline for each machine/runtime; keep reports and safe raw results in this directory.

## Model under test

- Repository: PrimeIntellect/qwen3-moe-tiny
- Intended use: small MoE runtime/testing workload, not a capability benchmark
- Model-card dimensions: about 670M parameters, 24 layers, 16 experts, top-4 active experts per token
- Context maximum in the current config: 32,768 tokens
- Resolve and save the model commit hash on every run

## CPU-specific priority

1. Build llama.cpp for this node with GGML_NATIVE=ON and Release settings.
2. Convert the HF checkpoint to an F16 GGUF reference and quantize it to Q8_0, Q5_K_M, Q4_K_M, and Q4_0.
3. On each format, measure prompt processing and token generation with thread counts 1 through 9, five repeats per setting.
4. Re-run the best thread/quant combination at multiple prompt and output lengths. Then test mmap/mlock and KV-cache formats as separate factors.
5. Compare against Transformers CPU FP32 and BF16, thread counts 1 through 9, then test torch.compile and supported oneDNN/Intel CPU paths.
6. Compare speed and output quality separately; preserve output hashes and record quantized-output differences.

The CPU path is described in cpu/README.md. The llama.cpp converter registers Qwen3MoeForCausalLM and its CPU feature matrix lists MoE and K-quants support. This makes llama.cpp the top candidate, not a pre-claimed winner.

## Workstreams

| Workstream | Controlled comparison | Main measures |
| --- | --- | --- |
| Environment | Standard-library preflight per fresh machine | CPU, memory, disk, accelerator visibility, runtime versions |
| CPU runtime | llama.cpp native GGUF vs Transformers CPU | Decode tok/s, prefill tok/s, first-token latency |
| CPU thread scaling | 1–9 visible cores, then affinity subsets | Median tok/s, variation, CPU use |
| Weight format | F16, Q8_0, Q5_K_M, Q4_K_M, Q4_0 | Speed, RAM, output agreement |
| CPU numeric path | Transformers FP32 vs BF16 | Speed, memory, output agreement |
| Compile/kernel | Eager vs torch.compile; supported Intel/oneDNN path | Warmup cost, steady-state, break-even |
| Serving backend | Transformers vs supported vLLM/SGLang; same host/model/request shape | Single-request latency and concurrent throughput |
| API workload | Streaming requests at concurrency 1/2/4/8, fixed prompt and output | Client-observed TTFT, latency, decode rate, request/token throughput |
| Request shape | Prompt 32/128/512/2048, output 32/128/512 | Prefill scaling, decode speed, memory |
| MoE/runtime kernels | Expert dispatch and fused-kernel options exposed by the installed runtime | Decode speed, utilization, correctness |

Direct measurements use benchmark_hf.py. Serving measurements use benchmark_api.py; its client-observed timings include network/client overhead and must not be compared directly to local inference TTFT.

## Rules for each iteration

1. Read AGENTS.md, this plan, and prior reports before selecting work.
2. State a hypothesis and change one primary factor.
3. Run a same-machine control before or after a candidate when practical.
4. Use at least two warmups and five timed repeats for direct comparisons.
5. Record model revision, hardware/software/build, exact command, raw measurements, and output-hash agreement.
6. If outputs differ, report that and run a short correctness check before accepting a faster setting.
7. Keep weights, caches, credentials, environment-variable values, prompts, and ephemeral monitor data out of Git.
8. If a dependency, accelerator, model download, or endpoint is unavailable, record the blocker once and pursue a different actionable setup/tool path. Never fabricate results or repeat an unchanged blocker.
9. After a useful measured run or concrete experiment improvement, add a dated report and safe raw data, update this plan, and commit/push to this experiment branch. Keep main unchanged.

## Current status — 2026-11-13

- The branch, initial benchmark harness, streaming API runner, and dependency-free preflight are established.
- CPU audit identifies Intel Xeon Platinum 8573C, 9 visible cores, AVX-512 VNNI and BF16/FP16; AMX flags are absent.
- Current task container has no CMake, llama.cpp binaries, PyTorch, Transformers, vLLM/SGLang, or local model cache.
- No valid tokens-per-second result exists yet. Environment preflight is not a performance baseline.
- Next CPU run: native llama.cpp build, model conversion/quantization, then sweep quant format and thread count. Follow with Transformers FP32/BF16 and thread sweep.
