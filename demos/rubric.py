"""LLM-free scoring: an answer passes when it matches the rubric's keywords. Same text in, same mark out."""

from __future__ import annotations

import re
from typing import Any


def _norm(text: str) -> str:
    text = text.lower().replace("’", "'").replace("‑", "-").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text)


def score(answer: str, rubric: dict[str, Any]) -> dict[str, Any]:
    """Check an answer against a rubric.

    Rubric keys (all optional):
        all_of:  list of groups; every group needs at least one of its phrases present.
        none_of: phrases that must not appear.

    Phrases are matched case-insensitively as substrings, so "diabet" matches "diabetes" and "diabetic".

    Returns:
        ``{"pass": bool, "matched": [...], "missing": [[...]], "forbidden": [...]}``.
    """
    text = _norm(answer)
    matched, missing = [], []
    for group in rubric.get("all_of", []):
        hits = [phrase for phrase in group if _norm(phrase) in text]
        (matched.extend(hits) if hits else missing.append(group))
    forbidden = [phrase for phrase in rubric.get("none_of", []) if _norm(phrase) in text]
    return {"pass": not missing and not forbidden, "matched": matched, "missing": missing, "forbidden": forbidden}
