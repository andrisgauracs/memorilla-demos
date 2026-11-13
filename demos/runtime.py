"""Shared run-time plumbing: the on-camera console, log capture, VRAM sampling, run metadata and results files."""

from __future__ import annotations

from contextlib import contextmanager
import datetime as dt
import importlib.metadata as md
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Iterator

from rich.console import Console

from demos.config import REPO_ROOT, rel, repo_path

SCHEMA_VERSION = 1
_ORIGINAL_STDOUT_FD: int | None = None


def make_console(width: int) -> Console:
    """A console bound to the *original* terminal, so it keeps working while fds 1/2 are redirected to a log."""
    global _ORIGINAL_STDOUT_FD
    if _ORIGINAL_STDOUT_FD is None:
        sys.stdout.flush()
        _ORIGINAL_STDOUT_FD = os.dup(1)
    stream = open(_ORIGINAL_STDOUT_FD, "w", buffering=1, closefd=False)  # noqa: SIM115
    columns = min(width, shutil.get_terminal_size((width, 35)).columns)
    return Console(file=stream, force_terminal=stream.isatty() or None, width=columns, highlight=False)


@contextmanager
def capture_logs(log_path: Path) -> Iterator[None]:
    """Send everything written to fds 1 and 2 (vLLM, NCCL, tqdm, C++ prints) to ``log_path``."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    sys.stdout.flush()
    sys.stderr.flush()
    saved = os.dup(1), os.dup(2)
    with open(log_path, "a") as log:
        os.dup2(log.fileno(), 1)
        os.dup2(log.fileno(), 2)
        try:
            yield
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            os.dup2(saved[0], 1)
            os.dup2(saved[1], 2)
            os.close(saved[0])
            os.close(saved[1])


def shutdown_engines(log_path: Path) -> None:
    """Tear down torch.distributed (which vLLM initialises) so no NCCL warning is printed on screen at exit."""
    with capture_logs(log_path):
        try:
            import torch.distributed as dist

            if dist.is_available() and dist.is_initialized():
                dist.destroy_process_group()
        except Exception:  # noqa: BLE001 - best effort; anything printed goes to the log
            pass


class VramMonitor:
    """Samples device-wide GPU memory with NVML in a background thread and keeps the peak.

    Device-wide means it includes other processes (e.g. a desktop or ComfyUI); ``baseline_mib`` records what was
    already in use before the demo started, so both numbers can be reported honestly.
    """

    def __init__(self, interval_s: float = 0.05) -> None:
        import pynvml

        pynvml.nvmlInit()
        self._nvml = pynvml
        self._handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        self.interval_s = interval_s
        info = pynvml.nvmlDeviceGetMemoryInfo(self._handle)
        self.total_mib = info.total / 2**20
        self.baseline_mib = info.used / 2**20
        self.peak_mib = self.baseline_mib
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.peak_mib = max(self.peak_mib, self.used_mib())
            time.sleep(self.interval_s)

    def used_mib(self) -> float:
        return self._nvml.nvmlDeviceGetMemoryInfo(self._handle).used / 2**20

    def __enter__(self) -> VramMonitor:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join()

    def summary(self) -> dict[str, float]:
        other = self._other_processes_mib()
        return {
            "total_mib": round(self.total_mib),
            "baseline_used_mib": round(self.baseline_mib),
            "peak_used_mib": round(self.peak_mib),
            "peak_demo_mib": round(self.peak_mib - self.baseline_mib),
            "other_processes_mib": round(other),
        }

    def _other_processes_mib(self) -> float:
        try:
            procs = self._nvml.nvmlDeviceGetComputeRunningProcesses(self._handle)
            return sum((p.usedGpuMemory or 0) for p in procs if p.pid != os.getpid()) / 2**20
        except Exception:
            return float("nan")


def _version(name: str) -> str | None:
    try:
        return md.version(name)
    except md.PackageNotFoundError:
        return None


def _git(*args: str) -> str | None:
    try:
        return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return None


def _memorilla_commit() -> str | None:
    try:
        info = json.loads(md.distribution("memorilla").read_text("direct_url.json") or "{}")
        return info.get("vcs_info", {}).get("commit_id")
    except Exception:
        return None


def run_metadata() -> dict[str, Any]:
    """Hardware, software and git facts for the results file. No hostnames, usernames or absolute paths."""
    hardware: dict[str, Any] = {"cpu": None, "ram_gib": None, "gpu": None}
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                hardware["cpu"] = line.split(":", 1)[1].strip()
                break
        mem_kib = int(Path("/proc/meminfo").read_text().split()[1])
        hardware["ram_gib"] = round(mem_kib / 2**20, 1)
    except Exception:
        pass
    try:
        import pynvml
        import torch

        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        hardware["gpu"] = {
            "name": pynvml.nvmlDeviceGetName(handle),
            "memory_mib": round(pynvml.nvmlDeviceGetMemoryInfo(handle).total / 2**20),
            "driver": pynvml.nvmlSystemGetDriverVersion(),
            "compute_capability": ".".join(map(str, torch.cuda.get_device_capability(0))),
        }
    except Exception:
        pass
    import torch

    software = {
        "python": platform.python_version(),
        "os": platform.platform(terse=True),
        "torch": torch.__version__,
        "torch_cuda": torch.version.cuda,
        **{name: _version(name) for name in ("vllm", "transformers", "xformers", "memorilla")},
        "memorilla_commit": _memorilla_commit(),
        "vllm_use_v1": os.environ.get("VLLM_USE_V1"),
        "vllm_attention_backend": os.environ.get("VLLM_ATTENTION_BACKEND"),
    }
    dirty = _git("status", "--porcelain")
    git = {"commit": _git("rev-parse", "HEAD"), "dirty": bool(dirty) if dirty is not None else None}
    return {"hardware": hardware, "software": software, "git": git}


def new_results_path(demo: str, config: dict[str, Any]) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return repo_path(config["paths"]["results"]) / f"{demo}-{stamp}.json"


def log_path_for(results_path: Path, config: dict[str, Any]) -> Path:
    return repo_path(config["paths"]["logs"]) / (results_path.stem + ".log")


def scrub(text: str) -> str:
    """Remove absolute paths that would reveal a username or machine layout (e.g. inside tracebacks)."""
    text = text.replace(str(REPO_ROOT) + "/", "").replace(str(REPO_ROOT), ".")
    home = str(Path.home())
    if home not in ("/", ""):
        text = text.replace(home, "~")
    return re.sub(r"/(?:home|Users)/[^/\s\"']+", "~", text)


def save_results(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": SCHEMA_VERSION, **payload}
    path.write_text(scrub(json.dumps(payload, indent=2, ensure_ascii=False)) + "\n")


def load_results(path: Path) -> dict[str, Any]:
    data = json.loads(repo_path(path).read_text())
    if data.get("schema_version") != SCHEMA_VERSION:
        raise SystemExit(f"{rel(path)} has schema {data.get('schema_version')}, expected {SCHEMA_VERSION}.")
    return data


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")
