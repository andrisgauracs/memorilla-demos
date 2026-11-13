"""Browser dashboard that mirrors a demo while it runs, or replays a saved run on its recorded timeline.

    python -m demos.dashboard                                  # live: follows the newest results/*.json
    python -m demos.dashboard --replay results/clue-<time>.json [--speed 1.0] [--pause 6]

It listens on 127.0.0.1 only. On a remote GPU machine, open it through an SSH tunnel:

    ssh -N -L 8000:localhost:8000 <user>@<gpu-host>      # then browse to http://localhost:8000

Live mode reads the results file the demo rewrites after every answer; nothing is computed here. Replay mode reveals
a saved run's answers after their recorded latencies x --speed (press R in the page to restart), like --replay in the
terminal. The page needs no internet access (no external scripts or fonts).
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import time
from urllib.parse import parse_qs, urlparse
from typing import Any

from demos.config import REPO_ROOT, rel
from demos.runtime import scrub

PAGE = Path(__file__).with_name("dashboard.html")
DEMOS = ("sanity", "clue", "pmv2", "speed", "book")
SLOT_ORDER = {
    "sanity": ["none", "memorilla"],
    "clue": ["none", "rag", "full", "memorilla"],
    "pmv2": ["none", "rag", "full", "memorilla"],
    "book": ["none", "rag", "full", "memorilla"],
}


ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
LOG_DIRS = (REPO_ROOT / "logs", REPO_ROOT / ".cache" / "remote-logs")
FIRST_CHUNK = 12_000  # bytes of an existing log shown when the page first connects
MAX_CHUNK = 64_000


def newest_log(demo: str | None) -> Path | None:
    names = (demo,) if demo else DEMOS
    files = [p for name in names for p in (REPO_ROOT / "logs").glob(f"{name}-*.log")]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def log_for(results: Path) -> Path | None:
    for directory in LOG_DIRS:
        candidate = directory / (results.stem + ".log")
        if candidate.exists():
            return candidate
    return None


def clean_log(raw: bytes) -> str:
    """Bytes of a log -> readable lines: ANSI colours removed, tqdm's carriage-return redraws collapsed, paths scrubbed."""
    text = ANSI.sub("", raw.decode("utf-8", errors="replace"))
    lines = [line.split("\r")[-1] for line in text.split("\n")]
    return scrub("\n".join(lines))


def read_log(path: Path | None, offset: int) -> dict[str, Any]:
    if path is None or not path.exists():
        return {"file": None, "offset": 0, "text": ""}
    size = path.stat().st_size
    if offset <= 0 or offset > size:
        offset = max(0, size - FIRST_CHUNK)
    with open(path, "rb") as handle:
        handle.seek(offset)
        raw = handle.read(MAX_CHUNK)
    return {"file": rel(path), "offset": offset + len(raw), "text": clean_log(raw)}


def newest_results(demo: str | None) -> Path | None:
    names = (demo,) if demo else DEMOS
    files = [p for name in names for p in (REPO_ROOT / "results").glob(f"{name}-*.json")]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


