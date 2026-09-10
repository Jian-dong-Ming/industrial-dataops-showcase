"""Add bundled synthetic manuals ONLY to the existing OPC_DEMO teaching plant.

No external requests by default. --vectorize-bundled plus --confirm explicitly
sends only unchanged bundled synthetic content to the configured embedding API.
"""

import argparse
import hashlib
import json
import logging
from pathlib import Path

from sqlmodel import Session, select

from app.assistant.provider import embedding_enabled
from app.assistant.retrieval import prepare_chunks, split_content
from app.core.config import settings
from app.core.db import engine
from app.models import KnowledgeDocument, Plant, User, get_datetime_utc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--vectorize-bundled", action="store_true")
    args = parser.parse_args()
    if args.vectorize_bundled and not embedding_enabled():
        parser.error("Configure the embedding service before requesting vectorization")
    corpus = json.loads(
        (
            Path(__file__).resolve().parents[1] / "app/assistant/eval_cases.json"
        ).read_text()
    )
    corpus["documents"].extend(
        json.loads(
            (
                Path(__file__).resolve().parents[1] / "app/assistant/manuals.json"
            ).read_text()
        )["documents"]
    )
    with Session(engine) as session:
        plant = session.exec(select(Plant).where(Plant.code == "OPC_DEMO")).first()
        admin = session.exec(
            select(User).where(User.email == settings.FIRST_SUPERUSER)
        ).first()
        if plant is None or admin is None or not admin.is_superuser:
            raise RuntimeError(
                "Existing OPC_DEMO teaching plant and configured superuser are required"
            )
        titles = set(
            session.exec(
                select(KnowledgeDocument.title).where(
                    KnowledgeDocument.plant_id == plant.id
                )
            ).all()
        )
        pending = [
            source for source in corpus["documents"] if source["title"] not in titles
        ]
        logging.warning(
            f"Teaching plant: {plant.name}; add {len(pending)} synthetic manuals; overwrite 0"
        )
        if not args.confirm:
            return
        for source in pending:
            session.add(
                KnowledgeDocument(
                    plant_id=plant.id,
                    created_by_id=admin.id,
                    title=source["title"],
                    content=source["content"],
                    content_sha256=hashlib.sha256(
                        source["content"].encode()
                    ).hexdigest(),
                    chunks=split_content(source["content"]),
                )
            )
        session.commit()
        if args.vectorize_bundled:
            sources = {
                source["title"]: source["content"] for source in corpus["documents"]
            }
            documents = session.exec(
                select(KnowledgeDocument)
                .where(KnowledgeDocument.plant_id == plant.id)
                .with_for_update()
            ).all()
            changed = 0
            for document in documents:
                # Do not send or overwrite user-created/edited manuals.
                if sources.get(document.title) != document.content:
                    continue
                if document.embedding_model == settings.AI_EMBEDDING_MODEL and all(
                    chunk.get("vector") for chunk in document.chunks
                ):
                    continue
                document.chunks, document.embedding_model = prepare_chunks(
                    document.content, allow_external=True
                )
                document.version += 1
                document.updated_at = get_datetime_utc()
                session.add(document)
                changed += 1
            session.commit()
            logging.warning(
                "Vectorized %s unchanged bundled manuals; user content untouched",
                changed,
            )


if __name__ == "__main__":
    main()
