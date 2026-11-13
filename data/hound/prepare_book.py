"""Turn Project Gutenberg ebook #2852 (The Hound of the Baskervilles, public domain) into ~250-token passages.

    python data/hound/prepare_book.py        # reads data/hound/raw/pg2852.txt, writes data/hound/passages.json

- Keeps the text between the "*** START OF" and "*** END OF" Gutenberg markers, from "Chapter 1." on (drops the
  title page and table of contents).
- Rejoins hard-wrapped lines into paragraphs.
- Packs whole paragraphs into passages of at most TARGET tokens (Qwen3-8B tokenizer at the pinned revision); a single
  paragraph longer than that is split at sentence boundaries. Each passage starts inside one chapter only.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
RAW = HERE / "raw" / "pg2852.txt"
OUT = HERE / "passages.json"
TARGET = 250
TOKENIZER_REPO = "Qwen/Qwen3-8B"
TOKENIZER_REVISION = "b968826d9c46dd6066d109eabc6255188de91218"  # same pin as config.yaml models.decoder
CHAPTER = re.compile(r"^Chapter (\d+)\.$")


def tokenizer():  # noqa: ANN201
    from huggingface_hub import hf_hub_download
    from tokenizers import Tokenizer

    return Tokenizer.from_file(hf_hub_download(TOKENIZER_REPO, "tokenizer.json", revision=TOKENIZER_REVISION))


def paragraphs(raw: str) -> list[tuple[int, str]]:
    body = raw.split("*** START OF", 1)[1].split("\n", 1)[1].split("*** END OF", 1)[0]
    body = body.replace("\r\n", "\n")
    body = body[body.index("Chapter 1.") :]
    out, chapter = [], 0
    for block in re.split(r"\n\s*\n", body):
        lines = [line.strip() for line in block.strip().splitlines() if line.strip()]
        if not lines:
            continue
        if match := CHAPTER.match(lines[0]):
            chapter = int(match.group(1))
            title = " ".join(lines[1:]) if len(lines) > 1 else ""
            out.append((chapter, f"Chapter {chapter}. {title}".strip()))
            continue
        out.append((chapter, " ".join(lines)))
    return out


def main() -> int:
    tok = tokenizer()

    def count(text: str) -> int:
        return len(tok.encode(text, add_special_tokens=False).ids)

    pieces: list[tuple[int, str]] = []
    for chapter, para in paragraphs(RAW.read_text(encoding="utf-8")):
        if count(para) <= TARGET:
            pieces.append((chapter, para))
            continue
        sentence_buffer = ""
        for sentence in re.split(r"(?<=[.!?])\s+|(?<=[.!?][\"'])\s+", para):
            candidate = f"{sentence_buffer} {sentence}".strip()
            if sentence_buffer and count(candidate) > TARGET:
                pieces.append((chapter, sentence_buffer))
                sentence_buffer = sentence
            else:
                sentence_buffer = candidate
        if sentence_buffer:
            pieces.append((chapter, sentence_buffer))

    passages: list[dict] = []
    current_chapter, current = None, []
    for chapter, piece in pieces:
        candidate = "\n\n".join([*current, piece])
        if current and (chapter != current_chapter or count(candidate) > TARGET):
            passages.append({"chapter": current_chapter, "text": "\n\n".join(current)})
            current = []
        current_chapter = chapter
        current.append(piece)
    if current:
        passages.append({"chapter": current_chapter, "text": "\n\n".join(current)})

    for index, passage in enumerate(passages, start=1):
        passage["id"] = index
        passage["tokens"] = count(passage["text"])
    total = sum(p["tokens"] for p in passages)
    payload = {
        "description": "The Hound of the Baskervilles (Arthur Conan Doyle), Project Gutenberg ebook #2852, public "
        "domain; Gutenberg header/footer stripped; ~250-token passages along paragraph boundaries. "
        "Built by data/hound/prepare_book.py.",
        "source_sha256": hashlib.sha256(RAW.read_bytes()).hexdigest(),
        "tokenizer": f"{TOKENIZER_REPO}@{TOKENIZER_REVISION}",
        "total_tokens": total,
        "passages": [{k: p[k] for k in ("id", "chapter", "tokens", "text")} for p in passages],
    }
    OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    sizes = sorted(p["tokens"] for p in passages)
    print(f"wrote {OUT.relative_to(HERE.parent.parent)}: {len(passages)} passages, {total:,} tokens, "
          f"min/median/max {sizes[0]}/{sizes[len(sizes) // 2]}/{sizes[-1]} tokens, chapters 1-{passages[-1]['chapter']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
