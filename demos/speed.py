"""Demo 2, the speed race: answer latency as the collection grows from 1 to 512 passages of 250 tokens (128K tokens).

    python -m demos.speed
    python -m demos.speed --replay results/speed-<time>.json [--replay-speed 0.2]

Protocol (config.yaml `speed:`): the same question at every size, batch size 1, greedy, max_new_tokens 32 (the paper's
Table 14 setting), one warm-up then the median of three timed runs (the user's spec; the paper used 2 + 5).

- Full context: every passage in the prompt, on a text-only Qwen3-8B engine started like evaluate_baselines.start_engine.
  Up to 32,768 tokens it runs at the native RoPE; above that the paper's serving rule applies (section 3.3: YaRN when the
  input exceeds 32,768), with factor 4 and a 131,072-token window. A size that does not fit in the window, or that runs
  out of GPU memory, is recorded as "out of context/VRAM" and larger sizes are skipped.
- Memorilla: MemoryVLLM (stage3 checkpoint), timed end to end including embedding all passages with
  Qwen3-Embedding-4B, with embedding and answering (memory module + decoding) also reported separately. The paper's
  0.74 s excludes document encoding, so "answering" is the number comparable to it.

Each phase runs in its own spawned process (clean GPU, no leftover vLLM state); the parent only draws the chart.
"""

from __future__ import annotations

import argparse
import csv
import json
import multiprocessing as mp
import os
from pathlib import Path
import queue as queue_module
import statistics
import time
import traceback
from typing import Any

from rich import box
from rich.console import Group
from rich.layout import Layout
from rich.live import Live
from rich.table import Table
from rich.text import Text

from demos import ui
from demos.config import REPO_ROOT, add_common_args, load_config, rel
from demos.runtime import (
    VramMonitor,
    load_results,
    log_path_for,
    make_console,
    new_results_path,
    now_iso,
    run_metadata,
    save_results,
)

PASSAGES_FILE = REPO_ROOT / "data/speed/passages.json"
SERIES = {
    "full": ("Full context", "yellow"),
    "memorilla": ("Memorilla (end to end, incl. embedding)", "magenta"),
    "memorilla_answer": ("Memorilla, answering only (paper's definition)", "orchid"),
}


# ------------------------------------------------------------------------------------------------------------ workers
def _redirect(log_path: str) -> None:
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(fd, 1)
    os.dup2(fd, 2)


