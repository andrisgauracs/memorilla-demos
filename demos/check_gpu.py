"""Does the pinned stack (torch 2.7.0, vLLM 0.9.1) actually run on this GPU? Runs each kernel family once.

Each check runs in its own subprocess: some kernels abort the whole process on an unsupported GPU (a CUDA "no kernel
image" error raised from C++), and that must be reported as a failed check, not kill the probe.
Writes results/gpu_check-<timestamp>.json. Exits non-zero only if a core torch check fails.
"""

from __future__ import annotations

import json
import subprocess
import sys
import traceback
from typing import Any, Callable

from rich.console import Console
from rich.table import Table

from demos.config import CONFIG_FILE, repo_path
from demos.runtime import now_iso, run_metadata, save_results


CORE = ("torch_build", "torch_kernels")
MARKER = "CHECK_RESULT "


def run_one(key: str) -> None:
    """Child process: run one check and print its result as a marked JSON line."""
    fn = checks()[key][1]
    try:
        print(MARKER + json.dumps({"ok": True, "detail": fn()}), flush=True)
    except Exception as error:  # noqa: BLE001
        detail = f"{type(error).__name__}: {error}".strip()[:300]
        print(MARKER + json.dumps({"ok": False, "detail": detail, "traceback": traceback.format_exc()}), flush=True)


def run_isolated(key: str, name: str) -> dict[str, Any]:
    proc = subprocess.run([sys.executable, "-m", "demos.check_gpu", "--one", key], capture_output=True, text=True,
                          timeout=600)
    for line in proc.stdout.splitlines():
        if line.startswith(MARKER):
            return {"check": name, **json.loads(line[len(MARKER):])}
    tail = [line for line in (proc.stderr + proc.stdout).splitlines() if line.strip()][-3:]
    return {"check": name, "ok": False, "detail": f"process aborted (exit {proc.returncode}): " + " | ".join(tail)[-400:],
            "stderr_tail": (proc.stderr or "")[-4000:]}


def checks() -> dict[str, tuple[str, Callable[[], str]]]:
    import torch

    def torch_build() -> str:
        major, minor = torch.cuda.get_device_capability(0)
        arch = f"sm_{major}{minor}"
        archs = torch.cuda.get_arch_list()
        if arch not in archs and f"compute_{major}{minor}" not in archs:
            raise RuntimeError(f"torch {torch.__version__} has no kernels for {arch}; built for {' '.join(archs)}")
        return f"torch {torch.__version__} (CUDA {torch.version.cuda}) includes {arch}"

    def torch_kernels() -> str:
        a = torch.randn(1024, 1024, device="cuda", dtype=torch.bfloat16)
        b = (a @ a).softmax(-1).float().sum().item()
        sdpa = torch.nn.functional.scaled_dot_product_attention(
            *(torch.randn(1, 8, 128, 128, device="cuda", dtype=torch.bfloat16) for _ in range(3)), is_causal=True)
        torch.cuda.synchronize()
        return f"bf16 matmul/softmax ok ({b:.1f}), SDPA ok {tuple(sdpa.shape)}"

    def vllm_custom_ops() -> str:
        import vllm
        import vllm._C  # noqa: F401  compiled kernels

        x = torch.randn(4, 4096, device="cuda", dtype=torch.bfloat16)
        w = torch.ones(4096, device="cuda", dtype=torch.bfloat16)
        out = torch.empty_like(x)
        torch.ops._C.rms_norm(out, x, w, 1e-6)
        torch.cuda.synchronize()
        ref = x.float() * torch.rsqrt(x.float().pow(2).mean(-1, keepdim=True) + 1e-6)
        err = (out.float() - ref).abs().max().item()
        if err > 0.05:
            raise RuntimeError(f"rms_norm mismatch {err}")
        return f"vllm {vllm.__version__}: _C rms_norm ok (max err {err:.3g})"

    def vllm_flash_attn() -> str:
        from vllm.vllm_flash_attn import flash_attn_varlen_func

        q, k, v = (torch.randn(64, 8, 128, device="cuda", dtype=torch.bfloat16) for _ in range(3))
        cu = torch.tensor([0, 64], device="cuda", dtype=torch.int32)
        out = flash_attn_varlen_func(q, k, v, max_seqlen_q=64, cu_seqlens_q=cu, max_seqlen_k=64, cu_seqlens_k=cu,
                                     causal=True)
        torch.cuda.synchronize()
        return f"vllm_flash_attn varlen ok {tuple(out.shape)}"

    def xformers_attn() -> str:
        import xformers
        import xformers.ops as xops

        q, k, v = (torch.randn(1, 64, 8, 128, device="cuda", dtype=torch.bfloat16) for _ in range(3))
        out = xops.memory_efficient_attention(q, k, v)
        torch.cuda.synchronize()
        return f"xformers {xformers.__version__} memory_efficient_attention ok {tuple(out.shape)}"

    def triton_kernel() -> str:
        import triton
        import triton.language as tl

        globals()["tl"] = tl  # @triton.jit resolves names from module globals, not this function's locals

        @triton.jit
        def add(x_ptr, y_ptr, out_ptr, n, BLOCK: tl.constexpr):
            offs = tl.program_id(0) * BLOCK + tl.arange(0, BLOCK)
            mask = offs < n
            tl.store(out_ptr + offs, tl.load(x_ptr + offs, mask=mask) + tl.load(y_ptr + offs, mask=mask), mask=mask)

        x = torch.randn(4096, device="cuda")
        out = torch.empty_like(x)
        add[(4,)](x, x, out, 4096, BLOCK=1024)
        torch.cuda.synchronize()
        if not torch.allclose(out, 2 * x):
            raise RuntimeError("triton add mismatch")
        return f"triton {triton.__version__} JIT kernel ok"

    return {
        "torch_build": ("torch build has this GPU's arch", torch_build),
        "torch_kernels": ("torch CUDA kernels", torch_kernels),
        "vllm_ops": ("vLLM compiled ops (_C)", vllm_custom_ops),
        "vllm_flash_attn": ("vLLM FlashAttention", vllm_flash_attn),
        "xformers": ("xFormers attention", xformers_attn),
        "triton": ("Triton JIT", triton_kernel),
    }


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--one":
        run_one(sys.argv[2])
        return 0
    import torch

    results = [run_isolated(key, name) for key, (name, _) in checks().items()]
    core_ok = all(r["ok"] for r, key in zip(results, checks(), strict=True) if key in CORE)
    console = Console(highlight=False)
    table = Table(title=f"GPU check · {torch.cuda.get_device_name(0)}", title_justify="left")
    table.add_column("check")
    table.add_column("")
    table.add_column("detail", overflow="fold")
    for row in results:
        table.add_row(row["check"], "[green]✓[/]" if row["ok"] else "[red]✗[/]", row["detail"])
    console.print(table)

    import yaml

    config = yaml.safe_load(CONFIG_FILE.read_text())
    path = repo_path(config["paths"]["results"]) / f"gpu_check-{now_iso()[:19].replace(':', '').replace('T', '-')}.json"
    save_results(path, {"demo": "gpu_check", "timestamp": now_iso(), **run_metadata(), "checks": results})
    console.print(f"[dim]saved {path.relative_to(repo_path('.'))}[/]")
    return 0 if core_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
