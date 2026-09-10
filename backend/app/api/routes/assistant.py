import hashlib
import uuid

from fastapi import APIRouter, HTTPException, Query
from sqlmodel import col, func, select

from app.api.deps import CurrentUser, SessionDep
from app.api.permissions import require_plant_access
from app.assistant.provider import ProviderError, embedding_enabled
from app.assistant.retrieval import prepare_chunks, retrieve
from app.assistant.schemas import (
    Answer,
    DocumentInput,
    DocumentPublic,
    Evidence,
    Question,
)
from app.assistant.service import answer_question
from app.core.config import settings
from app.models import AssistantRun, KnowledgeDocument, Message, Plant, get_datetime_utc

router = APIRouter(prefix="/assistant", tags=["ai-assistant"])


@router.get("/status")
def assistant_status(current_user: CurrentUser) -> dict[str, str | bool | int]:
    return {
        "configured": bool(settings.DEEPSEEK_API_KEY),
        "model": settings.DEEPSEEK_MODEL,
        "embedding_configured": embedding_enabled(),
        "read_only": True,
        "max_calls": settings.AI_MAX_CALLS,
        "requests_per_minute": settings.AI_REQUESTS_PER_MINUTE,
        "user_id": str(current_user.id),
    }


@router.post("/ask", response_model=Answer)
def ask(session: SessionDep, current_user: CurrentUser, request: Question) -> Answer:
    return answer_question(session=session, user=current_user, request=request)


@router.get("/documents", response_model=list[DocumentPublic])
def list_documents(
    session: SessionDep, current_user: CurrentUser, plant_id: uuid.UUID
) -> list[KnowledgeDocument]:
    require_plant_access(session=session, user=current_user, plant_id=plant_id)
    return list(
        session.exec(
            select(KnowledgeDocument)
            .where(KnowledgeDocument.plant_id == plant_id)
            .order_by(col(KnowledgeDocument.updated_at).desc())
        ).all()
    )


@router.post("/documents", response_model=DocumentPublic)
def create_document(
    session: SessionDep,
    current_user: CurrentUser,
    plant_id: uuid.UUID,
    body: DocumentInput,
) -> KnowledgeDocument:
    require_plant_access(
        session=session, user=current_user, plant_id=plant_id, write=True
    )
    # Serialize quota checks and creates within one plant.
    plant = session.exec(
        select(Plant).where(Plant.id == plant_id).with_for_update()
    ).first()
    if plant is None:
        raise HTTPException(404, "工厂不存在")
    count = session.exec(
        select(func.count())
        .select_from(KnowledgeDocument)
        .where(KnowledgeDocument.plant_id == plant_id)
    ).one()
    if count >= settings.AI_MAX_DOCUMENTS_PER_PLANT:
        raise HTTPException(409, "当前工厂文档数量已达上限")
    try:
        chunks, model = prepare_chunks(
            body.content, allow_external=body.allow_external_processing
        )
    except ProviderError as exc:
        raise HTTPException(502, f"文档向量化失败（{exc.code}），未保存文档") from exc
    document = KnowledgeDocument(
        plant_id=plant_id,
        title=body.title,
        content=body.content,
        content_sha256=hashlib.sha256(body.content.encode()).hexdigest(),
        chunks=chunks,
        embedding_model=model,
        created_by_id=current_user.id,
    )
    session.add(document)
    session.commit()
    session.refresh(document)
    return document


@router.put("/documents/{document_id}", response_model=DocumentPublic)
def update_document(
    session: SessionDep,
    current_user: CurrentUser,
    document_id: uuid.UUID,
    plant_id: uuid.UUID,
    body: DocumentInput,
) -> KnowledgeDocument:
    require_plant_access(
        session=session, user=current_user, plant_id=plant_id, write=True
    )
    document = session.exec(
        select(KnowledgeDocument)
        .where(
            KnowledgeDocument.id == document_id, KnowledgeDocument.plant_id == plant_id
        )
        .with_for_update()
    ).first()
    if document is None:
        raise HTTPException(404, "文档不存在或不在当前授权范围")
    if body.expected_version != document.version:
        raise HTTPException(409, "文档已变化，请刷新后重新编辑")
    try:
        chunks, model = prepare_chunks(
            body.content, allow_external=body.allow_external_processing
        )
    except ProviderError as exc:
        raise HTTPException(502, f"文档向量化失败（{exc.code}），旧版本未修改") from exc
    document.title, document.content = body.title, body.content
    document.chunks, document.embedding_model = chunks, model
    document.content_sha256 = hashlib.sha256(body.content.encode()).hexdigest()
    document.version += 1
    document.updated_at = get_datetime_utc()
    session.add(document)
    session.commit()
    session.refresh(document)
    return document


@router.delete("/documents/{document_id}", response_model=Message)
def delete_document(
    session: SessionDep,
    current_user: CurrentUser,
    document_id: uuid.UUID,
    plant_id: uuid.UUID,
    expected_version: int = Query(ge=1),
) -> Message:
    require_plant_access(
        session=session, user=current_user, plant_id=plant_id, write=True
    )
    document = session.exec(
        select(KnowledgeDocument)
        .where(
            KnowledgeDocument.id == document_id, KnowledgeDocument.plant_id == plant_id
        )
        .with_for_update()
    ).first()
    if document is None:
        raise HTTPException(404, "文档不存在或不在当前授权范围")
    if document.version != expected_version:
        raise HTTPException(409, "文档已变化，请刷新后再删除")
    session.delete(document)
    session.commit()
    return Message(
        message="文档及全部分块、向量已删除，不再用于新回答；已显示的历史回答不会自动消失"
    )


@router.get("/search", response_model=list[Evidence])
def search_documents(
    session: SessionDep,
    current_user: CurrentUser,
    plant_id: uuid.UUID,
    query: str = Query(min_length=2, max_length=2000),
) -> list[Evidence]:
    # Local-only diagnostic baseline. No external disclosure or billing on GET.
    results, _ = retrieve(
        session=session, user=current_user, plant_id=plant_id, query=query
    )
    return results


@router.get("/runs", response_model=list[AssistantRun])
def read_runs(
    session: SessionDep, current_user: CurrentUser, plant_id: uuid.UUID
) -> list[AssistantRun]:
    require_plant_access(session=session, user=current_user, plant_id=plant_id)
    return list(
        session.exec(
            select(AssistantRun)
            .where(
                AssistantRun.user_id == current_user.id,
                AssistantRun.plant_id == plant_id,
            )
            .order_by(col(AssistantRun.created_at).desc())
            .limit(30)
        ).all()
    )
