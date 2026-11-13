"""Demo 1b: the same four methods on real PersonaMem-v2 test rows, the data the pmv2 checkpoint was trained for.

Rows are drawn by a rule fixed in config.yaml before any run (`pmv2_check`: seeded sample of the test split), and every
drawn row is reported. Scoring is upstream's own PersonaMem-v2 metric (memorilla.metrics): the answer and each answer
option are embedded with Qwen3-Embedding-4B and the answer counts as correct when its nearest option is the reference.

    python -m demos.pmv2 [--auto 6]
    python -m demos.pmv2 --replay results/pmv2-<time>.json
"""

from __future__ import annotations

import argparse
import random
import traceback
from typing import Any

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from demos import ui
from demos.config import add_common_args, load_config, rel
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

SLOTS = [
    ("none", "No memory", "none"),
    ("rag", "RAG · top-5 chats", "rag"),
    ("full", "Full context · every chat", "full"),
    ("memorilla", "Memorilla · 16 memory tokens", "memorilla"),
]


def sample_rows(cfg: dict[str, Any], split_size: int) -> list[int]:
    return sorted(random.Random(cfg["seed"]).sample(range(split_size), cfg["rows"]))


def choice_score(model: Any, answer: str, choices: list[str], reference: str) -> dict[str, Any]:
    """Upstream's choice metric for one answer (memorilla.metrics.choice_accuracy, unrolled to keep the pick)."""
    from memorilla.embeddings import embed_texts
    from memorilla.metrics import choice_index, choice_predictions

    nonempty = [c for c in choices if c.strip()]
    vectors = embed_texts(model.encoder, [answer.strip() or " ", *nonempty])
    (picked,) = choice_predictions(vectors[:1], [vectors[1:]], [choices])
    gold = choice_index(choices, reference)
    return {"pass": picked == gold, "picked": picked, "gold": gold, "matched": []}


# --------------------------------------------------------------------------------------------------------- rendering
def header(data: dict[str, Any], index: int) -> Group:
    row = data["rows"][index]
    top = Text()
    top.append("PersonaMem-v2 · the checkpoint's own kind of data", style=ui.ACCENT)
    top.append(f"  ·  test row {row['row']} ({index + 1} of {len(data['rows'])})", style="bold")
    top.append(f"\n{row['num_documents']} past chats · {ui.tokens(row['history_tokens'])} tokens · scored by "
               f"upstream's metric: nearest of {row['num_choices']} answer options", style="dim")
    question = Panel(Text(row["question"], style="bold"), title="The user asks", title_align="left",
                     border_style="white")
    return Group(top, question)


def note_for(data: dict[str, Any], index: int):  # noqa: ANN201
    def note(key: str, result: dict[str, Any]) -> Text | None:
        rubric = result["rubric"]
        label = f"nearest option {rubric['picked'] + 1} · correct is {rubric['gold'] + 1}"
        line = Text(label, style="green" if rubric["pass"] else "red")
        if key == "rag":
            line.append(f"  ·  chats {' '.join(f'#{i + 1}' for i in result['retrieved'])}", style="dim")
        if key == "full" and result.get("truncated"):
            line.append(f"  ·  saw last {result['fraction_seen']:.0%}", style="red")
        return line

    return note


def reveal(data: dict[str, Any], index: int) -> Text:
    reference = data["rows"][index]["reference"]
    return Text(f"Reference answer: {reference[:230]}{'…' if len(reference) > 230 else ''}", style="dim italic")


def show_row(data: dict[str, Any], index: int, console: Any, produce: Any) -> None:
    if console.is_terminal:
        console.clear()
    board = ui.AnswerBoard(console, header(data, index), SLOTS, columns=2, note=note_for(data, index),
                           max_chars=data["config"]["display"].get("panel_chars", 520), footer=reveal(data, index))
    board.run(produce)
    console.print()


def summary(data: dict[str, Any]) -> Table:
    table = Table(title="PersonaMem-v2 · summary", title_justify="left", title_style=ui.ACCENT, expand=True)
    table.add_column("Test row", ratio=3)
    for _key, title, kind in SLOTS:
        table.add_column(title.split(" · ")[0], justify="center", style=ui.COLORS[kind], ratio=1)
    for row, answers in zip(data["rows"], data["answers"], strict=True):
        cells = [("[bold green]✓[/]" if answers[k]["rubric"]["pass"] else "[bold red]✗[/]")
                 + f" {ui.seconds(answers[k]['latency_s'])}" for k, _, _ in SLOTS]
        table.add_row(f"{row['row']}: {row['question'][:60]}", *cells)
    table.add_section()
    n = len(data["answers"])
    table.add_row("[bold]Correct[/]", *[f"{sum(a[k]['rubric']['pass'] for a in data['answers'])}/{n}"
                                         for k, _, _ in SLOTS])
    table.add_row("[dim]Avg tokens the LLM received[/]",
                  *[f"{sum(a[k]['prompt_tokens'] for a in data['answers']) // n:,}" for k, _, _ in SLOTS])
    return table


def finish(data: dict[str, Any], console: Any) -> None:
    console.print(summary(data))
    console.print(ui.footer(data))


