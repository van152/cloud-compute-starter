# Experiment plan

## Objective

Find repeatable inference-speed improvements for PrimeIntellect/qwen3-moe-tiny
in the available cloud runtime. Preserve a controlled baseline for each
machine and runtime. Keep every run and report in this folder so results remain
auditable.

## Model under test

- Repository: PrimeIntellect/qwen3-moe-tiny
- Intended use: small MoE runtime/testing workload, not a capability benchmark
- Model-card dimensions: about 670M parameters, 24 layers, 16 experts, top-4
  active experts per token
- Context maximum in the current config: 32,768 tokens
- Resolve and save the model commit hash on every run; do not assume the
  mutable main revision stayed constant

## Workstreams

| Workstream | First controlled sweep | Main measures |
| --- | --- | --- |
| Baseline | Same host, 128 input + 128 output tokens, batch 1, five measured repeats | First-token latency, prefill tok/s, decode tok/s, end-to-end tok/s |
| CPU scheduling | 1, 2, 4, then all visible CPU threads | Decode tok/s, CPU use, peak RSS |
| Numeric format | Supported FP32, BF16, FP16; test only valid device/kernel combinations | Speed, memory, output token agreement |
| Attention | Eager versus SDPA; add FlashAttention only if installed and supported | Prefill/decode speed, failures |
| Compilation | Eager versus compile modes; exclude compile time from steady-state and report break-even separately | Warmup/compile cost, steady-state speed |
| Serving runtime | Transformers versus supported vLLM/SGLang versions | Single-request latency and concurrent aggregate throughput |
| Request shape | Prompt lengths 32/128/512/2048, outputs 32/128/512, batches 1/2/4/8 when memory permits | Prefill scaling, decode speed, memory |
| MoE/runtime kernels | Measure expert dispatch and fused-kernel options available in the runtime | Decode speed, utilization, correctness |

The script currently measures the Transformers workstream. Add another runner
only when a candidate backend is available; keep its workload and output schema
aligned with this one.

## Rules for each iteration

1. Read the latest main branch and all prior reports before selecting a test.
2. Record a hypothesis and change one primary variable.
3. Run a same-machine control before or after the candidate when practical.
4. Warm up, run at least five timed repeats, and retain every result.
5. If outputs differ, say so and compare a short quality/correctness check before
   claiming the faster setting is acceptable.
6. Keep model downloads, caches, credentials, and ephemeral monitor output out
   of Git. Never record secrets or full environment-variable dumps.
7. If a dependency, accelerator, or model download is unavailable, record the
   observed blocker and choose another useful path; do not fabricate results or
   repeat a known blocker as if it were progress.
8. After a meaningful measured run, add a dated report and raw JSON, update this
   plan's status, then commit/push the new files to this repository.

## Status at initialization

- Experiment code and measurement protocol committed.
- Active Codex task container has no installed PyTorch/Transformers runtime or
  local model cache. No model throughput measurement was possible there.
- No GPU was confirmed in that container: nvidia-smi and rocm-smi were
  unavailable. This does not prove that another cloud machine lacks a GPU.
- Next useful step: establish the first real baseline on a runnable cloud
  image; then perform CPU thread and attention/dtype sweeps on that same image.
