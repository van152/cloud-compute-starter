# CPU capability audit — 2026-11-13

## Measured node

- CPU: Intel Xeon Platinum 8573C.
- Visible CPU IDs: 0 through 8.
- The visible CPUs map to nine distinct core IDs in one physical package;
  no SMT sibling CPUs were exposed in this container.
- Reported CPU frequency in /proc/cpuinfo: 2.30 GHz. CPU frequency-scaling
  sysfs files are unavailable, so this is not a measured sustained clock.
- Flags exposed by /proc/cpuinfo include AVX2, AVX-512F, AVX-512 VNNI,
  AVX-VNNI, AVX-512 BF16, AVX-512 FP16, and FMA.
- AMX tile/BF16/INT8 flags are absent from this container.
- Host-visible RAM: 9.7 GiB. CPU affinity covers all nine visible CPUs.
- Available utilities include g++, taskset, NumPy, and threadpoolctl.
- Missing: CMake, Ninja, llama.cpp binaries, PyTorch, Transformers, GGUF
  tooling, OpenVINO, Intel Extension for PyTorch, ONNX Runtime, and model
  weights/cache.

## Optimization inference

The strongest immediately testable CPU candidate is a native llama.cpp build
with GGUF Q4/Q5/Q8 variants. Its converter registers Qwen3MoeForCausalLM and
the upstream CPU feature matrix lists MoE and K-quants. Native compilation
should target the exposed AVX-512/VNNI CPU features; AMX-specific tuning is not
available on this container.

For the Transformers route, BF16 is a candidate because AVX-512 BF16 is exposed,
but actual performance depends on the installed PyTorch/oneDNN build. Thread
counts 1 through 9 should be swept: the visible set contains nine distinct
cores, so this range tests each available core count without assuming a
hyperthread topology.

These are informed hypotheses, not model-speed results.

## Run status

No Qwen3-MoE-Tiny inference ran in this audit. The model weights and all
candidate inference runtimes are absent. This report captures the node
capabilities and the next CPU experiments; it contains no tokens-per-second
claim.
