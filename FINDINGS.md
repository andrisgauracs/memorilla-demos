# Findings

What worked, what didn't, and what surprised us, per demo. Every number comes from a results file in `results/`.
**Final recorded runs** (the ones filmed) are in `results/` (2026-11-13, a second H100 PCIe); the earlier **test runs** on a
different H100 PCIe are in `results/test-runs/`. The final runs reproduced the test runs exactly: every answer was word
for word identical (Buried Clue 16/16, PersonaMem-v2 40/40, Hound 32/32), and speed-race timings agree within ~1%.
The per-demo sections below quote the test runs; the scores are the same in the final runs. Sample sizes are small
(4-10 questions per demo): read them as behaviour, not statistics.

## Final recorded runs

| Demo | Results file | No memory | RAG | Full context | Memorilla |
| --- | --- | --- | --- | --- | --- |
| Sanity (README example) | `sanity-20261113-221035.json` | ✗ generic list | - | - | ✓ README answer, 1.73 s |
| The Buried Clue | `clue-20261113-221214.json` | 0/4 | 1/4 (control) | 0/4 | 0/4 |
| PersonaMem-v2 sample | `pmv2-20261113-221513.json` | 1/10 | 1/10 | 1/10 | 2/10 |
| The Hound of the Baskervilles | `book-20261113-222648.json` | 2/8 | 4/8 | 5/8 | 1/8 |

Speed race (`speed-20261113-222105.{json,csv,png}`): full context 0.37 s -> 2.63 s (128 docs) -> **19.33 s** (512 docs);
Memorilla end to end 0.19 s -> 0.90 s -> **3.25 s**, of which answering 0.14 -> 0.12 -> **0.12 s** and embedding
0.04 -> 0.78 -> 3.13 s. Peak VRAM 70.7 GiB. All GPU checks passed, including the memory-health check
(`gpu_check-2026-11-13-215740.json`).

### A faulty cloud GPU along the way

A first replacement H100 (Scaleway via Shadeform) passed the kernel checks but crashed while loading the decoder with
"CUDA error: Invalid access of peer GPU memory over nvlink or a hardware error". The kernel log showed Xid 94 and
`nvidia-smi` reported 2,529 lifetime uncorrectable DRAM errors with "Remapping Failure Occurred: Yes". The record is in
`results/failed-hardware/`; `make check-gpu` now reads the GPU's ECC and row-remapping state and fails on such a card.

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

### H100 PCIe (Shadeform / Massed Compute: 1x H100 PCIe 80 GB, driver 580, 20 vCPU, 125 GB RAM)

The paper used an H100-80GB (variant not stated); this is the PCIe card, which has less memory bandwidth and power
than SXM. H100 SXM / NVL / H200 were unobtainable on RunPod for ~1.5 h (every request: `SUPPLY_CONSTRAINT`).
All six GPU checks pass (`results/gpu_check-2026-11-13-204031.json`). Load time: ~22 s encoder + ~59 s decoder.

| Run | Peak VRAM (device) |
| --- | --- |
| sanity | 59.8 GiB / 79.6 |
| Demo 1, Demo 1b | 60.2 / 61.1 GiB |
| Demo 2 (YaRN 131K engine at 85%) | 70.7 GiB |
| Demo 3 | 59.8 GiB |

## Step 0: sanity check - reproduces

`results/sanity-20261113-204213.json`. PersonaMem-v2 test row 73, `pmv2` checkpoint:
- Memorilla: "Since you've got a family history of type 2 diabetes, you might enjoy breakfasts that balance protein,
  healthy fats, and fiber ... Greek yogurt with chia seeds and fresh berries ...". Matches the README word for word
  except "and **fresh** berries".
- Plain Qwen3-8B: a generic 256-token list of breakfasts (no mention of diabetes).
- Memorilla took 2.06 s: 1.17 s embedding the 107 chats (32,143 tokens) + 0.89 s answering. Greedy output was
  identical across runs, and the timed mirror of `MemoryVLLM.answer` returned exactly what `model.answer()` returns.

## Demo 1: The Buried Clue - Memorilla 0/4

`results/test-runs/clue-20261113-204436.json` (rescored with the corrected gym rubric, see below).

| | No memory | RAG top-5 | Full context | Memorilla (pmv2) |
| --- | --- | --- | --- | --- |
| Weekend getaway (dog gets carsick, chat 23) | ✗ | ✗ | ✗ | ✗ |
| Cook for parents (vegan, chat 88) | ✗ | ✗ | ✗ | ✗ |
| Gym time (3 a.m. bakery shift, chat 131) | ✗ | ✗ | ✗ | ✗ |
| Control: sister's name and birthday (chat 57) | ✗ | **✓** | ✗ | ✗ |

- RAG retrieved the lookalike chats for all three buried clues and never the clue chat (as designed), and found the
  control chat first.
