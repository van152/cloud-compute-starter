# CPU inference optimization

This folder targets the CPU-only inference path for Qwen3-MoE-Tiny. The active
container exposes an Intel Xeon Platinum 8573C with 9 physical cores in the
visible CPU set, AVX-512 VNNI, AVX-512 BF16/FP16, and AVX-VNNI. AMX is not
exposed. CPU topology/frequency scaling details are partially hidden by the
container.

## Highest-priority route: llama.cpp

The current llama.cpp converter registers Qwen3MoeForCausalLM, and the upstream
CPU feature matrix lists MoE and K-quants support. The hypothesis is that a
native llama.cpp build with a compact GGUF will outperform eager Transformers
CPU decode through specialized low-bit kernels and reduced active-expert
traffic. This is a hypothesis until measured on the target node.

Build llama.cpp on the same Xeon node so native kernel selection matches the
actual CPU:

~~~bash
git clone https://github.com/ggml-org/llama.cpp.git ~/src/llama.cpp
cmake -S ~/src/llama.cpp -B ~/src/llama.cpp/build \
  -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=ON -DGGML_OPENMP=ON
cmake --build ~/src/llama.cpp/build --config Release --parallel 8 \
  --target llama-cli llama-bench llama-quantize
~~~

Download the public checkpoint outside the Git repository, then convert it to
F16 and make quantized candidates:

~~~bash
python3 -m pip install -r ~/src/llama.cpp/requirements/requirements-convert_hf_to_gguf.txt
hf download PrimeIntellect/qwen3-moe-tiny --local-dir ~/.cache/qwen3-moe-tiny-hf
mkdir -p ~/.cache/qwen3-moe-tiny-gguf
python3 ~/src/llama.cpp/convert_hf_to_gguf.py \
  ~/.cache/qwen3-moe-tiny-hf \
  --outfile ~/.cache/qwen3-moe-tiny-gguf/qwen3-moe-tiny-f16.gguf \
  --outtype f16
for q in Q8_0 Q5_K_M Q4_K_M Q4_0; do
  ~/src/llama.cpp/build/bin/llama-quantize \
    ~/.cache/qwen3-moe-tiny-gguf/qwen3-moe-tiny-f16.gguf \
    ~/.cache/qwen3-moe-tiny-gguf/qwen3-moe-tiny-$q.gguf $q
done
~~~

Keep source, checkpoint, converted weights, and quantized weights outside the
experiment repository. Record the llama.cpp commit and model revision with every
result. If converter requirements or model layout change upstream, inspect the
current repository instructions and adapt the setup instead of treating a
failed command as the end of the experiment.

## Thread and quantization sweep

The matrix runner invokes the official llama-bench binary and saves each raw
JSON response incrementally. It tests prompt processing and generation at 128
tokens by default, with five repeats and visible thread counts 1 through 9:

~~~bash
python3 experiments/qwen3-moe-tiny/cpu/run_llama_bench_matrix.py \
  --binary ~/src/llama.cpp/build/bin/llama-bench \
  --models \
    ~/.cache/qwen3-moe-tiny-gguf/qwen3-moe-tiny-f16.gguf \
    ~/.cache/qwen3-moe-tiny-gguf/qwen3-moe-tiny-Q8_0.gguf \
    ~/.cache/qwen3-moe-tiny-gguf/qwen3-moe-tiny-Q5_K_M.gguf \
    ~/.cache/qwen3-moe-tiny-gguf/qwen3-moe-tiny-Q4_K_M.gguf \
    ~/.cache/qwen3-moe-tiny-gguf/qwen3-moe-tiny-Q4_0.gguf \
  --threads 1-9 \
  --prompt-tokens 128 \
  --generation-tokens 128 \
  --repeats 5 \
  --output experiments/qwen3-moe-tiny/results/2026-11-13/llama-cpu-matrix.jsonl
~~~

This isolates model prefill and generation speed. Later runs should test
prompt/output length, affinity, mmap/mlock, KV cache type, and sustained
requests separately. Do not combine these variables in one comparison.

## Second route: PyTorch CPU

Once the runtime and weights are available, use benchmark_hf.py with CPU thread
counts 1 through 9 and compare FP32 to BF16. This Xeon exposes AVX-512 BF16,
but the actual PyTorch build must include a suitable oneDNN CPU path. Test
torch.compile only after a stable eager baseline; record compile/warmup time
separately from steady-state throughput.

## References

- [llama.cpp Qwen3 MoE converter registration](https://github.com/ggml-org/llama.cpp/blob/master/conversion/qwen.py)
- [llama.cpp CPU feature matrix](https://github.com/ggml-org/llama.cpp/wiki/Feature-matrix)
- [llama-bench controls and output formats](https://github.com/ggml-org/llama.cpp/tree/master/tools/llama-bench)
- [llama.cpp native CPU build options](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md)
