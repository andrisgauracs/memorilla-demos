# Memorilla demos

Three filmable, reproducible demos of [Memorilla](https://github.com/snap-stanford/memorilla)
([paper](https://arxiv.org/abs/2610.21375)): a memory module that compresses a whole document collection into
**16 memory tokens** for a frozen Qwen3-8B. Everything here uses upstream Memorilla's own API (`MemoryVLLM`,
`MemoryModule`, `embed_texts`, its chat formatting and prompts) at a pinned commit, and mirrors its baselines
(`evaluate_baselines.py`) where they are not importable. Each mirror says what it mirrors in its docstring.

| Command | What it shows |
| --- | --- |
| `make sanity` | The README's own example: PersonaMem-v2 test row 73, the breakfast question, `pmv2` checkpoint. |
| `make demo-clue` | **The Buried Clue.** 150 chats from a fictional user; can a model connect a question to a fact buried in one unrelated chat? No memory vs RAG top-5 vs full context vs Memorilla. |
| `make demo-speed` | **The speed race.** Answer latency as the collection grows from 1 to 512 passages of 250 tokens (128K tokens). |
| `make demo-book` | **A book too big for the context window.** Plot questions about *The Hound of the Baskervilles* (72K tokens). |

`results/` holds the JSON (and, for the speed race, CSV and PNG) of the full runs reported in
[FINDINGS.md](FINDINGS.md). Every run writes one; nothing is cherry-picked or retried.

## Setup

Linux, one NVIDIA GPU with 80 GB (H100 SXM recommended), Python is installed for you. Everything (uv, Python 3.11, the
virtualenv, model weights, caches) goes inside the repo directory, so deleting it removes everything.

```bash
git clone <this repo> memorilla-demos && cd memorilla-demos
make setup        # ~10 min: pinned stack + ~25 GB of pinned model and data downloads, then a GPU self-test
make sanity       # ~3 min: should print a breakfast answer that mentions the family diabetes history
```

`make setup` installs exactly what upstream pins (torch 2.7.0, vLLM 0.9.1, transformers 4.53.3, Memorilla at commit
`d8f1889`). The only deviation from a plain `pip install` is that torch/torchvision/torchaudio/xformers come from
PyTorch's CUDA 12.8 index (same versions, CUDA 12.8 build), because vLLM 0.9.1's PyPI wheel is itself a CUDA 12.8
build. The host driver must support CUDA 12.8 (driver 570 or newer). `make check-gpu` re-runs the GPU self-test and
writes `results/gpu_check-*.json`.

Models and data are pinned to exact Hub commits in [`config.yaml`](config.yaml): Qwen3-8B, Qwen3-Embedding-4B,
`memorilla/Memorilla-Qwen3-8B` (checkpoints `pmv2` and `stage3`) and `memorilla/Memorilla-Data`. Set `HF_TOKEN` in
your environment if you hit Hub rate limits; it is never printed or stored.

### H100 (80 GB)

Works as is with the `h100` profile (picked automatically from the GPU name, or `PROFILE=h100 make ...`). Encoder and
decoder share the card like upstream's `MemoryVLLM.from_pretrained` (25% / 60%); the speed race's full-context
phases start their own engine at 85% after the Memorilla engine has exited.

### RTX 5090 (32 GB, Blackwell sm_120): does not run with the pinned vLLM

`make check-gpu` on an RTX 5090 shows torch 2.7.0+cu128 and Triton working, but **vLLM 0.9.1's compiled kernels have no
sm_120 code**: its custom ops are built for sm_52-sm_100 and its FlashAttention-2 for sm_80/90/100 (read from the
binaries' fatbin headers), so every engine fails with "no kernel image is available for execution on the device".
xFormers 0.0.30 also aborts on sm_120. Newer vLLM releases ship sm_120 kernels but drop the V0 engine and the
prompt-embeddings API that Memorilla's inference path depends on, so upgrading is not a drop-in fix and would change
the pinned stack. The smallest change that could work is building vLLM 0.9.1 from source for sm_120 with a CUDA 12.8
toolkit; that is untested here. The `rtx5090` profile in `config.yaml` keeps the memory budget we would use (encoder
30% with a 4K window, decoder 58% with an 8K window), so the same code runs if such a build exists.

## Recording

Every demo takes the same flags:

```bash
make demo-clue                                     # live run; any key moves to the next question
make demo-clue ARGS="--auto 8"                     # live run; next question after 8 s
make demo-clue REPLAY=results/clue-<time>.json     # re-render a saved run: same screens, same answers
make demo-speed REPLAY=results/speed-<time>.json ARGS="--replay-speed 0.2"
make demo-speed REPLAY=results/speed-<time>.json ARGS="--png results/speed-chart.png"   # re-export PNG + CSV
```

- The layout is designed for a 16:9 terminal of about **120 x 35** characters at a large font.
- Model loading is one progress line; vLLM's own output goes to `logs/<demo>-<time>.log`.
- `--replay` loads no models and needs no GPU (only `rich`, `pyyaml`, and for the speed race `plotext` and
  `matplotlib`). It shows each answer after its *recorded* latency x `--replay-speed`. It re-renders a real, saved run;
  it does not compute anything, so say so if you show a replay.
- Paths on screen are relative to the repo; no tokens, usernames or hostnames are printed or stored.

## How the demos work

All methods use the same Qwen3-8B (bf16, vLLM) with **greedy decoding** and fixed seeds. The text-only baselines run
on the very engine `MemoryVLLM` started (no second copy of the decoder), except in the speed race (see below).
Every answer is marked ✓/✗ by an **LLM-free keyword rubric** (`demos/rubric.py`; the rubrics live next to the
questions), so the marks are reproducible. Each panel shows the tokens the LLM actually received and the latency.

### The Buried Clue (`data/sam/`)

- `chats.json`: 150 hand-written chats between a fictional user, Sam, and an assistant (~29K tokens), in
  PersonaMem-v2's exact document format (`User: ...\nAssistant: ...`), built deterministically by
  `data/sam/build_chats.py` from `data/sam/source/`.
- Three clues are buried in single chats that never mention their question's topic: Sam's dog Biscuit gets carsick on
  rides over ~20 minutes (chat 23), Sam has been vegan for years, said only while asking about leather-jacket
  alternatives (chat 88), and Sam's bakery shift starts at 3 a.m. (chat 131). Nine lookalike chats share wording with
  the questions without helping (a friend's road trip, a pasta recipe for a date, gym shoes, ...). A control question
  is answered plainly in one chat (chat 57), where RAG should win.
- `questions.json`: the four questions, where each answer lives, and the rubrics.
- Methods: no memory; RAG top-5 by cosine similarity over the same Qwen3-Embedding-4B vectors Memorilla receives
  (the panel lists the retrieved chat numbers); full context (all 150 chats); Memorilla with the `pmv2` checkpoint.
  All use `PERSONA_PROMPT`. RAG's latency includes embedding the question and searching; the one-off index build is
  recorded separately. Memorilla's latency includes embedding all 150 chats, as `MemoryVLLM.answer` does, and the
  panel splits it into embedding and answering.

### The speed race (`data/speed/`)

- `passages.json`: 512 consecutive windows of exactly 250 Qwen3-8B tokens (128,000 tokens). *The Hound of the
  Baskervilles* alone is ~72K tokens, so the corpus continues with *The Sign of the Four* and the opening of *A Study in
  Scarlet* (all public domain; built by `data/speed/prepare_corpus.py`).
- Sizes 1, 2, 4, ..., 512; the same question; batch size 1; greedy; at most 32 new tokens (the paper's setting); one
  warm-up, then the median of three timed runs.
- Full context runs on its own text-only engine (V0, like `MemoryVLLM`'s): native RoPE with a 32,768-token window
  while the prompt fits, then YaRN x4 with a 131,072-token window, the rule the paper describes. A size that does not
  fit or runs out of memory is marked "out of context/VRAM" and not hidden.
- Memorilla (`stage3`) is timed end to end **including embedding the passages**, and embedding and answering are
  reported separately. The paper's headline 0.74 s does not include document encoding (reported there as a separate
  "write path"), so the dashed "answering only" line is the one comparable to it.
- Each phase runs in its own process; the chart draws live. Outputs: `results/speed-<time>.{json,csv,png}`
  (1920x1080, dark).

### A book too big for the context window (`data/hound/`)

- `passages.json`: Gutenberg ebook #2852 with the Gutenberg header and footer stripped, packed into ~250-token passages
  along paragraph boundaries (364 passages, 72,130 tokens; `data/hound/prepare_book.py`). The raw ebook is committed
  in `data/hound/raw/` with its SHA-256 in `passages.json`.
- `questions.json`: 8 questions whose answers were checked against the text (passage ids in `evidence`); two need
  facts from separate chapters.
- Methods: no memory, RAG top-5, full context, Memorilla (`stage3`). Prompts follow upstream's NarrativeQA protocol
  (Memorilla with its benchmark prompt, baselines with `BRIEF_ANSWER_PROMPT`), 64 new tokens.
- Full context gets Qwen3-8B's native 40,960-token window, so it sees only part of the book: whole passages are dropped
  from the start (the direction upstream's baseline truncates), and the panel says which share and chapters it saw.
  Note that with YaRN x4 (upstream's NarrativeQA baseline setting) the whole book would fit.

## Configuration

[`config.yaml`](config.yaml) is the only config file: pinned revisions, paths, generation settings, display, one
profile per GPU (engine memory shares and context lengths), and one section per demo. Override anything for one run:

```bash
make demo-clue ARGS="--set clue.top_k=10 --set decoder.max_model_len=32768"
```

## Repository layout

```
config.yaml          all settings, one profile per GPU
Makefile             setup, check-gpu, sanity, demo-clue, demo-speed, demo-book
scripts/             setup.sh, env.sh (keeps every cache inside the repo)
demos/               the demos; models.py (loading, mirrors of MemoryVLLM.answer), methods.py (the four methods),
                     ui.py (rich layouts), runtime.py (log capture, VRAM sampling, results files), rubric.py
data/sam/            The Buried Clue dataset and its build script
data/speed/          speed-race corpus and its build script
data/hound/          the book, its passages, questions and build script
results/             one JSON per run (+ CSV/PNG for the speed race)
logs/                raw engine logs (not committed)
```
