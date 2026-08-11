"""FastAPI entrypoint for Gilbut route accessibility scoring.

Backend sends user context and route candidates. The API layer queries current
weather, creates the internal ``environment`` field, and delegates scoring to
``route_scoring``.
"""

from copy import deepcopy
import logging
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import Body, FastAPI
from fastapi.responses import JSONResponse

from api.slope_enrichment import (
    enrich_routes_with_slopes,
    mark_slope_failed,
)
from route_scoring.scoring import score_routes
from route_scoring.scoring.weather_penalty import get_weather_environment


load_dotenv(Path(__file__).with_name(".env"))

LOGGER = logging.getLogger(__name__)

app = FastAPI(
    title="Gilbut Route Scoring API",
    version="1.0.0",
)


@app.get("/health")
def health():
    """Simple process health check."""
    return {"status": "ok"}


@app.post("/routes/score")
def score_route_candidates(request: Any = Body(...)):
    """Score Backend route candidates using the shared request/response contract.

    Backend provides userContext and candidates, including walkSegments when
    available. FastAPI adds AI-owned weather information internally before
    delegating the policy logic to ``route_scoring.scoring.score_routes``.
    """
    if not isinstance(request, dict):
        return score_routes(request)

    try:
        scoring_request = deepcopy(request)
        scoring_request["environment"] = get_weather_environment()
        try:
            enrich_routes_with_slopes(scoring_request)
        except Exception:
            # 외부 고도 보강은 추천 API 전체를 실패시키지 않는다.
            LOGGER.error(
                "unexpected slope enrichment failure requestId=%s",
                request.get("requestId"),
            )
            mark_slope_failed(scoring_request)
        return score_routes(scoring_request)
    except Exception:
        return JSONResponse(
            status_code=500,
            content={
                "requestId": request.get("requestId"),
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "route scoring failed",
                    "retryable": True,
                },
            },
        )
