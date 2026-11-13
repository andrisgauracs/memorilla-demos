"""Step 0: reproduce the README's example (PersonaMem-v2 test row 73, the breakfast question, pmv2 checkpoint).

    python -m demos.sanity                      # live run, writes results/sanity-<time>.json and logs/sanity-<time>.log
    python -m demos.sanity --replay results/sanity-<time>.json
"""

from __future__ import annotations

import argparse
import traceback
from typing import Any

from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from demos import ui
from demos.config import add_common_args, load_config, rel
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

SLOTS = [("none", "Qwen3-8B · no memory", "none"), ("memorilla", "Memorilla · pmv2", "memorilla")]
CLUE_KEYWORD = "diabet"


def header(data: dict[str, Any]) -> Group:
    inputs = data["inputs"]
    ratio = inputs["document_tokens"] / data["load"]["num_memories"]
    lines = Text()
    lines.append("Step 0 · Sanity check", style=ui.ACCENT)
    lines.append(f"   PersonaMem-v2 test row {inputs['row']} · checkpoint {data['load']['checkpoint']}\n", style="dim")
    lines.append(f"{inputs['num_documents']} past chats", style="bold")
    lines.append(f" · {ui.tokens(inputs['document_tokens'])} tokens of history → ")
    lines.append(f"{data['load']['num_memories']} memory tokens", style="bold magenta")
    lines.append(f" ({ratio:,.0f}× smaller)\n")
    if inputs.get("clue_index") is not None:
        lines.append(f"The only clue is chat {inputs['clue_index'] + 1} of {inputs['num_documents']}: ", style="dim")
        lines.append(f"“{inputs['clue_excerpt']}”", style="dim italic")
    question = Panel(Text(inputs["question"], style="bold"), title="Question", title_align="left", border_style="white")
    return Group(lines, question)


def render(data: dict[str, Any], console: Any, produce: Any) -> None:
    board = ui.AnswerBoard(console, header(data), SLOTS, columns=2)
    board.run(produce)
    console.print()


def details(data: dict[str, Any], console: Any) -> None:
    load = data["load"]
    kv = load.get("kv_cache_tokens") or {}
    console.print(ui.footer(data))
    console.print(
        Text(
            f"Model load {sum(load['stages_s'].values()):.0f} s  ·  decoder KV cache room "
            f"{ui.tokens(kv.get('decoder') or 0)} tokens  ·  "
            f"baseline prompt via {data['methods']['none'].get('input_mode', '?')}",
            style="dim",
        )
    )


def run_live(config: dict[str, Any], console: Any) -> int:
    from memorilla import benchmarks

    sanity = config["sanity"]
    system_prompt = getattr(benchmarks, sanity["system_prompt"])
    max_new = config["generation"]["max_new_tokens"]
    results_path = new_results_path("sanity", config)
    log_path = log_path_for(results_path, config)
    data: dict[str, Any] = {"demo": "sanity", "timestamp": now_iso(), "config": config}
    stage = "start"

    with VramMonitor() as vram:
        try:
            with ui.loading_line(console) as status, capture_logs(log_path):
                from demos.models import count_tokens, load_memorilla, load_row, memorilla_answer, text_answer
                from memorilla.utils import seed_everything

                seed_everything(config["seed"])

                def on_stage(text: str) -> None:
                    nonlocal stage
                    stage = text
                    status(text)

                on_stage("Loading PersonaMem-v2 test set (pinned)")
                row = load_row(config, sanity["config"], sanity["split"], sanity["row"])
                model, load = load_memorilla(config, sanity["checkpoint"], on_stage)
                docs = list(row["documents"])
                clue = next((i for i, d in enumerate(docs) if CLUE_KEYWORD in d.lower()), None)
                excerpt = docs[clue].split("\n", 1)[0].removeprefix("User: ")[:90] + "…" if clue is not None else None
                data.update(run_metadata())
                data["load"] = load
                data["inputs"] = {
                    "dataset": sanity["config"], "split": sanity["split"], "row": sanity["row"],
                    "question": row["question"], "reference_answer": row["answer"],
                    "num_documents": len(docs), "document_tokens": count_tokens(model, docs),
                    "clue_index": clue, "clue_excerpt": excerpt,
                }
                on_stage("Warming up")
                warm = {"none": text_answer(model, system_prompt, row["question"], max_new),
                        "memorilla": memorilla_answer(model, row["question"], docs, system_prompt, max_new)}
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

        methods: dict[str, dict[str, Any]] = {}
        rubric = sanity["rubric"]

        def produce(key: str) -> dict[str, Any]:
            with capture_logs(log_path):
                if key == "none":
                    result = text_answer(model, system_prompt, row["question"], max_new)
                else:
                    result = memorilla_answer(model, row["question"], docs, system_prompt, max_new)
            result["rubric"] = score(result["answer"], rubric)
            result["warmup_latency_s"] = warm[key]["latency_s"]
            result["same_as_warmup"] = result["answer"] == warm[key]["answer"]
            methods[key] = result
            return result

        data["methods"] = methods
        render(data, console, produce)

        # Check that the timed mirror of MemoryVLLM.answer gives the same text as calling answer() itself.
        with capture_logs(log_path):
            official = model.answer(row["question"], docs, system_prompt=system_prompt, max_new_tokens=max_new)
        data["checks"] = {"mirror_matches_model_answer": official == methods["memorilla"]["answer"],
                          "model_answer": official}
    data["vram"] = vram.summary()
    save_results(results_path, data)
    shutdown_engines(log_path)
    details(data, console)
    if not data["checks"]["mirror_matches_model_answer"]:
        console.print(Text("! model.answer() differs from the timed mirror; see results JSON", style="yellow"))
    console.print(Text(f"Saved {rel(results_path)}  ·  log {rel(log_path)}", style="dim"))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(parser)
    args = parser.parse_args()
    if args.replay:
        data = load_results(args.replay)
        if "methods" not in data:
            raise SystemExit(f"{rel(args.replay)} is from a failed run: {data.get('error')}")
        config = data["config"]
        console = make_console(config["display"]["width"])
        speed = args.replay_speed if args.replay_speed is not None else config["display"]["replay_speed"]
        render(data, console, ui.replay_producer(data["methods"], speed))
        details(data, console)
        return 0
    config = load_config(args.profile, args.set)
    console = make_console(config["display"]["width"])
    return run_live(config, console)


if __name__ == "__main__":
    raise SystemExit(main())
