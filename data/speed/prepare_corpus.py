"""Build the speed-race corpus: 512 passages of exactly 250 Qwen3-8B tokens (128,000 tokens in total).

    python data/speed/prepare_corpus.py      # writes data/speed/passages.json

The Hound of the Baskervilles (Gutenberg #2852, Demo 3's book) is only ~72K tokens, i.e. ~290 passages of 250 tokens,
so the corpus continues with Conan Doyle's The Sign of the Four (Gutenberg #2097) and the opening of A Study in Scarlet
(#244), both also public domain, to reach the paper's 512 documents / ~128K tokens without repeating text. Each book's body (Gutenberg header/footer and front
matter stripped, wrapped lines rejoined) is tokenized and cut into consecutive 250-token windows.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
BOOKS = [
    ("The Hound of the Baskervilles", ROOT / "data/hound/raw/pg2852.txt", "Chapter 1."),
    ("The Sign of the Four", HERE / "raw/pg2097.txt", "Chapter I\n"),
    ("A Study in Scarlet", HERE / "raw/pg244.txt", "A STUDY IN SCARLET.\n"),
]
WINDOW = 250
COUNT = 512


def _book_module():  # noqa: ANN202 - reuse the tokenizer pin from data/hound/prepare_book.py
    spec = importlib.util.spec_from_file_location("prepare_book", ROOT / "data/hound/prepare_book.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def body(path: Path, first_line: str) -> str:
    raw = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    text = raw.split("*** START OF", 1)[1].split("\n", 1)[1].split("*** END OF", 1)[0]
    text = text[text.index(first_line) :]
    paragraphs = [" ".join(line.strip() for line in block.splitlines() if line.strip())
                  for block in re.split(r"\n\s*\n", text)]
    return "\n\n".join(p for p in paragraphs if p)


def main() -> int:
    tok = _book_module().tokenizer()
    passages, sources = [], []
    for title, path, first_line in BOOKS:
        ids = tok.encode(body(path, first_line), add_special_tokens=False).ids
        windows = [ids[i : i + WINDOW] for i in range(0, len(ids) - WINDOW + 1, WINDOW)]
        sources.append({"title": title, "file": str(path.relative_to(ROOT)), "tokens": len(ids),
                        "windows": len(windows), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        for window in windows:
            passages.append({"book": title, "text": tok.decode(window)})
    if len(passages) < COUNT:
        raise SystemExit(f"only {len(passages)} windows, need {COUNT}")
    passages = passages[:COUNT]
    for index, passage in enumerate(passages, start=1):
        passage["id"] = index
        passage["tokens"] = len(tok.encode(passage["text"], add_special_tokens=False).ids)
    payload = {
        "description": f"{COUNT} consecutive {WINDOW}-token windows (Qwen3-8B tokenizer) from public-domain Conan "
        "Doyle novels, for the speed race. Built by data/speed/prepare_corpus.py.",
        "sources": sources,
        "total_tokens": sum(p["tokens"] for p in passages),
        "passages": [{k: p[k] for k in ("id", "book", "tokens", "text")} for p in passages],
    }
    out = HERE / "passages.json"
    out.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    tokens = sorted(p["tokens"] for p in passages)
    print(f"wrote {out.relative_to(ROOT)}: {len(passages)} passages, {payload['total_tokens']:,} tokens "
          f"(re-encoded min/max {tokens[0]}/{tokens[-1]}); sources: "
          + "; ".join(f"{s['title']} {s['tokens']:,} tokens -> {s['windows']} windows" for s in sources))
    return 0


if __name__ == "__main__":
    sys.exit(main())
