# Sourced by the Makefile and scripts/setup.sh. Keeps every tool, model and cache inside the repo, so the demos never
# write to the shared ~/.cache, ~/.triton or ~/.local, and deleting the repo removes everything they downloaded.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export REPO_ROOT
export PATH="$REPO_ROOT/.tools/bin:$PATH"
export UV_INSTALL_DIR="$REPO_ROOT/.tools/bin"
export UV_NO_MODIFY_PATH=1
export UV_CACHE_DIR="$REPO_ROOT/.cache/uv"
export UV_PYTHON_INSTALL_DIR="$REPO_ROOT/.tools/python"
export HF_HOME="$REPO_ROOT/.cache/hf"
export XDG_CACHE_HOME="$REPO_ROOT/.cache/xdg"
export VLLM_CACHE_ROOT="$REPO_ROOT/.cache/vllm"
export TRITON_CACHE_DIR="$REPO_ROOT/.cache/triton"
export TORCHINDUCTOR_CACHE_DIR="$REPO_ROOT/.cache/inductor"
export NLTK_DATA="$REPO_ROOT/.cache/nltk"
export MEMORILLA_HOME="$REPO_ROOT"
export TOKENIZERS_PARALLELISM=false
export VLLM_NO_USAGE_STATS=1
export DO_NOT_TRACK=1
# HF_TOKEN is read from your own environment if you set it; the models and data used here are public.
