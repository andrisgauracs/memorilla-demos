"""On-camera building blocks shared by every demo: the loading line, answer panels and the footer.

Live runs and ``--replay`` both go through ``AnswerBoard``, so a retake looks exactly like the original take.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import re
import sys
import time
from typing import Any

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

ACCENT = "bold magenta"
COLORS = {"memorilla": "magenta", "none": "grey70", "rag": "cyan", "full": "yellow"}


def gib(mib: float) -> str:
    return f"{mib / 1024:.1f} GiB"


def seconds(value: float) -> str:
    return f"{value * 1000:.0f} ms" if value < 1 else f"{value:.2f} s"


def tokens(value: int) -> str:
    return f"{value:,}"


@contextmanager
def loading_line(console: Console) -> Iterator[Callable[[str], None]]:
    """One tidy progress line while models load; vLLM's own logs go to logs/ meanwhile."""
    progress = Progress(
        SpinnerColumn(style=ACCENT),
        TextColumn("{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    )
    with progress:
        task = progress.add_task("Starting", total=None)
        yield lambda text: progress.update(task, description=text)


def rubric_mark(result: dict[str, Any] | None) -> Text:
    if not result or "rubric" not in result:
        return Text("")
    return Text("✓", style="bold green") if result["rubric"]["pass"] else Text("✗", style="bold red")


def _split_sentences(text: str) -> list[tuple[int, int]]:
    spans, start = [], 0
    for match in re.finditer(r"(?<=[.!?])\s+|\n+", text):
        spans.append((start, match.start()))
        start = match.end()
    spans.append((start, len(text)))
    return [(a, b) for a, b in spans if b > a]


def excerpt(answer: str, phrases: list[str], max_chars: int) -> Text:
    """The start of an answer, plus the sentence holding the first rubric match if that lies past the cut.

    Matched phrases are highlighted, so viewers can see why a panel got its check mark.
    """
    lower = answer.lower()
    if len(answer) <= max_chars:
        shown = answer
    else:
        cut = answer.rfind(" ", 0, max_chars - 40)
        shown = answer[: cut if cut > 0 else max_chars - 40].rstrip() + " …"
        hits = [lower.find(p.lower()) for p in phrases if lower.find(p.lower()) >= len(shown) - 2]
        if hits:
            first = min(hits)
            for a, b in _split_sentences(answer):
                if a <= first < b:
                    room = max_chars - len(shown) - 2
                    sentence = answer[a:b] if b - a <= room else answer[a : a + room].rstrip() + " …"
                    head = answer[: max(0, max_chars - len(sentence) - 6)]
                    head = head[: head.rfind(" ")] if " " in head else head
                    shown = head.rstrip() + " … " + sentence
                    break
    text = Text(shown)
    for phrase in phrases:
        text.highlight_regex(re.compile(re.escape(phrase), re.IGNORECASE), "bold black on green3")
    return text


def answer_panel(
    title: str,
    kind: str,
    result: dict[str, Any] | None,
    note: Text | None = None,
    max_chars: int = 600,
    waiting: str = "thinking…",
) -> Panel:
    color = COLORS.get(kind, "white")
    if result is None:
        body: Any = Spinner("dots", text=Text(waiting, style="dim"), style=color)
        subtitle = None
    elif result.get("error"):
        body = Text(result["error"], style="red")
        subtitle = Text(result.get("error_label", "failed"), style="red")
    else:
        phrases = (result.get("rubric") or {}).get("matched", [])
        answer = excerpt(result["answer"], phrases, max_chars)
        body = Group(note, answer) if note is not None else answer
        stats = [f"{tokens(result['prompt_tokens'])} tokens in", seconds(result["latency_s"])]
        subtitle = Text("  ·  ".join(stats) + "  ", style="dim") + rubric_mark(result)
    return Panel(body, title=Text(title, style=f"bold {color}"), title_align="left", subtitle=subtitle,
                 subtitle_align="right", border_style=color, padding=(0, 1))


NoteFn = Callable[[str, dict[str, Any]], "Text | None"]


class AnswerBoard:
    """A row (or 2x2 grid) of answer panels that fill in one by one."""

    def __init__(
        self,
        console: Console,
        header: Any,
        slots: list[tuple[str, str, str]],
        columns: int,
        note: NoteFn | None = None,
        max_chars: int = 600,
        footer: Any = None,
    ) -> None:
        self.console = console
        self.header = header
        self.slots = slots  # (key, title, kind)
        self.columns = columns
        self.note = note
        self.max_chars = max_chars
        self.footer = footer
        self.results: dict[str, dict[str, Any]] = {}

    def renderable(self) -> Group:
        grid = Table.grid(expand=True, padding=(0, 1))
        for _ in range(self.columns):
            grid.add_column(ratio=1)
        panels = []
        for key, title, kind in self.slots:
            result = self.results.get(key)
            note = self.note(key, result) if (self.note and result and not result.get("error")) else None
            panels.append(answer_panel(title, kind, result, note, self.max_chars))
        for start in range(0, len(panels), self.columns):
            grid.add_row(*panels[start : start + self.columns])
        parts = [self.header, grid]
        if self.footer is not None and len(self.results) == len(self.slots):
            parts.append(self.footer() if callable(self.footer) else self.footer)
        return Group(*parts)

    def run(self, produce: Callable[[str], dict[str, Any]]) -> None:
        """Fill every slot in order; ``produce(key)`` returns the result (live: computes it; replay: waits)."""
        with Live(self.renderable(), console=self.console, refresh_per_second=12, transient=False) as live:
            for key, _, _ in self.slots:
                self.results[key] = produce(key)
                live.update(self.renderable())


def wait_for_key(console: Console, auto: float | None, message: str) -> None:
    """Pause between questions: a fixed delay with --auto, otherwise one key press (Enter if not a TTY)."""
    if auto is not None:
        time.sleep(auto)
        return
    console.print(Text(message, style="dim"), end="")
    if sys.stdin.isatty():
        import termios
        import tty

        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)
    else:
        sys.stdin.readline()
    console.print("\r" + " " * len(message) + "\r", end="")


def replay_producer(results: dict[str, dict[str, Any]], speed: float) -> Callable[[str], dict[str, Any]]:
    def produce(key: str) -> dict[str, Any]:
        time.sleep(max(0.0, results[key].get("latency_s", 0.0) * speed))
        return results[key]

    return produce


def footer(data: dict[str, Any]) -> Text:
    vram = data.get("vram") or {}
    gpu = (data.get("hardware") or {}).get("gpu") or {}
    sw = data.get("software") or {}
    parts = []
    if vram:
        parts.append(f"Peak VRAM {gib(vram['peak_used_mib'])} / {gib(vram['total_mib'])}")
        if vram.get("baseline_used_mib", 0) > 256:
            parts.append(f"incl. {gib(vram['baseline_used_mib'])} used before start")
    if gpu.get("name"):
        parts.append(gpu["name"].replace("NVIDIA GeForce ", "").replace("NVIDIA ", ""))
    if sw.get("torch"):
        parts.append(f"torch {sw['torch']}")
    if sw.get("vllm"):
        parts.append(f"vLLM {sw['vllm']}")
    return Text("  ·  ".join(parts), style="dim")
