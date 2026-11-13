"""Demo 1, "The Buried Clue": can a model connect a question to a fact buried in one unrelated past chat?

Four methods answer each question with the same Qwen3-8B, the same system prompt and greedy decoding:
no memory, RAG top-5, full context, and Memorilla (pmv2 checkpoint).

    python -m demos.clue                 # live; a key press moves to the next question
    python -m demos.clue --auto 8        # live; next question after 8 s
    python -m demos.clue --replay results/clue-<time>.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
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

CHATS_FILE = REPO_ROOT / "data/sam/chats.json"
QUESTIONS_FILE = REPO_ROOT / "data/sam/questions.json"
SLOTS = [
    ("none", "No memory", "none"),
    ("rag", "RAG · top-5 chats", "rag"),
    ("full", "Full context · every chat", "full"),
    ("memorilla", "Memorilla · 16 memory tokens", "memorilla"),
]


def _sha(path: Any) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------------------------------------- rendering
def header(data: dict[str, Any], index: int) -> Group:
    q = data["questions"][index]
    inputs = data["inputs"]
    top = Text()
    top.append("The Buried Clue", style=ui.ACCENT)
    top.append(f"  ·  question {index + 1} of {len(data['questions'])}", style="bold")
    if q["kind"] == "control":
        top.append("  ·  control: the answer is stated plainly in one chat", style="yellow")
    top.append(
        f"\nSam's history: {inputs['num_chats']} chats · {ui.tokens(inputs['history_tokens'])} tokens · "
        f"same Qwen3-8B, same prompt, greedy decoding",
        style="dim",
    )
    question = Panel(Text(q["question"], style="bold"), title="Sam asks", title_align="left", border_style="white")
    return Group(top, question)


def note_for(data: dict[str, Any], index: int):  # noqa: ANN201
    q = data["questions"][index]
    total = data["inputs"]["num_chats"]

    def note(key: str, result: dict[str, Any]) -> Text | None:
        if key == "rag":
            line = Text("retrieved ", style="dim")
            for chat in (i + 1 for i in result["retrieved"]):
                line.append(f"#{chat} ", style="bold green" if chat == q["clue_chat"] else "cyan")
            hit = q["clue_chat"] - 1 in result["retrieved"]
            line.append(f"· clue #{q['clue_chat']} " + ("found" if hit else "missed"),
                        style="bold green" if hit else "bold red")
            return line
        if key == "full":
            if result.get("truncated"):
                return Text(f"saw only the last {result['fraction_seen']:.0%} of the history (truncated)", style="red")
            return Text(f"saw all {total} chats", style="dim")
        if key == "memorilla":
            return Text(
                f"embedding {ui.seconds(result['embed_s'])} · answering {ui.seconds(result['generate_s'])}",
                style="dim",
            )
        return None

    return note


def reveal(data: dict[str, Any], index: int) -> Text:
    q = data["questions"][index]
    line = Text()
    line.append(f"Where the answer is: chat #{q['clue_chat']} of {data['inputs']['num_chats']} - ", style="dim")
    line.append(q["clue_summary"], style="dim italic")
    return line


def show_question(data: dict[str, Any], index: int, console: Any, produce: Any) -> None:
    if console.is_terminal:
        console.clear()
    board = ui.AnswerBoard(
        console,
        header(data, index),
        SLOTS,
        columns=2,
        note=note_for(data, index),
        max_chars=data["config"]["display"].get("panel_chars", 520),
        footer=reveal(data, index),
    )
    board.run(produce)
    console.print()


def summary(data: dict[str, Any]) -> Table:
    table = Table(title="The Buried Clue · summary", title_justify="left", title_style=ui.ACCENT, expand=True)
    table.add_column("Question", ratio=3)
    for _key, title, kind in SLOTS:
        table.add_column(title.split(" · ")[0], justify="center", style=ui.COLORS[kind], ratio=1)
    for q, answers in zip(data["questions"], data["answers"], strict=True):
        cells = []
        for key, _, _ in SLOTS:
            r = answers[key]
            mark = "[bold green]✓[/]" if r["rubric"]["pass"] else "[bold red]✗[/]"
            cells.append(f"{mark} {ui.seconds(r['latency_s'])}")
        table.add_row(q["question"] + (" [yellow](control)[/]" if q["kind"] == "control" else ""), *cells)
    table.add_section()
    passed = [f"{sum(a[k]['rubric']['pass'] for a in data['answers'])}/{len(data['answers'])}" for k, _, _ in SLOTS]
    table.add_row("[bold]Correct[/]", *passed)
    avg = [f"{sum(a[k]['prompt_tokens'] for a in data['answers']) // len(data['answers']):,}" for k, _, _ in SLOTS]
    table.add_row("[dim]Avg tokens the LLM received[/]", *avg)
    return table


def finish(data: dict[str, Any], console: Any) -> None:
    console.print(summary(data))
    console.print(ui.footer(data))


# ---------------------------------------------------------------------------------------------------------- live run
def run_live(config: dict[str, Any], console: Any, auto: float | None) -> int:
    from memorilla import benchmarks

    cfg = config["clue"]
    system_prompt = getattr(benchmarks, cfg["system_prompt"])
    max_new = config["generation"]["max_new_tokens"]
    chats = json.loads(CHATS_FILE.read_text())["chats"]
    questions = json.loads(QUESTIONS_FILE.read_text())["questions"]
    documents = [chat["text"] for chat in chats]
    results_path = new_results_path("clue", config)
    log_path = log_path_for(results_path, config)
    data: dict[str, Any] = {
        "demo": "clue",
        "timestamp": now_iso(),
        "config": config,
        "dataset": {"chats": rel(CHATS_FILE), "chats_sha256": _sha(CHATS_FILE),
                    "questions": rel(QUESTIONS_FILE), "questions_sha256": _sha(QUESTIONS_FILE)},
        "questions": questions,
        "answers": [],
    }
    stage = "start"

    with VramMonitor() as vram:
        try:
            with ui.loading_line(console) as status, capture_logs(log_path):
                import time

                from demos import methods
                from demos.models import count_tokens, load_memorilla
                from memorilla.utils import seed_everything

                seed_everything(config["seed"])

                def on_stage(text: str) -> None:
                    nonlocal stage
                    stage = text
                    status(text)

                model, load = load_memorilla(config, cfg["checkpoint"], on_stage)
                data.update(run_metadata())
                data["load"] = load
                on_stage("Embedding Sam's chats for the RAG index")
                start = time.perf_counter()
                doc_vectors = methods.embed(model, documents)
                data["inputs"] = {
                    "num_chats": len(documents),
                    "history_tokens": count_tokens(model, documents),
                    "index_build_s": round(time.perf_counter() - start, 3),
                }

                def run(key: str, question: str) -> dict[str, Any]:
                    if key == "none":
                        return methods.run_no_memory(model, system_prompt, question, max_new)
                    if key == "rag":
                        return methods.run_rag(model, system_prompt, question, documents, doc_vectors,
                                               cfg["top_k"], max_new)
                    if key == "full":
                        return methods.run_full_context(model, system_prompt, question, documents,
                                                        config["decoder"]["max_model_len"], max_new)
                    return methods.run_memorilla(model, system_prompt, question, documents, max_new)

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
            save_results(results_path, data)  # after every question, so a crash keeps what ran
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
    console = make_console(config["display"]["width"])
    return run_live(config, console, args.auto)


if __name__ == "__main__":
    raise SystemExit(main())
