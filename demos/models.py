"""Pinned downloads and model loading, built on Memorilla's own API.

Everything here either calls upstream code directly (``MemoryModule.from_pretrained``, ``MemoryVLLM``,
``embed_texts``, ``build_messages``, ``apply_chat_template``, ``load_split``) or mirrors it line for line where the
demos need something upstream does not expose (a pinned revision, an encoder ``max_model_len``, a timing breakdown).
Each mirror says what it mirrors.
"""

from __future__ import annotations

from collections.abc import Callable
import functools
import os
from pathlib import Path
import time
from typing import Any

from demos.config import repo_path

MEMORY_TOKENS_FALLBACK = 16


# ----------------------------------------------------------------------------------------------------------- downloads
def snapshot(spec: dict[str, str], config: dict[str, Any], allow_patterns: list[str] | None = None) -> Path:
    """Download (or find in the cache) one model repo at its pinned revision."""
    from huggingface_hub import snapshot_download

    return Path(
        snapshot_download(
            repo_id=spec["repo"],
            revision=spec["revision"],
            allow_patterns=allow_patterns,
            cache_dir=config["paths"].get("model_cache"),
        )
    )


def memorilla_data_root(config: dict[str, Any], dataset: str, split: str) -> Path:
    """Fetch one split of Memorilla-Data at its pinned revision into the layout ``memorilla.data.load_split`` reads.

    Mirrors ``memorilla.data.ensure_data`` (same ``local_dir`` layout and file pattern) but passes ``revision``.
    """
    from huggingface_hub import snapshot_download
    from memorilla.data import split_complete

    spec = config["datasets"]["memorilla_data"]
    root = repo_path(config["paths"]["memorilla_data"])
    if not split_complete(dataset, split, root):
        snapshot_download(
            repo_id=spec["repo"],
            repo_type="dataset",
            revision=spec["revision"],
            allow_patterns=[f"data/{dataset}/{split}-*.parquet"],
            local_dir=root,
        )
    return root


def load_row(config: dict[str, Any], dataset: str, split: str, row: int) -> dict[str, Any]:
    from memorilla.data import load_split

    root = memorilla_data_root(config, dataset, split)
    return load_split(dataset, split, root)[row]


# ------------------------------------------------------------------------------------------------------------- loading
def apply_engine_env(config: dict[str, Any]) -> None:
    """Environment that must be set before vLLM is imported."""
    if config.get("attention_backend"):
        os.environ["VLLM_ATTENTION_BACKEND"] = str(config["attention_backend"])


def load_encoder_engine(config: dict[str, Any], path: Path) -> Any:
    """Mirrors ``memorilla.embeddings.load_encoder`` and adds ``max_model_len`` and ``seed``.

    Upstream leaves ``max_model_len`` at the encoder's 40,960-token default, which on a 32 GB card needs ~6 GiB of KV
    cache. The demos only embed short documents, so a shorter window changes nothing about the vectors.
    """
    from vllm import LLM

    enc = config["encoder"]
    return LLM(
        model=str(path),
        task="embed",
        dtype="bfloat16",
        gpu_memory_utilization=enc["gpu_memory_utilization"],
        max_model_len=enc["max_model_len"],
        seed=config["seed"],
        **(enc.get("engine_extra") or {}),
    )


def kv_capacity_tokens(engine: Any) -> int | None:
    """How many tokens fit in an engine's KV cache (vLLM V0 engine)."""
    try:
        cache = engine.llm_engine.cache_config
        return int(cache.num_gpu_blocks) * int(cache.block_size)
    except Exception:
        return None


def load_memorilla(
    config: dict[str, Any], subfolder: str, on_stage: Callable[[str], None] = lambda _: None
) -> tuple[Any, dict[str, Any]]:
    """Load decoder + encoder + one memory checkpoint, as ``MemoryVLLM.from_pretrained`` does, with pinned revisions.

    ``from_pretrained`` takes no revision and no encoder ``max_model_len``, so this does its three steps itself:
    ``MemoryModule.from_pretrained`` on the pinned checkpoint folder, the encoder engine, then the ``MemoryVLLM``
    constructor. Extra decoder engine options from the config (seed, ``engine_extra``) are passed by wrapping the
    ``LLM`` class that ``memorilla.vllm`` instantiates, without copying its constructor.

    Returns:
        The model and load facts (stage timings, KV capacities, checkpoint info).
    """
    apply_engine_env(config)
    facts: dict[str, Any] = {"stages_s": {}}

    def stage(name: str) -> Any:
        on_stage(name)
        facts["_t"] = time.perf_counter()

    def done(key: str) -> None:
        facts["stages_s"][key] = round(time.perf_counter() - facts.pop("_t"), 2)

    stage("Checking pinned model files")
    decoder_path = snapshot(config["models"]["decoder"], config)
    encoder_path = snapshot(config["models"]["encoder"], config)
    checkpoint_root = snapshot(config["models"]["memorilla"], config, allow_patterns=[f"{subfolder}/*"])
    done("files")

    stage(f"Loading memory module ({subfolder})")
    from memorilla.memory import MemoryModule

    memory = MemoryModule.from_pretrained(checkpoint_root / subfolder)
    done("memory_module")

    stage("Loading encoder · Qwen3-Embedding-4B")
    encoder = load_encoder_engine(config, encoder_path)
    done("encoder")

    stage("Loading decoder · Qwen3-8B")
    import memorilla.vllm as memorilla_vllm

    dec = config["decoder"]
    original_llm = memorilla_vllm.LLM
    memorilla_vllm.LLM = functools.partial(original_llm, seed=config["seed"], **(dec.get("engine_extra") or {}))
    try:
        model = memorilla_vllm.MemoryVLLM(
            str(decoder_path),
            memory,
            max_model_len=dec["max_model_len"],
            gpu_memory_utilization=dec["gpu_memory_utilization"],
            encoder=encoder,
        )
    finally:
        memorilla_vllm.LLM = original_llm
    done("decoder")

    facts["checkpoint"] = subfolder
    facts["num_memories"] = int(model.memory.num_memories)
    facts["kv_cache_tokens"] = {"decoder": kv_capacity_tokens(model.engine), "encoder": kv_capacity_tokens(encoder)}
    return model, facts


