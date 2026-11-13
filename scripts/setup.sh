#!/usr/bin/env bash
# One-time setup: uv + Python 3.11 + the pinned stack, all inside the repo.
#
# TORCH_BACKEND selects which CUDA build of the *same* pinned torch 2.7.0 is installed:
#   cu128 (default) - required on Blackwell (RTX 5090, sm_120); also runs on H100.
#   cu126           - the build plain `pip install torch==2.7.0` gets from PyPI (no sm_120 kernels).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
cd "$REPO_ROOT"
TORCH_BACKEND="${TORCH_BACKEND:-cu128}"

if ! command -v uv >/dev/null 2>&1; then
    echo "==> Installing uv into .tools/bin"
    mkdir -p .tools/bin
    curl -LsSf https://astral.sh/uv/0.9.5/install.sh | sh
fi
uv --version

if [ ! -x .venv/bin/python ]; then
    echo "==> Creating .venv (Python 3.11)"
    uv venv --python 3.11 .venv
fi
source .venv/bin/activate

echo "==> Installing torch 2.7.0 / torchvision 0.22.0 / torchaudio 2.7.0 / xformers 0.0.30 ($TORCH_BACKEND build)"
uv pip install --index-url "https://download.pytorch.org/whl/$TORCH_BACKEND" \
    torch==2.7.0 torchvision==0.22.0 torchaudio==2.7.0 xformers==0.0.30

echo "==> Installing memorilla (pinned commit), vllm 0.9.1 and the demo package"
uv pip install -e .

echo "==> Installed versions"
python - <<'EOF'
import importlib.metadata as md
for name in ("torch", "torchvision", "torchaudio", "xformers", "vllm", "transformers", "memorilla"):
    print(f"  {name:<12} {md.version(name)}")
EOF

echo "==> GPU check"
python -m demos.check_gpu

echo "==> Downloading pinned models and data"
python -m demos.download
