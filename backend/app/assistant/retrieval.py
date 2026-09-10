"""Bounded hybrid retrieval with Chinese bigram lexical baseline.

JSON vectors intentionally use exact cosine search for the small local corpus.
This is not an ANN/vector-database scalability claim. All filtering is performed
before content enters the model. No cross-user retrieval cache is used.
"""

import math
import re
import uuid
from collections import Counter
from typing import Any, Literal

from sqlmodel import Session, select

from app.api.permissions import require_plant_access
from app.assistant.provider import ProviderError, embed, embedding_enabled
from app.assistant.schemas import Evidence
from app.core.config import settings
from app.models import KnowledgeDocument, User


def tokens(text: str) -> Counter[str]:
    words = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", text.casefold())
    output: list[str] = []
    for word in words:
        if re.fullmatch(r"[\u4e00-\u9fff]+", word):
            output.extend(word[index : index + 2] for index in range(len(word) - 1))
        else:
            output.append(word)
    return Counter(
        word
        for word in output
        if word not in {"什么", "怎么", "如何", "请问", "一下", "这个", "那个"}
    )


def split_content(content: str) -> list[dict[str, Any]]:
    # Stable character offsets retain an exact source span for every citation.
    return [
        {
            "index": index,
            "start": start,
            "end": min(start + 700, len(content)),
            "text": content[start : start + 700],
        }
        for index, start in enumerate(range(0, len(content), 600))
    ]


def prepare_chunks(
    content: str, *, allow_external: bool
) -> tuple[list[dict[str, Any]], str | None]:
    chunks = split_content(content)
    if not embedding_enabled() or not allow_external:
        return chunks, None
    vectors = embed([chunk["text"] for chunk in chunks])
    for chunk, vector in zip(chunks, vectors, strict=True):
        chunk["vector"] = vector
    return chunks, settings.AI_EMBEDDING_MODEL


def cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    denominator = math.sqrt(sum(x * x for x in left) * sum(x * x for x in right))
    return (
        sum(x * y for x, y in zip(left, right, strict=True)) / denominator
        if denominator
        else 0.0
    )


def retrieve(
    *,
    session: Session,
    user: User,
    plant_id: uuid.UUID,
    query: str,
    allow_external: bool = False,
    strategy: Literal["auto", "lexical", "vector"] = "auto",
) -> tuple[list[Evidence], str]:
    require_plant_access(session=session, user=user, plant_id=plant_id)
    documents = session.exec(
        select(KnowledgeDocument).where(KnowledgeDocument.plant_id == plant_id)
    ).all()
    query_tokens = tokens(query)
    if not query_tokens:
        return [], "lexical"
    corpus = [
        (document, chunk, tokens(document.title + " " + chunk["text"]))
        for document in documents
        for chunk in document.chunks
    ]
    document_frequency: Counter[str] = Counter()
    for _, _, terms in corpus:
        document_frequency.update(terms.keys())
    average_length = sum(sum(terms.values()) for _, _, terms in corpus) / max(
        1, len(corpus)
    )
    mode = "lexical"
    query_vector: list[float] | None = None
    if (
        strategy != "lexical"
        and allow_external
        and embedding_enabled()
        and any(doc.embedding_model == settings.AI_EMBEDDING_MODEL for doc in documents)
    ):
        try:
            query_vector = embed([query])[0]
            mode = "vector" if strategy == "vector" else "hybrid"
        except ProviderError:
            if strategy == "vector":
                raise
            # A vector outage must not prevent authorized business tools from running.
            mode = "lexical_embedding_unavailable"
    if strategy == "vector" and query_vector is None:
        raise ProviderError("embedding_required_for_vector_comparison")
    candidates: list[tuple[float, Evidence]] = []
    for document, chunk, chunk_tokens in corpus:
        overlap = sum((query_tokens & chunk_tokens).values())
        lexical = overlap / max(1, sum(query_tokens.values()))
        semantic = 0.0
        if (
            query_vector is not None
            and document.embedding_model == settings.AI_EMBEDDING_MODEL
        ):
            semantic = cosine(query_vector, chunk.get("vector", []))
        # The cutoff is model/corpus-specific, not a universal cosine threshold.
        if strategy == "vector" and semantic < settings.AI_MIN_SEMANTIC_SCORE:
            continue
        if lexical < 0.12 and (
            query_vector is None or semantic < settings.AI_MIN_SEMANTIC_SCORE
        ):
            continue
        evidence_id = f"doc:{document.id}:v{document.version}:c{chunk['index']}"
        length_factor = 0.25 + 0.75 * sum(chunk_tokens.values()) / max(
            1, average_length
        )
        bm25 = sum(
            math.log(
                1
                + (len(corpus) - document_frequency[term] + 0.5)
                / (document_frequency[term] + 0.5)
            )
            * chunk_tokens[term]
            * 2.2
            / (chunk_tokens[term] + 1.2 * length_factor)
            for term in query_tokens
            if chunk_tokens[term]
        )
        candidates.append(
            (
                bm25,
                Evidence(
                    id=evidence_id,
                    kind="document",
                    title=document.title,
                    data={
                        "document_id": str(document.id),
                        "version": document.version,
                        "content_sha256": document.content_sha256,
                        "start": chunk["start"],
                        "end": chunk["end"],
                        "text": chunk["text"],
                        "score": round(lexical + semantic, 4),
                        "lexical_score": round(lexical, 4),
                        "semantic_score": round(semantic, 4),
                        "bm25_score": round(bm25, 4),
                    },
                ),
            )
        )
    candidates.sort(key=lambda candidate: (-candidate[0], candidate[1].id))
    # Rank fusion avoids adding incomparable cosine and lexical scales.
    fused = {item.id: 1 / (60 + rank) for rank, (_, item) in enumerate(candidates, 1)}
    semantic_candidates = sorted(
        (
            item
            for _, item in candidates
            if item.data["semantic_score"] >= settings.AI_MIN_SEMANTIC_SCORE
        ),
        key=lambda item: (-item.data["semantic_score"], item.id),
    )
    for rank, item in enumerate(semantic_candidates, 1):
        fused[item.id] += 1 / (60 + rank)
    candidates.sort(key=lambda candidate: (-fused[candidate[1].id], candidate[1].id))
    if strategy == "vector":
        candidates.sort(
            key=lambda candidate: (
                -candidate[1].data["semantic_score"],
                candidate[1].id,
            )
        )
    selected: list[Evidence] = []
    per_document: Counter[str] = Counter()
    for _, item in candidates:
        key = item.data["document_id"]
        if per_document[key] >= 2:
            continue
        per_document[key] += 1
        item.data["score"] = (
            item.data["semantic_score"]
            if strategy == "vector"
            else round(fused[item.id], 6)
        )
        selected.append(item)
        if len(selected) == 5:
            break
    return selected, mode
