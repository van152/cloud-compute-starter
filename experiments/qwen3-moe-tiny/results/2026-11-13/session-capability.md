# Session capability audit — 2026-11-13

This is an environment audit of the Codex task container available during
experiment setup. It is not a Qwen3-MoE-Tiny speed result and may not describe
a separately provisioned cloud VM.

## Observed

- OS: Linux, kernel 6.18.57, x86_64.
- Python: 3.12.14.
- CPU affinity: 9 visible CPUs. The container's lscpu could not read
  /sys/devices/system/cpu/possible, so the specific CPU model was unavailable.
- Host-visible memory reported by free: 9.7 GiB total.
- Workspace filesystem: 63 GiB total, about 60 GiB free at inspection.
- Python packages absent: torch, transformers, safetensors, huggingface_hub,
  vLLM, SGLang, and Triton.
- No Hugging Face model cache was present.
- nvidia-smi and rocm-smi were not installed. GPU availability therefore
  could not be confirmed from this container.

## Conclusion

No valid model inference measurement could be run in this session because the
model weights and inference runtime were unavailable. The next run must first
establish a working runtime and record its actual hardware and package versions.
Do not use this audit as a performance baseline or extrapolate a speed estimate
from it.

## Measurement provenance

Collected from the active execution container with Python package discovery,
CPU-affinity inspection, /proc and free memory reporting, filesystem
capacity reporting, and accelerator CLI availability checks. No dependencies
were installed and no model weights were downloaded.