class Replay:
    """A saved run revealed over time: answers appear after their recorded latency x speed."""

    def __init__(self, path: Path, speed: float, pause: float) -> None:
        self.data = json.loads(path.read_text())
        self.path = path
        self.speed = speed
        self.pause = pause
        self.events = self._timeline()
        self.start = time.monotonic()
        log = log_for(path)
        self.log_lines = clean_log(log.read_bytes()).splitlines() if log else []
        self.log_file = rel(log) if log else None

    def _timeline(self) -> list[tuple[float, Any]]:
        demo, events, t = self.data["demo"], [], 0.0
        if demo == "speed":
            runs = self.data["config"]["speed"]["warmup_runs"] + self.data["config"]["speed"]["timed_runs"]
            for index, result in enumerate(self.data["results"]):
                t += result.get("latency_s", 0.0) * runs * self.speed
                events.append((t, index + 1))
        elif demo == "sanity":
            for key in SLOT_ORDER["sanity"]:
                t += self.data["methods"][key]["latency_s"] * self.speed
                events.append((t, key))
        else:
            for qi, answers in enumerate(self.data["answers"]):
                events.append((t, (qi, None)))  # question appears
                t += 1.5
                for key in SLOT_ORDER[demo]:
                    if key in answers:
                        t += answers[key]["latency_s"] * self.speed
                        events.append((t, (qi, key)))
                t += self.pause
        return events

    def restart(self) -> None:
        self.start = time.monotonic()

    def log_view(self) -> dict[str, Any]:
        """A replay reveals the recorded log in step with its timeline (it has no per-line timestamps to replay)."""
        total = self.events[-1][0] if self.events else 0.0
        elapsed = time.monotonic() - self.start
        fraction = 1.0 if total <= 0 else min(1.0, 0.25 + 0.75 * elapsed / total)
        shown = self.log_lines[: int(len(self.log_lines) * fraction)]
        return {"file": self.log_file, "lines": shown[-400:]}

    def view(self) -> dict[str, Any]:
        elapsed = time.monotonic() - self.start
        done = [payload for at, payload in self.events if at <= elapsed]
        data = dict(self.data)
        demo = data["demo"]
        if demo == "speed":
            data["results"] = self.data["results"][: (done[-1] if done else 0)]
        elif demo == "sanity":
            data["methods"] = {k: v for k, v in self.data["methods"].items() if k in done}
        else:
            answers: list[dict[str, Any]] = []
            for qi, key in done:
                while len(answers) <= qi:
                    answers.append({})
                if key is not None:
                    answers[qi][key] = self.data["answers"][qi][key]
            data["answers"] = answers
            if demo == "pmv2":
                data["rows"] = self.data["rows"][: len(answers)]
        data["_complete"] = len(done) == len(self.events)
        return data


def make_handler(args: argparse.Namespace, replay: Replay | None):  # noqa: ANN201
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_: Any) -> None:  # keep the terminal quiet
            pass

        def _send(self, body: bytes, content_type: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path in ("/", "/index.html"):
                self._send(PAGE.read_bytes(), "text/html; charset=utf-8")
            elif self.path.startswith("/api/state"):
                if replay is not None:
                    payload = {"mode": "replay", "file": rel(replay.path), "data": replay.view()}
                else:
                    path = newest_results(args.demo)
                    log = newest_log(args.demo)
                    loading = None
                    if log is not None and not (REPO_ROOT / "results" / f"{log.stem}.json").exists():
                        # the newest run is still loading models: it has a log but no results file yet
                        started = time.mktime(time.strptime("-".join(log.stem.split("-")[-2:]), "%Y%m%d-%H%M%S"))
                        loading = {"demo": log.stem.split("-")[0], "elapsed": round(time.time() - started, 1)}
                    data = None
                    if path is not None:
                        try:
                            data = json.loads(path.read_text())
                        except (OSError, json.JSONDecodeError):  # caught mid-write; the next poll gets it
                            data = None
                    if loading is not None:
                        data = None
                    payload = {"mode": "live", "file": rel(path) if path else None, "data": data, "loading": loading}
                self._send(json.dumps(payload).encode(), "application/json")
            elif self.path.startswith("/api/logs"):
                if replay is not None:
                    payload = {"mode": "replay", **replay.log_view()}
                else:
                    query = parse_qs(urlparse(self.path).query)
                    offset = int(query.get("offset", ["0"])[0] or 0)
                    log = newest_log(args.demo)  # the run being watched: newest log (it exists from the first second)
                    payload = {"mode": "live", **read_log(log, offset)}
                self._send(json.dumps(payload).encode(), "application/json")
            elif self.path == "/api/restart" and replay is not None:
                replay.restart()
                self._send(b"{}", "application/json")
            else:
                self.send_error(404)

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--demo", choices=DEMOS, default=None, help="Live mode: follow only this demo's results.")
    parser.add_argument("--replay", type=Path, default=None, help="Replay a saved results file instead.")
    parser.add_argument("--speed", type=float, default=1.0, help="Replay: latency scale (0 = instant).")
    parser.add_argument("--pause", type=float, default=6.0, help="Replay: seconds between questions.")
    args = parser.parse_args()
    replay = None
    if args.replay:
        path = args.replay if args.replay.is_absolute() else REPO_ROOT / args.replay
        replay = Replay(path, args.speed, args.pause)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args, replay))
    what = f"replaying {rel(replay.path)}" if replay else "following the newest results file"
    print(f"Dashboard on http://localhost:{args.port} ({what}). Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
