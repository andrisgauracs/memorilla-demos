"""Assemble data/sam/chats.json from the hand-written sources in data/sam/source/ (deterministic, no randomness).

    python data/sam/build_chats.py

Key chats (clues, lookalikes, control) sit at fixed positions; the 137 filler slots are filled round-robin from the
four filler sets in their written order. Each chat is rendered in PersonaMem-v2's document format:
"User: ...\\nAssistant: ..." (a two-exchange chat continues "\\nUser: ...\\nAssistant: ...").
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
TOTAL = 150
FILLER_SETS = ("filler_a", "filler_b", "filler_c", "filler_d")
# Words that must not appear in filler chats: each would leak (or contradict) a buried clue or the control.
FORBIDDEN = re.compile(
    r"\b(dogs?|puppy|pets?|biscuit|vegan|plant-based|leather|bakery|shifts?|3 ?a\.?m|gym|workouts?|"
    r"sisters?|priya|birthdays?|toronto|weekend|getaway|parents?|carsick|car rides?)\b",
    re.IGNORECASE,
)


def _load(name: str) -> object:
    spec = importlib.util.spec_from_file_location(name, HERE / "source" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render(turns: list[list[str]]) -> str:
    labels = {"user": "User", "assistant": "Assistant"}
    roles = [role for role, _ in turns]
    if roles != ["user", "assistant"] * (len(turns) // 2) or len(turns) not in (2, 4):
        raise ValueError(f"turns must alternate user/assistant, 2 or 4 messages: {roles}")
    return "\n".join(f"{labels[role]}: {text.strip()}" for role, text in turns)


def main() -> int:
    key = _load("key_chats")
    slots: dict[int, dict] = {}
    for qid, chat in key.CLUES.items():
        slots[chat["position"]] = {"kind": "clue", "for": qid, **chat}
    slots[key.CONTROL["position"]] = {"kind": "control", "for": "control", **key.CONTROL}
    for chat in key.LOOKALIKES:
        if chat["position"] in slots:
            raise SystemExit(f"two key chats at position {chat['position']}")
        slots[chat["position"]] = {"kind": "lookalike", **chat}

    sets = [_load(name).CHATS for name in FILLER_SETS]
    fillers = [chat for row in zip(*sets, strict=False) for chat in row]  # round-robin a, b, c, d, a, ...
    problems = []
    for name, chats in zip(FILLER_SETS, sets, strict=True):
        for index, chat in enumerate(chats):
            text = " ".join(t for _, t in chat["turns"])
            if hit := FORBIDDEN.search(text):
                problems.append(f"{name}[{index}] '{chat['topic']}': forbidden word {hit.group(0)!r}")
    if problems:
        print("\n".join(problems))
        return 1

    needed = TOTAL - len(slots)
    if len(fillers) < needed:
        raise SystemExit(f"need {needed} filler chats, have {len(fillers)}")
    filler_iter = iter(fillers[:needed])
    chats = []
    for position in range(1, TOTAL + 1):
        chat = slots.get(position) or {"kind": "filler", **next(filler_iter)}
        chats.append({
            "id": position,
            "kind": chat["kind"],
            "for": chat.get("for"),
            "topic": chat["topic"],
            "text": render(chat["turns"]),
        })

    payload = {
        "description": "Sam, a fictional user: 150 past chats with an AI assistant (hand-written, fixed). "
        "Format copied from PersonaMem-v2 documents. Built by data/sam/build_chats.py.",
        "chats": chats,
    }
    out = HERE / "chats.json"
    out.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    lengths = [len(c["text"]) for c in chats]
    digest = hashlib.sha256(out.read_bytes()).hexdigest()[:16]
    print(f"wrote {out.relative_to(HERE.parent.parent)}: {len(chats)} chats, {sum(lengths):,} chars "
          f"(~{sum(lengths) // 4:,} tokens), min/median/max {min(lengths)}/{sorted(lengths)[len(lengths) // 2]}/"
          f"{max(lengths)} chars, sha256 {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
