"""FastAPI entrypoint for Gilbut route accessibility scoring.

Backend sends the shared scoring request contract. The API layer enriches the
request with current weather and delegates scoring to ``route_scoring``.
"""

from copy import deepcopy
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import Body, FastAPI
from fastapi.responses import JSONResponse

from route_scoring.scoring import score_routes
from route_scoring.scoring.weather_penalty import get_weather_environment


load_dotenv(Path(__file__).with_name(".env"))

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

    FastAPI does not recreate candidates or walkSegments. It forwards the
    Backend payload as-is, adds AI-owned weather information, and delegates the
    policy logic to ``route_scoring.scoring.score_routes``.
    """
    if not isinstance(request, dict):
        return score_routes(request)

    try:
        scoring_request = deepcopy(request)
        scoring_request["environment"] = get_weather_environment()
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
