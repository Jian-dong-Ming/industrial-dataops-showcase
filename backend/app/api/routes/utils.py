from fastapi import APIRouter, Depends, HTTPException, status
from pydantic.networks import EmailStr
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import func, select

from app.api.deps import SessionDep, get_current_active_superuser
from app.core.config import settings
from app.models import Message
from app.utils import generate_test_email, send_email

router = APIRouter(prefix="/utils", tags=["utils"])


@router.get("/browser-test-safety/")
def browser_test_safety(session: SessionDep) -> dict[str, bool]:
    """Expose only an opt-in flag, never database names or credentials.

    Verify the connected database rather than trusting a browser environment flag.
    This is a guard against accidental test writes, not an authorization mechanism.
    """
    if not settings.BROWSER_TEST_MODE:
        return {"browser_tests_allowed": False}
    database = session.exec(select(func.current_database())).one()
    return {"browser_tests_allowed": str(database).endswith("_test")}


@router.post(
    "/test-email/",
    dependencies=[Depends(get_current_active_superuser)],
    status_code=201,
)
def test_email(email_to: EmailStr) -> Message:
    """
    Test emails.
    """
    email_data = generate_test_email(email_to=email_to)
    send_email(
        email_to=email_to,
        subject=email_data.subject,
        html_content=email_data.html_content,
    )
    return Message(message="Test email sent")


@router.get("/health-check/")
def health_check(session: SessionDep) -> bool:
    try:
        session.exec(select(1)).one()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable",
        ) from exc
    return True
