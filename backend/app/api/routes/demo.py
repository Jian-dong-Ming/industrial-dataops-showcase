from fastapi import APIRouter, Response

from app.api.deps import CurrentUser
from app.opcua.scenario import TeachingScenario, csv_content, teaching_scenario

router = APIRouter(prefix="/demo", tags=["demo"])


@router.get("/scenario", response_model=TeachingScenario)
def read_scenario(_current_user: CurrentUser) -> TeachingScenario:
    # Same bundled synthetic content for all authenticated users; no plant data.
    return teaching_scenario()


@router.get("/scenario.csv")
def download_scenario(_current_user: CurrentUser) -> Response:
    return Response(
        csv_content(teaching_scenario().frames),
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="synthetic-cooling-loop-v1.csv"'
        },
    )
