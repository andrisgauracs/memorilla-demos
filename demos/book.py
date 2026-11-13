"""Demo 3: a book too big for the context window - The Hound of the Baskervilles.

No memory, RAG top-5, full context (as much of the book as fits in Qwen3-8B's window) and Memorilla (stage3
checkpoint) answer plot questions. Prompts follow upstream's NarrativeQA protocol in memorilla/benchmarks.py:
Memorilla uses the benchmark's system prompt, the text-only baselines use its baseline prompt.

    python -m demos.book [--auto 8]
    python -m demos.book --replay results/book-<time>.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import traceback
from typing import Any

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from demos import ui
from demos.config import REPO_ROOT, add_common_args, load_config, rel
from demos.rubric import score
from demos.runtime import (
    VramMonitor,
    capture_logs,
    load_results,
    log_path_for,
    make_console,
    new_results_path,
    now_iso,
    run_metadata,
    save_results,
    shutdown_engines,
)

PASSAGES_FILE = REPO_ROOT / "data/hound/passages.json"
QUESTIONS_FILE = REPO_ROOT / "data/hound/questions.json"
SLOTS = [
    ("none", "No memory", "none"),
    ("rag", "RAG · top-5 passages", "rag"),
    ("full", "Full context · as much as fits", "full"),
    ("memorilla", "Memorilla · 16 memory tokens", "memorilla"),
]


def _sha(path: Any) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def chapters_of(data: dict[str, Any], first: int | None, last: int | None) -> str:
    if first is None:
        return "nothing"
    chapters = data["inputs"]["passage_chapters"]
    a, b = chapters[first], chapters[last]
    return f"chapter {a}" if a == b else f"chapters {a}-{b}"


# --------------------------------------------------------------------------------------------------------- rendering
def header(data: dict[str, Any], index: int) -> Group:
    q = data["questions"][index]
    inputs = data["inputs"]
    ratio = inputs["book_tokens"] / 16
    top = Text()
    top.append("A book too big for the context window", style=ui.ACCENT)
    top.append(f"  ·  question {index + 1} of {len(data['questions'])}", style="bold")
    chapters = ", ".join(str(c) for c in q["chapters"])
    top.append(f"  ·  answer in chapter{'s' if len(q['chapters']) > 1 else ''} {chapters}", style="dim")
    top.append("\nThe whole book: ", style="dim")
    top.append(f"{ui.tokens(inputs['book_tokens'])} tokens", style="bold yellow")
    top.append("  vs  ", style="dim")
    top.append("16 memory tokens", style="bold magenta")
    top.append(f"  ({ratio:,.0f}× compression)  ·  LLM context window {ui.tokens(inputs['window'])} tokens", style="dim")
    question = Panel(Text(q["question"], style="bold"), title="Question", title_align="left", border_style="white")
    return Group(top, question)


def note_for(data: dict[str, Any], index: int):  # noqa: ANN201
    q = data["questions"][index]
    evidence = set(q["evidence"])

    def note(key: str, result: dict[str, Any]) -> Text | None:
        if key == "rag":
            line = Text("retrieved ", style="dim")
            for pid in (i + 1 for i in result["retrieved"]):
                line.append(f"#{pid} ", style="bold green" if pid in evidence else "cyan")
            hits = len(evidence & {i + 1 for i in result["retrieved"]})
            line.append(f"· {hits} answer passage{'s' if hits != 1 else ''}", style="bold green" if hits else "red")
            return line
        if key == "full":
            seen = chapters_of(data, result["first_document"], result["last_document"])
            if result["truncated"]:
                where = "last" if data["config"]["book"]["truncate_keep"] == "end" else "first"
                return Text(f"saw only the {where} {result['fraction_seen']:.0%} of the book ({seen})", style="red")
            return Text("saw the whole book", style="dim")
        if key == "memorilla":
            return Text(f"embedding {ui.seconds(result['embed_s'])} · answering {ui.seconds(result['generate_s'])}",
                        style="dim")
        return None

    return note


def reveal(data: dict[str, Any], index: int) -> Text:
    q = data["questions"][index]
    return Text(f"Answer from the book: {q['answer']}", style="dim italic")


def show_question(data: dict[str, Any], index: int, console: Any, produce: Any) -> None:
    if console.is_terminal:
        console.clear()
    board = ui.AnswerBoard(console, header(data, index), SLOTS, columns=2, note=note_for(data, index),
                           max_chars=data["config"]["display"].get("panel_chars", 520), footer=reveal(data, index))
    board.run(produce)
    console.print()


def summary(data: dict[str, Any]) -> Table:
    table = Table(title="The Hound of the Baskervilles · summary", title_justify="left", title_style=ui.ACCENT,
                  expand=True)
    table.add_column("Question", ratio=3)
    for _key, title, kind in SLOTS:
        table.add_column(title.split(" · ")[0], justify="center", style=ui.COLORS[kind], ratio=1)
    for q, answers in zip(data["questions"], data["answers"], strict=True):
        cells = []
        for key, _, _ in SLOTS:
            r = answers[key]
            cells.append(("[bold green]✓[/]" if r["rubric"]["pass"] else "[bold red]✗[/]") + f" {ui.seconds(r['latency_s'])}")
        table.add_row(q["question"], *cells)
    table.add_section()
    n = len(data["answers"])
    table.add_row("[bold]Correct[/]", *[f"{sum(a[k]['rubric']['pass'] for a in data['answers'])}/{n}" for k, _, _ in SLOTS])
    table.add_row("[dim]Avg tokens the LLM received[/]",
                  *[f"{sum(a[k]['prompt_tokens'] for a in data['answers']) // n:,}" for k, _, _ in SLOTS])
    return table


def finish(data: dict[str, Any], console: Any) -> None:
    console.print(summary(data))
    console.print(ui.footer(data))


# ---------------------------------------------------------------------------------------------------------- live run
def run_live(config: dict[str, Any], console: Any, auto: float | None) -> int:
    from memorilla import benchmarks

    cfg = config["book"]
    memorilla_prompt = getattr(benchmarks, cfg["memorilla_system_prompt"])
    baseline_prompt = getattr(benchmarks, cfg["baseline_system_prompt"])
    max_new = cfg["max_new_tokens"]
    corpus = json.loads(PASSAGES_FILE.read_text())
    passages = [p["text"] for p in corpus["passages"]]
    questions = json.loads(QUESTIONS_FILE.read_text())["questions"]
    results_path = new_results_path("book", config)
    log_path = log_path_for(results_path, config)
    window = config["decoder"]["max_model_len"]
    data: dict[str, Any] = {
        "demo": "book", "timestamp": now_iso(), "config": config,
        "dataset": {"passages": rel(PASSAGES_FILE), "passages_sha256": _sha(PASSAGES_FILE),
                    "questions": rel(QUESTIONS_FILE), "questions_sha256": _sha(QUESTIONS_FILE)},
        "inputs": {"num_passages": len(passages), "book_tokens": corpus["total_tokens"], "window": window,
                   "passage_chapters": [p["chapter"] for p in corpus["passages"]]},
        "questions": questions, "answers": [],
    }
    stage = "start"
    with VramMonitor() as vram:
        try:
            with ui.loading_line(console) as status, capture_logs(log_path):
                from demos import methods
                from demos.models import load_memorilla
                from memorilla.utils import seed_everything

                seed_everything(config["seed"])

                def on_stage(text: str) -> None:
                    nonlocal stage
                    stage = text
                    status(text)

                model, load = load_memorilla(config, cfg["checkpoint"], on_stage)
                data.update(run_metadata())
                data["load"] = load
                on_stage("Embedding the book's passages for the RAG index")
                start = time.perf_counter()
                vectors = methods.embed(model, passages)
                data["inputs"]["index_build_s"] = round(time.perf_counter() - start, 3)

                def run(key: str, question: str) -> dict[str, Any]:
                    if key == "none":
                        return methods.run_no_memory(model, baseline_prompt, question, max_new)
                    if key == "rag":
                        return methods.run_rag(model, baseline_prompt, question, passages, vectors, cfg["top_k"],
                                               max_new)
                    if key == "full":
                        return methods.run_full_context_fit(model, baseline_prompt, question, passages, window,
                                                            max_new, keep=cfg["truncate_keep"])
                    return methods.run_memorilla(model, memorilla_prompt, question, passages, max_new)

                on_stage("Warming up every method once")
                data["warmup"] = {key: run(key, cfg["warmup_question"])["latency_s"] for key, _, _ in SLOTS}
        except Exception as error:  # noqa: BLE001
            with capture_logs(log_path):
                traceback.print_exc()
            data["error"] = {"stage": stage, "type": type(error).__name__, "message": str(error)[:2000]}
            data["vram"] = vram.summary()
            save_results(results_path, data)
            console.print(Text(f"✗ Failed while: {stage}", style="bold red"))
            console.print(Text(f"  {type(error).__name__}: {str(error)[:300]}", style="red"))
            console.print(Text(f"  Full log: {rel(log_path)}   Results: {rel(results_path)}", style="dim"))
            return 1

        for index, q in enumerate(questions):
            answers: dict[str, dict[str, Any]] = {}
            data["answers"].append(answers)

            def produce(key: str, q: dict[str, Any] = q, answers: dict[str, Any] = answers) -> dict[str, Any]:
                with capture_logs(log_path):
                    try:
                        result = run(key, q["question"])
                    except Exception as error:  # noqa: BLE001 - shown on screen and kept in the results
                        traceback.print_exc()
                        result = {"answer": "", "error": f"{type(error).__name__}: {str(error)[:200]}",
                                  "prompt_tokens": 0, "latency_s": 0.0}
                result["rubric"] = score(result["answer"], q["rubric"])
                answers[key] = result
                return result

            show_question(data, index, console, produce)
            data["vram"] = vram.summary()
            save_results(results_path, data)
            if index < len(questions) - 1:
                ui.wait_for_key(console, auto, "  press any key for the next question")
    data["vram"] = vram.summary()
    save_results(results_path, data)
    shutdown_engines(log_path)
    finish(data, console)
    console.print(Text(f"Saved {rel(results_path)}  ·  log {rel(log_path)}", style="dim"))
    return 0


def replay(path: Any, speed: float | None, auto: float | None) -> int:
    data = load_results(path)
    if "error" in data and not data.get("answers"):
        raise SystemExit(f"{rel(path)} is from a failed run: {data['error']}")
    console = make_console(data["config"]["display"]["width"])
    speed = speed if speed is not None else data["config"]["display"]["replay_speed"]
    for index, answers in enumerate(data["answers"]):
        show_question(data, index, console, ui.replay_producer(answers, speed))
        if index < len(data["answers"]) - 1:
            ui.wait_for_key(console, auto, "  press any key for the next question")
    finish(data, console)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(parser)
    parser.add_argument("--auto", type=float, default=None, help="Seconds before the next question (no key press).")
    args = parser.parse_args()
    if args.replay:
        return replay(args.replay, args.replay_speed, args.auto)
    config = load_config(args.profile, args.set)
    return run_live(config, make_console(config["display"]["width"]), args.auto)


if __name__ == "__main__":
    raise SystemExit(main())
