"""Prefetch every pinned model and dataset file the demos use (run by `make setup`; safe to re-run)."""

from __future__ import annotations

from demos.config import load_config
from demos.models import memorilla_data_root, snapshot

CHECKPOINTS = ("pmv2", "stage3")


def main() -> None:
    config = load_config(profile="h100")  # downloads do not depend on the GPU profile
    for key in ("decoder", "encoder"):
        spec = config["models"][key]
        print(f"  {spec['repo']}@{spec['revision'][:10]}")
        snapshot(spec, config)
    spec = config["models"]["memorilla"]
    print(f"  {spec['repo']}@{spec['revision'][:10]} ({', '.join(CHECKPOINTS)})")
    snapshot(spec, config, allow_patterns=[f"{name}/*" for name in CHECKPOINTS])
    sanity = config["sanity"]
    print(f"  {config['datasets']['memorilla_data']['repo']} {sanity['config']}/{sanity['split']}")
    memorilla_data_root(config, sanity["config"], sanity["split"])


if __name__ == "__main__":
    main()
