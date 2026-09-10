import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Question(StrictModel):
    plant_id: uuid.UUID
    question: str = Field(min_length=2, max_length=2000)
    allow_external_processing: bool = False

    @field_validator("question")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if len(value.strip()) < 2:
            raise ValueError("请输入具体问题")
        return value.strip()


class DocumentInput(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=10, max_length=30000)
    expected_version: int | None = Field(default=None, ge=1)
    allow_external_processing: bool = False


class DocumentPublic(BaseModel):
    id: uuid.UUID
    plant_id: uuid.UUID
    title: str
    version: int
    content_sha256: str
    embedding_model: str | None
    updated_at: datetime
    content: str


class Evidence(BaseModel):
    id: str
    kind: Literal["document", "tool", "capability"]
    title: str
    data: dict[str, Any]


class GeneratedAnswer(StrictModel):
    status: Literal["answered", "no_answer", "clarification"]
    answer: str = Field(min_length=1, max_length=6000)
    citation_ids: list[str] = Field(default_factory=list, max_length=20)


class Answer(GeneratedAnswer):
    run_id: uuid.UUID
    evidence: list[Evidence]
    model: str
    retrieval_mode: str
    prompt_tokens: int
    completion_tokens: int
    duration_ms: int
    generated_at: datetime