# ---------------------------------------------------------------------------------------------------------- live run
def run_live(config: dict[str, Any], console: Any, auto: float | None) -> int:
    from memorilla import benchmarks

    cfg = config["pmv2_check"]
    system_prompt = getattr(benchmarks, cfg["system_prompt"])
    max_new = config["generation"]["max_new_tokens"]
    results_path = new_results_path("pmv2", config)
    log_path = log_path_for(results_path, config)
    data: dict[str, Any] = {"demo": "pmv2", "timestamp": now_iso(), "config": config, "rows": [], "answers": []}
    stage = "start"
    with VramMonitor() as vram:
        try:
            with ui.loading_line(console) as status, capture_logs(log_path):
                from demos import methods
                from demos.models import count_tokens, load_memorilla, memorilla_data_root
                from memorilla.data import load_split
                from memorilla.utils import seed_everything

                seed_everything(config["seed"])

                def on_stage(text: str) -> None:
                    nonlocal stage
                    stage = text
                    status(text)

                on_stage("Loading PersonaMem-v2 test set (pinned)")
                split = load_split("pmv2", "test", memorilla_data_root(config, "pmv2", "test"))
                rows = sample_rows(cfg, len(split))
                model, load = load_memorilla(config, cfg["checkpoint"], on_stage)
                data.update(run_metadata())
                data["load"] = load
                data["sample"] = {"rule": f"random.Random({cfg['seed']}).sample(range({len(split)}), {cfg['rows']})",
                                  "rows": rows}
                on_stage("Warming up every method once")
                warm = split[rows[0]]
                docs0 = list(warm["documents"])
                methods.run_no_memory(model, system_prompt, warm["question"], max_new)
                methods.run_full_context(model, system_prompt, warm["question"], docs0,
                                         config["decoder"]["max_model_len"], max_new)
                methods.run_memorilla(model, system_prompt, warm["question"], docs0, max_new)
        except Exception as error:  # noqa: BLE001
            with capture_logs(log_path):
                traceback.print_exc()
            data["error"] = {"stage": stage, "type": type(error).__name__, "message": str(error)[:2000]}
            data["vram"] = vram.summary()
            save_results(results_path, data)
            console.print(Text(f"✗ Failed while: {stage}: {type(error).__name__}: {str(error)[:300]}", style="red"))
            console.print(Text(f"  Full log: {rel(log_path)}", style="dim"))
            return 1

        for index, row_id in enumerate(rows):
            item = split[row_id]
            docs = list(item["documents"])
            choices = list(item["choices"])
            with capture_logs(log_path):
                vectors = methods.embed(model, docs)
                history_tokens = count_tokens(model, docs)
            data["rows"].append({"row": row_id, "question": item["question"], "reference": item["answer"],
                                 "num_documents": len(docs), "history_tokens": history_tokens,
                                 "num_choices": len([c for c in choices if c.strip()]), "choices": choices})
            answers: dict[str, dict[str, Any]] = {}
            data["answers"].append(answers)

            def produce(key: str, item: Any = item, docs: list[str] = docs, vectors: Any = vectors,
                        choices: list[str] = choices, answers: dict[str, Any] = answers) -> dict[str, Any]:
                with capture_logs(log_path):
                    q = item["question"]
                    if key == "none":
                        result = methods.run_no_memory(model, system_prompt, q, max_new)
                    elif key == "rag":
                        result = methods.run_rag(model, system_prompt, q, docs, vectors, cfg["top_k"], max_new)
                    elif key == "full":
                        result = methods.run_full_context(model, system_prompt, q, docs,
                                                          config["decoder"]["max_model_len"], max_new)
                    else:
                        result = methods.run_memorilla(model, system_prompt, q, docs, max_new)
                    result["rubric"] = choice_score(model, result["answer"], choices, item["answer"])
                answers[key] = result
                return result

            show_row(data, index, console, produce)
            data["vram"] = vram.summary()
            save_results(results_path, data)
            if index < len(rows) - 1:
                ui.wait_for_key(console, auto, "  press any key for the next row")
    data["vram"] = vram.summary()
    save_results(results_path, data)
    shutdown_engines(log_path)
    finish(data, console)
    console.print(Text(f"Saved {rel(results_path)}  ·  log {rel(log_path)}", style="dim"))
    return 0


def replay(path: Any, speed: float | None, auto: float | None) -> int:
    data = load_results(path)
    console = make_console(data["config"]["display"]["width"])
    speed = speed if speed is not None else data["config"]["display"]["replay_speed"]
    for index, answers in enumerate(data["answers"]):
        show_row(data, index, console, ui.replay_producer(answers, speed))
        if index < len(data["answers"]) - 1:
            ui.wait_for_key(console, auto, "  press any key for the next row")
    finish(data, console)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(parser)
    parser.add_argument("--auto", type=float, default=None, help="Seconds before the next row (no key press).")
    args = parser.parse_args()
    if args.replay:
        return replay(args.replay, args.replay_speed, args.auto)
    config = load_config(args.profile, args.set)
    return run_live(config, make_console(config["display"]["width"]), args.auto)


if __name__ == "__main__":
    raise SystemExit(main())
