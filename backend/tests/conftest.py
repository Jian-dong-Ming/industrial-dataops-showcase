import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete

from app.core.config import settings
from app.core.db import engine, init_db
from app.main import app
from app.models import (
    AcquisitionNode,
    AcquisitionTask,
    AssistantRun,
    DataQualityIssue,
    Device,
    ImportBatch,
    ImportFieldMapping,
    ImportJob,
    KnowledgeDocument,
    Plant,
    ProductionLine,
    RetentionPreview,
    Tag,
    TagSample,
    User,
    UserPlantAccess,
)
from tests.utils.user import authentication_token_from_email
from tests.utils.utils import get_superuser_token_headers

database_name = settings.DATABASE_URL.path.lstrip("/")
if not database_name.endswith("_test") or database_name in {"app", "postgres"}:
    raise RuntimeError(
        "Refusing to run destructive tests against a non-test database. "
        "Use backend/scripts/tests-start.sh or set DATABASE_URL to a database "
        "whose name ends with '_test'."
    )

# Local .env.ai may contain real credentials. Ordinary tests must never bill or
# send fixture data to a real provider; explicit live acceptance opts in separately.
if os.environ.get("AI_LIVE_ACCEPTANCE") != "1":
    settings.DEEPSEEK_API_KEY = "test-only"
    settings.AI_EMBEDDING_URL = ""
    settings.AI_EMBEDDING_MODEL = ""
    settings.AI_EMBEDDING_API_KEY = "test-only"


@pytest.fixture(scope="session", autouse=True)
def db() -> Generator[Session]:
    with Session(engine) as session:
        init_db(session)
        yield session
        for model in (
            RetentionPreview,
            AssistantRun,
            KnowledgeDocument,
            TagSample,
            DataQualityIssue,
            ImportFieldMapping,
            ImportJob,
            ImportBatch,
            AcquisitionNode,
            AcquisitionTask,
            Tag,
            Device,
            ProductionLine,
            UserPlantAccess,
            Plant,
        ):
            session.execute(delete(model))
        statement = delete(User)
        session.execute(statement)
        session.commit()


@pytest.fixture(scope="module")
def client() -> Generator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def superuser_token_headers(client: TestClient) -> dict[str, str]:
    return get_superuser_token_headers(client)


@pytest.fixture(scope="module")
def normal_user_token_headers(client: TestClient, db: Session) -> dict[str, str]:
    return authentication_token_from_email(
        client=client, email=settings.EMAIL_TEST_USER, db=db
    )
