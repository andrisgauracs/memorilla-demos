# Findings

What worked, what didn't, and what surprised us, per demo. Every number links to a results file in `results/`.
Sections marked *pending* are filled in from the runs on the H100.

## Hardware

### RTX 5090 (homelab: Ubuntu 24.04, driver 595.99, 32 GB, Core Ultra 9 285K, 64 GB RAM)

**The pinned stack does not run on the 5090.** Measured with `make check-gpu`
(`results/gpu_check-*.json` from the homelab run):

| Check | Result |
| --- | --- |
| torch 2.7.0+cu128 includes sm_120, bf16 matmul + SDPA | ✓ |
| Triton 3.3.0 JIT kernel | ✓ |
| vLLM 0.9.1 compiled ops (`_C`, e.g. `rms_norm`) | ✗ "no kernel image is available for execution on the device" |
| vLLM 0.9.1 FlashAttention-2 | ✗ same error |
| xFormers 0.0.30 attention | ✗ process abort in its bundled FlashAttention-3 (hopper) kernel |

Reading the fatbin headers of the installed binaries shows why: vLLM 0.9.1's `_C` is compiled for sm_52 through
sm_100 (with PTX only up to sm_90), its FlashAttention-2 for sm_80/90/100, and xFormers 0.0.30 up to sm_90.
torch 2.7.0+cu128 itself has sm_120 code. PyPI's default torch 2.7.0 is a CUDA 12.6 build without sm_120, which is why
setup installs the same version from the CUDA 12.8 index.

- Newer vLLM releases include sm_120 kernels, but Memorilla's inference path needs vLLM 0.9.1's V0 engine and
  `enable_prompt_embeds`, which later releases changed and removed. Upgrading would also change the pinned stack.
- Smallest change that could work (not tried): build vLLM 0.9.1 from source with `TORCH_CUDA_ARCH_LIST=12.0` and a
  CUDA 12.8 toolkit (the homelab has CUDA 12.0, which cannot target sm_120).
- Memory, had it run: upstream's defaults (encoder 25%, decoder 60% of the card) do not fit 32 GB as is. The encoder's
  25% (~8 GB) equals its weights, and its default 40,960-token window alone needs ~6 GB of KV cache. The `rtx5090`
  profile (encoder 30% with a 4K window, decoder 58% with an 8K window) is a budget, not a measured fit.

### H100 *(pending)*

## Step 0: sanity check *(pending)*

## Demo 1: The Buried Clue *(pending)*

Dataset notes:
- Every PersonaMem-v2 document is a single exchange (`User: ...\nAssistant: ...`). The 13 key chats (clues,
  lookalikes, control) copy that exactly; the 137 filler chats have two exchanges, i.e. the same format continued
  once. Filler chats were written under a banned-word list so no clue topic leaks outside its one chat.

## Demo 2: the speed race *(pending)*

Protocol notes:
- The paper's latency table reports N = 1 and N = 512 only, timed with Hugging Face `generate` (no paged KV cache),
  max_new_tokens 32, median of 5 after 2 warm-ups. Its 0.74 s excludes document encoding (2.59 s at N = 512, reported
  as a separate write path). This demo uses vLLM, 1 warm-up + median of 3 (as briefed), and reports Memorilla both
  with and without embedding.
- The Hound is only ~72K tokens, so the 512 x 250-token corpus continues into two more Conan Doyle novels.

## Demo 3: The Hound of the Baskervilles *(pending)*

Protocol notes:
- With Qwen3-8B's native 40,960-token window the full-context baseline sees ~55% of the 72K-token book. Upstream's
  own NarrativeQA full-context baseline uses YaRN x4 (131,072 tokens), with which this book would fit whole.
- Qwen3-8B may know this famous novel from pretraining; the no-memory panel shows how much.

## Claims from the README that did not hold up *(pending)*