- Full context (27K tokens, all 150 chats, upstream's layout: chats, then the question, no instruction) ignored the
  history; on the control it answered "I don't have access to personal information about your family members".
- Memorilla invented PersonaMem-style facts that are nowhere in Sam's history: "Since you're allergic to shellfish",
  "Since you've had a mild ankle sprain", "Your sister's name is Emily, and her birthday is on April 12th".
- Diagnostic (`results/test-runs/diagnostic-clue-*.json`): given *only* the Priya chat, `pmv2` says "Priya, April
  12th" (name right, date wrong); with 5 chats still "Priya"; with all 150, "Emily". Given only the vegan chat it
  suggests a lentil stew but never says vegan. `stage3` behaves the same way. One vector per chat keeps the gist, not
  details, and the signal fades as the collection grows; on data unlike PersonaMem it falls back to persona clichés.
- Rubric fix: the first gym rubric accepted "after work", which matched a generic heading ("Midday (Lunchtime or After
  Work)") in the no-memory answer. It now requires a reference to the shift. Fixed before any final run. The test-run
  file is kept unchanged, so it (and a replay of it) still shows the original, wrong ✓ for no memory on the gym
  question; the table above is the rescore with the corrected rubric.

Dataset notes:
- Every PersonaMem-v2 document is a single exchange (`User: ...\nAssistant: ...`). The 13 key chats (clues,
  lookalikes, control) copy that exactly; the 137 filler chats have two exchanges, i.e. the same format continued
  once. Filler chats were written under a banned-word list so no clue topic leaks outside its one chat.


## Demo 1b: PersonaMem-v2 sample - everyone at or below chance

`results/test-runs/pmv2-20261113-210049.json`. Ten test rows fixed by seed in `config.yaml` before running
(139, 684, 1067, 2374, 2512, 2933, 3106, 3471, 4342, 4849), scored with upstream's nearest-option metric (4 options,
chance = 25%): no memory 1/10, RAG 1/10, full context 1/10, **Memorilla 2/10**. On most rows all four methods picked
the same wrong option, so at this sample size the metric mostly measures answer style. Too small to rank methods.

## Demo 2: the speed race - the latency claim holds (answering only)

`results/test-runs/speed-20261113-205406.{json,csv,png}`. Median of 3 after 1 warm-up, 32 new tokens max.

| Passages (x250 tokens) | Full context | Memorilla end to end | embedding | answering |
| --- | --- | --- | --- | --- |
| 1 | 0.37 s | 0.20 s | 0.05 s | 0.15 s |
| 32 | 0.70 s | 0.34 s | 0.22 s | 0.12 s |
| 128 | 2.66 s | 0.93 s | 0.80 s | 0.12 s |
| 256 (YaRN) | 6.67 s | 1.71 s | 1.59 s | 0.12 s |
| 512 (YaRN, 128,511 prompt tokens) | **19.43 s** | **3.31 s** | 3.19 s | **0.12 s** |

- Full context at 512 documents: 19.4 s, close to the paper's 19.9 s. It fit (no out-of-memory point on 80 GB).
- Memorilla's answering time is flat (0.12-0.15 s), faster than the paper's 0.74 s; but Memorilla's answers were only
  7-13 tokens while full context always generated the full 32, worth roughly 0.2 s of decoding. It does not change
  the picture at large sizes, where full context's time is reading 128K tokens.
- Including embedding, Memorilla grows linearly (embedding 512 x 250 tokens takes 3.2 s): ~6x faster than full context
  at 512, not 27x. The paper's 27x excludes embedding.

Protocol notes:
- The paper's latency table reports N = 1 and N = 512 only, timed with Hugging Face `generate` (no paged KV cache),
  max_new_tokens 32, median of 5 after 2 warm-ups. Its 0.74 s excludes document encoding (2.59 s at N = 512, reported
  as a separate write path). This demo uses vLLM, 1 warm-up + median of 3 (as briefed), and reports Memorilla both
  with and without embedding.
- The Hound is only ~72K tokens, so the 512 x 250-token corpus continues into two more Conan Doyle novels.

## Demo 3: The Hound of the Baskervilles - Memorilla 1/8

`results/test-runs/book-20261113-205137.json`. No memory 2/8, RAG 4/8, full context 5/8, Memorilla (`stage3`) 1/8.
- Full context saw only the last 56% of the book (chapters 7-15): it missed all three early-chapter questions and got
  all five later ones, including both multi-chapter questions.
- Memorilla gave short answers, mostly wrong or invented: "Sir Charles Barrymore is the escaped convict", "Lady Laura,
  persuaded by Dr. Roylott" (a character from another Holmes story). It got "Phosphorus". For the footprints it said
  only "Footprints", without the hound, so the rubric marks it wrong.
- No memory knows parts of the novel from pretraining (Selden, Beryl being Stapleton's wife).
- Rubric leniency to note: full context's ✓ on "Who is Stapleton really" calls him "the escaped convict Selden, who is
  the illegitimate son of Rodger Baskerville": half wrong, but the rubric checks only the second part. Left as is
  rather than tightened after seeing the answer.

Protocol notes:
- With Qwen3-8B's native 40,960-token window the full-context baseline sees ~55% of the 72K-token book. Upstream's
  own NarrativeQA full-context baseline uses YaRN x4 (131,072 tokens), with which this book would fit whole.
- Qwen3-8B may know this famous novel from pretraining; the no-memory panel shows how much.

## Claims from the README, checked on this hardware

- **Holds:** the getting-started example (diabetes breakfast) reproduces almost word for word.
- **Holds, with a caveat:** "0.74 s vs 19.9 s, 27x". Full context reached 19.4 s at 512 documents; Memorilla's
  answering stayed flat. Counting the time to embed the documents, Memorilla is ~6x faster, not 27x.
- **Did not hold on our data:** connecting a question to a clue buried in one unrelated past conversation. On
  hand-written chats, Memorilla scored 0/4 and invented facts; on a 10-row PersonaMem-v2 sample, every method was at or
  below chance. The README's example is a PersonaMem-v2 test row, i.e. the checkpoint's own data distribution.
- **Not tested:** the benchmark averages (FactKG 84.33% vs 80.43%, Mem0/MemoRAG comparisons) and the 4,000x NarrativeQA
  compression; on one long book Memorilla answered 1 of 8 questions.
- **Hardware:** "you need one 80 GB GPU" holds; the pinned stack does not run on an RTX 5090 at all (see above).
