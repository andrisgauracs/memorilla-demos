"""The four answering methods compared in the demos, all on the same Qwen3-8B engine with greedy decoding.

- no memory:     the question alone.
- RAG top-k:     the k chats/passages most cosine-similar to the question, embedded with the same Qwen3-Embedding-4B
                 vectors Memorilla receives. Mirrors ``evaluate_baselines.retrieve`` (normalise, dot product, argsort,
                 most similar first) and ``evaluate_baselines.user_message`` (documents joined by a blank line, then
                 the question). evaluate_baselines.py is a top-level script, not part of the installed package, so the
                 few lines are mirrored here.
- full context:  every document in the prompt, in stored order; truncated from the front if it does not fit, exactly
                 like ``evaluate_baselines.build_prompts`` (which keeps ``ids[-max_input_tokens:]``).
- Memorilla:     ``MemoryVLLM.answer`` (see ``demos.models.memorilla_answer``).
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from demos.models import memorilla_answer

DOCUMENT_SEPARATOR = "\n\n"  # evaluate_baselines.DOCUMENT_SEPARATOR
GENERATION_RESERVE = 32  # evaluate_baselines.GENERATION_RESERVE


def user_message(documents: list[str], question: str) -> str:
    if documents:
        return DOCUMENT_SEPARATOR.join(documents) + DOCUMENT_SEPARATOR + question
    return question


def embed(model: Any, texts: list[str]) -> np.ndarray:
    from memorilla.embeddings import embed_texts

    return embed_texts(model.encoder, texts)


def top_k(doc_vectors: np.ndarray, question_vector: np.ndarray, k: int) -> tuple[list[int], list[float]]:
    docs = doc_vectors.astype(np.float32)
    docs /= np.linalg.norm(docs, axis=1, keepdims=True)
    q = question_vector.astype(np.float32)
    q /= np.linalg.norm(q)
    sims = docs @ q
    order = np.argsort(-sims, kind="stable")[: min(k, len(docs))]
    return [int(i) for i in order], [round(float(sims[i]), 4) for i in order]


def _prompt_ids(model: Any, system_prompt: str, content: str) -> list[int]:
    from memorilla.data import apply_chat_template

    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": content}]
    return model.tokenizer.encode(
        apply_chat_template(model.tokenizer, messages, add_generation_prompt=True), add_special_tokens=False
    )


def _generate_ids(model: Any, ids: list[int], max_new_tokens: int) -> dict[str, Any]:
    from demos.models import generate_from_ids

    return generate_from_ids(model, ids, max_new_tokens)


def run_no_memory(model: Any, system_prompt: str, question: str, max_new_tokens: int) -> dict[str, Any]:
    ids = _prompt_ids(model, system_prompt, question)
    return _generate_ids(model, ids, max_new_tokens)


def run_rag(
    model: Any,
    system_prompt: str,
    question: str,
    documents: list[str],
    doc_vectors: np.ndarray,
    k: int,
    max_new_tokens: int,
) -> dict[str, Any]:
    """RAG over a prebuilt index: the question is embedded and searched at answer time; the documents were embedded
    once beforehand (that one-off cost is reported separately as ``index_build_s``)."""
    start = time.perf_counter()
    question_vector = embed(model, [question])[0]
    indices, scores = top_k(doc_vectors, question_vector, k)
    retrieved = time.perf_counter()
    ids = _prompt_ids(model, system_prompt, user_message([documents[i] for i in indices], question))
    result = _generate_ids(model, ids, max_new_tokens)
    result["retrieve_s"] = round(retrieved - start, 4)
    result["generate_s"] = result["latency_s"]
    result["latency_s"] = round(result["latency_s"] + result["retrieve_s"], 4)
    result["retrieved"] = indices
    result["scores"] = scores
    return result


def run_full_context(
    model: Any,
    system_prompt: str,
    question: str,
    documents: list[str],
    max_model_len: int,
    max_new_tokens: int,
) -> dict[str, Any]:
    ids = _prompt_ids(model, system_prompt, user_message(documents, question))
    budget = max(max_model_len - max_new_tokens - GENERATION_RESERVE, 1)
    full_length = len(ids)
    if full_length > budget:
        ids = ids[-budget:]
    result = _generate_ids(model, ids, max_new_tokens)
    result["untruncated_prompt_tokens"] = full_length
    result["truncated"] = full_length > budget
    result["fraction_seen"] = round(min(1.0, budget / full_length), 4)
    return result


def run_memorilla(
    model: Any, system_prompt: str, question: str, documents: list[str], max_new_tokens: int
) -> dict[str, Any]:
    return memorilla_answer(model, question, documents, system_prompt, max_new_tokens)


def run_full_context_fit(
    model: Any,
    system_prompt: str,
    question: str,
    documents: list[str],
    max_model_len: int,
    max_new_tokens: int,
    keep: str = "end",
) -> dict[str, Any]:
    """Full context for a collection that may not fit: keep as many whole documents as fit in the window.

    ``keep="end"`` drops documents from the start, the same direction as evaluate_baselines (which keeps
    ``ids[-max_input_tokens:]``) but at document boundaries, so the screen can say exactly what the model saw.
    ``keep="start"`` reads from the beginning instead.
    """
    budget = max(max_model_len - max_new_tokens - GENERATION_RESERVE, 1)
    overhead = len(_prompt_ids(model, system_prompt, user_message([], question)))
    sizes = [len(model.tokenizer.encode(doc + DOCUMENT_SEPARATOR, add_special_tokens=False)) for doc in documents]
    order = list(range(len(documents)))
    if keep == "end":
        order.reverse()
    kept, used = [], overhead
    for index in order:
        if used + sizes[index] > budget:
            break
        kept.append(index)
        used += sizes[index]
    kept.sort()
    while True:  # the estimate is per-document; confirm on the real prompt and drop one more if needed
        ids = _prompt_ids(model, system_prompt, user_message([documents[i] for i in kept], question))
        if len(ids) <= budget or not kept:
            break
        kept = kept[1:] if keep == "end" else kept[:-1]
    result = _generate_ids(model, ids, max_new_tokens)
    total = sum(sizes)
    seen = sum(sizes[i] for i in kept)
    result.update({
        "documents_seen": len(kept),
        "first_document": kept[0] if kept else None,
        "last_document": kept[-1] if kept else None,
        "fraction_seen": round(seen / total, 4) if total else 0.0,
        "truncated": len(kept) < len(documents),
    })
    return result
