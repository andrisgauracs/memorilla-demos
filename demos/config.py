"""Load config.yaml, resolve the hardware profile and apply command-line overrides."""

from __future__ import annotations

import argparse
import copy
import os
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = REPO_ROOT / "config.yaml"
PROFILE_BY_GPU = {"5090": "rtx5090", "H100": "h100"}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _set(config: dict[str, Any], dotted: str, raw: str) -> None:
    *parents, leaf = dotted.split(".")
    node = config
    for key in parents:
        node = node.setdefault(key, {})
    node[leaf] = yaml.safe_load(raw)  # "0.5" -> float, "true" -> bool, "{a: 1}" -> dict


def gpu_name() -> str:
    try:
        import pynvml

        pynvml.nvmlInit()
        return pynvml.nvmlDeviceGetName(pynvml.nvmlDeviceGetHandleByIndex(0))
    except Exception:
        return ""


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", default=os.environ.get("PROFILE"), help="Hardware profile in config.yaml.")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="Override a config value.")
    parser.add_argument("--replay", type=Path, default=None, help="Re-render a saved results JSON; loads no models.")
    parser.add_argument("--replay-speed", type=float, default=None, help="Latency scale for --replay (0 = instant).")


def load_config(profile: str | None = None, overrides: list[str] | None = None) -> dict[str, Any]:
    """Return the base config merged with one profile, then with ``--set`` overrides.

    The profile's keys (encoder, decoder, attention_backend) end up at the top level of the result.
    """
    with open(CONFIG_FILE) as handle:
        raw = yaml.safe_load(handle)
    profiles = raw.pop("profiles")
    name = profile or raw.get("profile", "auto")
    if name == "auto":
        gpu = gpu_name()
        name = next((p for key, p in PROFILE_BY_GPU.items() if key in gpu), None)
        if name is None:
            raise SystemExit(f"No profile matches GPU {gpu!r}; pass --profile ({', '.join(profiles)}).")
    if name not in profiles:
        raise SystemExit(f"Unknown profile {name!r}; choose from {', '.join(profiles)}.")
    config = _merge(raw, profiles[name])
    config["profile"] = name
    for item in overrides or []:
        key, _, value = item.partition("=")
        _set(config, key, value)
    return config


def repo_path(relative: str | Path) -> Path:
    path = Path(relative)
    return path if path.is_absolute() else REPO_ROOT / path


def rel(path: str | Path) -> str:
    """Path relative to the repo, for anything printed on screen or stored in results."""
    try:
        return str(Path(path).resolve().relative_to(REPO_ROOT))
    except ValueError:
        return Path(path).name