# ----------------------------------------------------------------------------------------------------------- inference
def memorilla_answer(
    model: Any, question: str, documents: list[str], system_prompt: str, max_new_tokens: int
) -> dict[str, Any]:
    """Mirrors ``MemoryVLLM.answer`` step by step, timing the embedding and generation parts separately."""
    import torch
    from memorilla.data import apply_chat_template, build_messages
    from memorilla.embeddings import embed_texts

    start = time.perf_counter()
    embeddings = torch.from_numpy(embed_texts(model.encoder, [*documents, question]))
    embedded = time.perf_counter()
    messages = build_messages(question, system_prompt, num_memories=model.memory.num_memories)
    prompt = apply_chat_template(model.tokenizer, messages, add_generation_prompt=True)
    inputs = model.tokenizer(prompt, return_tensors="pt")
    (text,) = model.generate(
        inputs["input_ids"],
        inputs["attention_mask"],
        doc_embeds=embeddings[None, :-1],
        doc_padding_mask=torch.zeros(1, len(documents), dtype=torch.bool),
        question_embeds=embeddings[-1:],
        max_new_tokens=max_new_tokens,
    )
    finished = time.perf_counter()
    text = text.strip()
    return {
        "answer": text,
        "prompt_tokens": int(inputs["input_ids"].shape[1]),
        "memory_tokens": int(model.memory.num_memories),
        "output_tokens": len(model.tokenizer.encode(text, add_special_tokens=False)),
        "embed_s": round(embedded - start, 4),
        "generate_s": round(finished - embedded, 4),
        "latency_s": round(finished - start, 4),
    }


def text_answer(model: Any, system_prompt: str, user_content: str, max_new_tokens: int) -> dict[str, Any]:
    """Plain text prompt on the *same* vLLM engine MemoryVLLM started (no second copy of Qwen3-8B).

    Prompt layout and decoding follow ``evaluate_baselines.build_prompts`` / ``generate_baseline``: system + user chat
    rendered with ``apply_chat_template`` (thinking off), greedy, ``clean_generation`` on the output. The engine was
    started with ``enable_prompt_embeds=True``; token-id prompts are tried first, and if that engine refuses them the
    same ids are sent as embeddings from the decoder's own embedding matrix. Which path ran is recorded.
    """
    from memorilla.data import apply_chat_template

    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_content}]
    ids = model.tokenizer.encode(
        apply_chat_template(model.tokenizer, messages, add_generation_prompt=True), add_special_tokens=False
    )
    return generate_from_ids(model, ids, max_new_tokens)


def generate_from_ids(model: Any, ids: list[int], max_new_tokens: int) -> dict[str, Any]:
    """Greedy generation from prompt token ids on MemoryVLLM's own engine (see ``text_answer``)."""
    import torch
    from memorilla.metrics import clean_generation
    from vllm import SamplingParams

    sampling = SamplingParams(max_tokens=max_new_tokens, temperature=0.0, top_p=1.0)
    start = time.perf_counter()
    try:
        (output,) = model.engine.generate([{"prompt_token_ids": ids}], sampling_params=sampling, use_tqdm=False)
        mode = "token_ids"
    except Exception as error:  # noqa: BLE001 - fall back and record why
        with torch.inference_mode():
            embeds = model.embed_tokens(torch.tensor(ids, device=model.device))
        start = time.perf_counter()
        (output,) = model.engine.generate([{"prompt_embeds": embeds}], sampling_params=sampling, use_tqdm=False)
        mode = f"prompt_embeds (token ids refused: {type(error).__name__})"
    finished = time.perf_counter()
    return {
        "answer": clean_generation(output.outputs[0].text),
        "prompt_tokens": len(ids),
        "output_tokens": len(output.outputs[0].token_ids),
        "latency_s": round(finished - start, 4),
        "input_mode": mode,
    }


def count_tokens(model: Any, texts: list[str]) -> int:
    return sum(len(model.tokenizer.encode(text, add_special_tokens=False)) for text in texts)
