"""Diagnostic (not a demo): does Memorilla pick up Sam's facts at all, and how does that change with collection size?

For two questions, Memorilla answers from shrinking collections: only the chat that holds the answer, a handful of
chats, then all 150. Run once per checkpoint. Results go to results/diagnostic-clue-<checkpoint>-<time>.json and never
replace a demo's results.

    python -m demos.diagnose_clue --checkpoint pmv2
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from rich.table import Table

from demos.config import REPO_ROOT, add_common_args, load_config, rel
from demos.rubric import score
from demos.runtime import (
    capture_logs,
    log_path_for,
    make_console,
    new_results_path,
    now_iso,
    run_metadata,
    save_results,
    shutdown_engines,
)

QUESTIONS = ("control", "parents_dinner")
# 1-based chat numbers. "five" = the clue chat plus the four chats RAG ranked next for the control question.
CONDITIONS = {
    "control": {"only the clue chat": [57], "clue + 4 others": [57, 117, 119, 80, 76], "all 150": None},
    "parents_dinner": {"only the clue chat": [88], "clue + 4 others": [88, 76, 30, 140, 119], "all 150": None},
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(parser)
    parser.add_argument("--checkpoint", required=True)
    args = parser.parse_args()
    config = load_config(args.profile, args.set)
    console = make_console(config["display"]["width"])
    chats = [c["text"] for c in json.loads((REPO_ROOT / "data/sam/chats.json").read_text())["chats"]]
    questions = {q["id"]: q for q in json.loads((REPO_ROOT / "data/sam/questions.json").read_text())["questions"]}
    results_path = new_results_path(f"diagnostic-clue-{args.checkpoint}", config)
    log_path = log_path_for(results_path, config)
    rows: list[dict[str, Any]] = []
    with console.status(f"Loading Memorilla ({args.checkpoint})"), capture_logs(log_path):
        from demos.models import load_memorilla, memorilla_answer
        from memorilla import benchmarks
        from memorilla.utils import seed_everything

        seed_everything(config["seed"])
        model, load = load_memorilla(config, args.checkpoint)
        prompt = getattr(benchmarks, config["clue"]["system_prompt"])
        for qid in QUESTIONS:
            q = questions[qid]
            for label, ids in CONDITIONS[qid].items():
                docs = chats if ids is None else [chats[i - 1] for i in ids]
                result = memorilla_answer(model, q["question"], docs, prompt, config["generation"]["max_new_tokens"])
                rows.append({"question": qid, "condition": label, "num_docs": len(docs),
                             "pass": score(result["answer"], q["rubric"])["pass"], **result})
    data = {"demo": "diagnostic-clue", "checkpoint": args.checkpoint, "timestamp": now_iso(), "config": config,
            **run_metadata(), "load": load, "rows": rows}
    save_results(results_path, data)
    shutdown_engines(log_path)
    table = Table(title=f"Diagnostic · Memorilla {args.checkpoint} on Sam's chats", title_justify="left")
    for column in ("question", "collection", "docs", "", "answer"):
        table.add_column(column, overflow="fold")
    for r in rows:
        table.add_row(r["question"], r["condition"], str(r["num_docs"]),
                      "[green]✓[/]" if r["pass"] else "[red]✗[/]", r["answer"][:160])
    console.print(table)
    console.print(f"[dim]Saved {rel(results_path)}[/]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