def _timed(fn: Any, warmup: int, runs: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    for _ in range(warmup):
        fn()
    results = [fn() for _ in range(runs)]
    return results, results[len(results) // 2]


def _median(results: list[dict[str, Any]], key: str) -> float:
    return round(statistics.median(r[key] for r in results), 4)


def worker_full(config: dict[str, Any], phase: dict[str, Any], log_path: str, events: Any) -> None:
    """Full-context timings on a text-only engine (one context window per phase)."""
    _redirect(log_path)
    try:
        os.environ["VLLM_USE_V1"] = "0"  # same V0 engine type as MemoryVLLM's prompt-embeds engine
        from demos import methods
        from demos.models import apply_engine_env, snapshot
        from memorilla.benchmarks import YARN_ORIGINAL_MAX_POSITION_EMBEDDINGS
        from memorilla.data import apply_chat_template
        from memorilla.metrics import clean_generation
        from memorilla.utils import seed_everything
        from transformers import AutoTokenizer
        from vllm import LLM, SamplingParams

        apply_engine_env(config)
        seed_everything(config["seed"])
        speed = config["speed"]
        passages = [p["text"] for p in json.loads(PASSAGES_FILE.read_text())["passages"]]
        path = str(snapshot(config["models"]["decoder"], config))
        options: dict[str, Any] = {}
        if phase["yarn_factor"]:
            options["hf_overrides"] = {
                "rope_scaling": {"rope_type": "yarn", "factor": phase["yarn_factor"],
                                 "original_max_position_embeddings": YARN_ORIGINAL_MAX_POSITION_EMBEDDINGS},
                "max_position_embeddings": phase["max_model_len"],
            }
        events.put({"type": "stage", "text": f"Loading Qwen3-8B for full context ({phase['label']})"})
        start = time.perf_counter()
        engine = LLM(model=path, dtype="bfloat16", trust_remote_code=True, max_model_len=phase["max_model_len"],
                     gpu_memory_utilization=speed["full_context_gpu_memory_utilization"], seed=config["seed"],
                     **options)
        tokenizer = AutoTokenizer.from_pretrained(path, trust_remote_code=True)
        events.put({"type": "loaded", "phase": phase["label"], "seconds": round(time.perf_counter() - start, 1)})
        budget = phase["max_model_len"] - speed["max_new_tokens"] - methods.GENERATION_RESERVE
        sampling = SamplingParams(max_tokens=speed["max_new_tokens"], temperature=0.0, top_p=1.0)
        system_prompt = getattr(__import__("memorilla.benchmarks", fromlist=["x"]), speed["system_prompt"])

        for size in phase["sizes"]:
            messages = [{"role": "system", "content": system_prompt},
                        {"role": "user", "content": methods.user_message(passages[:size], speed["question"])}]
            ids = tokenizer.encode(apply_chat_template(tokenizer, messages, add_generation_prompt=True),
                                   add_special_tokens=False)
            base = {"type": "result", "series": "full", "size": size, "prompt_tokens": len(ids),
                    "window": phase["max_model_len"], "yarn_factor": phase["yarn_factor"]}
            if len(ids) > budget:
                events.put({**base, "status": "out_of_context",
                            "detail": f"{len(ids):,} tokens > {budget:,}-token budget"})
                continue
            events.put({"type": "stage", "text": f"Full context · {size} passages · {len(ids):,} tokens"})

            def once(ids: list[int] = ids) -> dict[str, Any]:
                t0 = time.perf_counter()
                (out,) = engine.generate([{"prompt_token_ids": ids}], sampling_params=sampling, use_tqdm=False)
                return {"latency_s": time.perf_counter() - t0, "output_tokens": len(out.outputs[0].token_ids),
                        "answer": clean_generation(out.outputs[0].text)}

            try:
                runs, mid = _timed(once, speed["warmup_runs"], speed["timed_runs"])
            except Exception as error:  # noqa: BLE001 - OOM and friends end the series, honestly marked
                traceback.print_exc()
                events.put({**base, "status": "out_of_memory", "detail": f"{type(error).__name__}: {str(error)[:200]}"})
                break
            events.put({**base, "status": "ok", "latency_s": _median(runs, "latency_s"),
                        "runs_s": [round(r["latency_s"], 4) for r in runs],
                        "output_tokens": mid["output_tokens"], "answer": mid["answer"]})
    except Exception as error:  # noqa: BLE001
        traceback.print_exc()
        events.put({"type": "error", "phase": phase["label"], "detail": f"{type(error).__name__}: {str(error)[:300]}"})
    finally:
        events.put({"type": "done", "phase": phase["label"]})


def worker_memorilla(config: dict[str, Any], sizes: list[int], log_path: str, events: Any) -> None:
    _redirect(log_path)
    try:
        from demos.models import load_memorilla, memorilla_answer
        from memorilla import benchmarks
        from memorilla.utils import seed_everything

        seed_everything(config["seed"])
        speed = config["speed"]
        system_prompt = getattr(benchmarks, speed["system_prompt"])
        passages = [p["text"] for p in json.loads(PASSAGES_FILE.read_text())["passages"]]
        start = time.perf_counter()
        model, load = load_memorilla(config, speed["checkpoint"],
                                     lambda text: events.put({"type": "stage", "text": text}))
        events.put({"type": "loaded", "phase": "memorilla", "seconds": round(time.perf_counter() - start, 1),
                    "load": load})
        for size in sizes:
            events.put({"type": "stage", "text": f"Memorilla · {size} passages"})
            docs = passages[:size]
            runs, mid = _timed(
                lambda docs=docs: memorilla_answer(model, speed["question"], docs, system_prompt,
                                                   speed["max_new_tokens"]),
                speed["warmup_runs"], speed["timed_runs"])
            events.put({"type": "result", "series": "memorilla", "size": size, "status": "ok",
                        "latency_s": _median(runs, "latency_s"), "embed_s": _median(runs, "embed_s"),
                        "generate_s": _median(runs, "generate_s"), "runs_s": [r["latency_s"] for r in runs],
                        "prompt_tokens": mid["prompt_tokens"], "output_tokens": mid["output_tokens"],
                        "answer": mid["answer"]})
    except Exception as error:  # noqa: BLE001
        traceback.print_exc()
        events.put({"type": "error", "phase": "memorilla", "detail": f"{type(error).__name__}: {str(error)[:300]}"})
    finally:
        events.put({"type": "done", "phase": "memorilla"})


# ---------------------------------------------------------------------------------------------------------- rendering
def series_points(results: list[dict[str, Any]]) -> dict[str, list[tuple[int, float]]]:
    points: dict[str, list[tuple[int, float]]] = {key: [] for key in SERIES}
    for r in results:
        if r.get("status") != "ok":
            continue
        points[r["series"]].append((r["size"], r["latency_s"]))
        if r["series"] == "memorilla":
            points["memorilla_answer"].append((r["size"], r["generate_s"]))
    return points


def first_failure(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    fails = [r for r in results if r["series"] == "full" and r.get("status") not in (None, "ok")]
    return min(fails, key=lambda r: r["size"]) if fails else None


def chart(data: dict[str, Any], width: int, height: int) -> Text:
    import plotext as plt

    sizes = data["sizes"]
    pos = {size: index for index, size in enumerate(sizes)}
    plt.clear_figure()
    plt.plotsize(width, height)
    plt.theme("clear")
    points = series_points(data["results"])
    for key, (label, color) in SERIES.items():
        if points[key]:
            xs = [pos[s] for s, _ in sorted(points[key])]
            ys = [v for _, v in sorted(points[key])]
            plt.plot(xs, ys, label=label, color=color, marker="hd")
    fail = first_failure(data["results"])
    if fail is not None:
        plt.vline(pos[fail["size"]], color="red")
        plt.text("out of context/VRAM", x=pos[fail["size"]], y=0, color="red", alignment="right")
    plt.xticks(list(range(len(sizes))), [str(s) for s in sizes])
    plt.xlabel("passages in the collection (x 250 tokens)")
    plt.ylabel("seconds")
    plt.ylim(0, None)
    return Text.from_ansi(plt.build())


def table(data: dict[str, Any]) -> Table:
    t = Table(expand=True, padding=(0, 1), title="seconds, median of 3", title_style="dim", box=box.SIMPLE_HEAD)
    t.add_column("docs", justify="right", no_wrap=True)
    t.add_column("full", justify="right", style="yellow", no_wrap=True)
    t.add_column("Memorilla", justify="right", style="magenta", no_wrap=True)
    t.add_column("emb+ans", justify="right", style="dim", no_wrap=True)
    by = {(r["series"], r["size"]): r for r in data["results"]}
    for size in data["sizes"]:
        full, mem = by.get(("full", size)), by.get(("memorilla", size))
        if full is None:
            full_cell = ""
        elif full.get("status") == "ok":
            full_cell = f"{full['latency_s']:.2f}"
        else:
            full_cell = "[red]no fit[/]"
        mem_cell = f"{mem['latency_s']:.2f}" if mem else ""
        split = f"{mem['embed_s']:.2f}+{mem['generate_s']:.2f}" if mem else ""
        t.add_row(str(size), full_cell, mem_cell, split)
    return t


def screen(data: dict[str, Any], status: str, width: int) -> Group:
    top = Text()
    top.append("The speed race", style=ui.ACCENT)
    top.append(f"  ·  same question, 1 → {data['sizes'][-1]} passages of 250 tokens, Qwen3-8B, "
               f"{data['config']['speed']['max_new_tokens']} new tokens max", style="dim")
    layout = Layout(size=26)
    layout.split_row(Layout(chart(data, max(40, width - 50), 25), name="chart"), Layout(table(data), size=48))
    parts = [top, layout]
    if status:
        parts.append(Text(status, style="dim italic"))
    return Group(*parts)


# --------------------------------------------------------------------------------------------------------- export
def export(data: dict[str, Any], results_path: Path) -> tuple[Path, Path]:
    csv_path = results_path.with_suffix(".csv")
    by = {(r["series"], r["size"]): r for r in data["results"]}
    with open(csv_path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["passages", "document_tokens", "full_context_s", "full_context_status",
                         "full_context_prompt_tokens", "full_context_window", "memorilla_end_to_end_s",
                         "memorilla_embed_s", "memorilla_answer_s", "memorilla_prompt_tokens"])
        for size in data["sizes"]:
            f, m = by.get(("full", size), {}), by.get(("memorilla", size), {})
            writer.writerow([size, size * 250, f.get("latency_s", ""), f.get("status", ""), f.get("prompt_tokens", ""),
                             f.get("window", ""), m.get("latency_s", ""), m.get("embed_s", ""),
                             m.get("generate_s", ""), m.get("prompt_tokens", "")])
    png_path = results_path.with_suffix(".png")
    plot_png(data, png_path)
    return csv_path, png_path


def plot_png(data: dict[str, Any], path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colors = {"full": "#f5c542", "memorilla": "#e056fd", "memorilla_answer": "#b39ddb"}
    plt.rcParams.update({"font.size": 26, "axes.edgecolor": "#888888", "text.color": "white",
                         "axes.labelcolor": "white", "xtick.color": "white", "ytick.color": "white"})
    fig, ax = plt.subplots(figsize=(19.2, 10.8), dpi=100, facecolor="#0d1117")
    ax.set_facecolor("#0d1117")
    points = series_points(data["results"])
    for key, (label, _) in SERIES.items():
        if points[key]:
            xs, ys = zip(*sorted(points[key]), strict=True)
            style = {"linestyle": "--", "linewidth": 3} if key == "memorilla_answer" else {"linewidth": 6}
            ax.plot(xs, ys, marker="o", markersize=12, color=colors[key], label=label, **style)
            ax.annotate(f"{ys[-1]:.2f} s", (xs[-1], ys[-1]), textcoords="offset points", xytext=(14, 0),
                        va="center", color=colors[key], fontsize=26, fontweight="bold")
    fail = first_failure(data["results"])
    if fail is not None:
        ax.axvline(fail["size"], color="#ff5555", linewidth=3, linestyle=":")
        ax.text(fail["size"] * 1.06, ax.get_ylim()[1] * 0.5, "out of context/VRAM", color="#ff5555", fontsize=24,
                rotation=90, va="center", ha="left")
    ax.set_xscale("log", base=2)
    ax.set_xlim(0.8, data["sizes"][-1] * 1.9)  # room for the end-of-line value labels
    ax.set_xticks(data["sizes"])
    ax.set_xticklabels([str(s) for s in data["sizes"]])
    ax.set_xlabel("documents in the collection (250 tokens each)", labelpad=16, fontsize=30)
    ax.set_ylabel("seconds per answer", labelpad=16, fontsize=30)
    ax.set_ylim(bottom=0)
    ax.grid(color="#333333", linewidth=1)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    gpu = ((data.get("hardware") or {}).get("gpu") or {}).get("name", "")
    ax.set_title(f"Answer latency vs. collection size · Qwen3-8B · {gpu.replace('NVIDIA ', '')}", pad=24,
                 fontsize=32, color="white")
    ax.legend(loc="upper left", frameon=False, fontsize=24)
    fig.tight_layout()
    fig.savefig(path, facecolor=fig.get_facecolor())
    plt.close(fig)


# ---------------------------------------------------------------------------------------------------------- live run
def phases(config: dict[str, Any]) -> list[dict[str, Any]]:
    speed = config["speed"]
    sizes = speed["sizes"]
    return [
        {"label": "native window", "max_model_len": speed["native_window"], "yarn_factor": None,
         "sizes": [s for s in sizes if s * 250 <= speed["native_window"] - 768]},
        {"label": f"YaRN x{speed['yarn_factor']}", "max_model_len": speed["yarn_window"],
         "yarn_factor": speed["yarn_factor"], "sizes": [s for s in sizes if s * 250 > speed["native_window"] - 768]},
    ]


def run_live(config: dict[str, Any], console: Any) -> int:
    results_path = new_results_path("speed", config)
    log_path = log_path_for(results_path, config)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    corpus = json.loads(PASSAGES_FILE.read_text())
    data: dict[str, Any] = {"demo": "speed", "timestamp": now_iso(), "config": config, **run_metadata(),
                            "corpus": {"file": rel(PASSAGES_FILE), "total_tokens": corpus["total_tokens"],
                                       "sources": corpus["sources"]},
                            "sizes": config["speed"]["sizes"], "results": [], "phases": [], "errors": []}
    ctx = mp.get_context("spawn")
    jobs = [(worker_full, (config, phase)) for phase in phases(config) if phase["sizes"]]
    jobs.append((worker_memorilla, (config, config["speed"]["sizes"])))
    width = console.width
    status = "starting"
    with VramMonitor() as vram, Live(screen(data, status, width), console=console, refresh_per_second=4) as live:
        for target, args in jobs:
            events = ctx.Queue()
            proc = ctx.Process(target=target, args=(*args, str(log_path), events))
            proc.start()
            done = False
            while not done:
                try:
                    event = events.get(timeout=1.0)
                except queue_module.Empty:
                    if not proc.is_alive():
                        data["errors"].append({"phase": target.__name__, "detail": f"exit {proc.exitcode}"})
                        break
                    continue
                kind = event.pop("type")
                if kind == "stage":
                    status = event["text"]
                elif kind == "result":
                    data["results"].append(event)
                    save_results(results_path, {**data, "vram": vram.summary()})
                elif kind == "loaded":
                    data["phases"].append(event)
                elif kind == "error":
                    data["errors"].append(event)
                elif kind == "done":
                    done = True
                live.update(screen(data, status, width))
            proc.join()
        data["vram"] = vram.summary()
        live.update(screen(data, "", width))
    console.print()
    save_results(results_path, data)
    csv_path, png_path = export(data, results_path)
    console.print(ui.footer(data))
    for error in data["errors"]:
        console.print(Text(f"! {error}", style="red"))
    console.print(Text(f"Saved {rel(results_path)} · {rel(csv_path)} · {rel(png_path)} · log {rel(log_path)}",
                       style="dim"))
    return 0 if not data["errors"] else 1


def replay(path: Path, speed: float | None) -> int:
    data = load_results(path)
    console = make_console(data["config"]["display"]["width"])
    speed = speed if speed is not None else data["config"]["display"]["replay_speed"]
    shown = {**data, "results": []}
    runs = data["config"]["speed"]["warmup_runs"] + data["config"]["speed"]["timed_runs"]
    with Live(screen(shown, "", console.width), console=console, refresh_per_second=8) as live:
        for result in data["results"]:
            time.sleep(max(0.0, result.get("latency_s", 0.0) * runs * speed))
            shown["results"].append(result)
            live.update(screen(shown, "", console.width))
    console.print()
    console.print(ui.footer(data))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(parser)
    parser.add_argument("--png", type=Path, default=None, help="With --replay: re-export the PNG/CSV to this path.")
    args = parser.parse_args()
    if args.replay:
        if args.png:
            export(load_results(args.replay), args.png)
            return 0
        return replay(args.replay, args.replay_speed)
    config = load_config(args.profile, args.set)
    return run_live(config, make_console(config["display"]["width"]))


if __name__ == "__main__":
    raise SystemExit(main())
