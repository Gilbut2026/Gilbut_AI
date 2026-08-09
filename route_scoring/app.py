"""FastAPI entrypoint for route accessibility scoring.

Backend sends the scoring request using the shared contract. The AI server adds
weather information internally and delegates the actual policy logic to
``score_routes``.
"""

from copy import deepcopy
from typing import Any

from dotenv import load_dotenv
from fastapi import Body, FastAPI
from fastapi.responses import JSONResponse

from scoring import score_routes
from scoring.weather_penalty import get_weather_environment


load_dotenv()

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

    Weather is owned by the AI server. If Backend happens to send an
    ``environment`` field, it is overwritten with the latest weather lookup so
    there is only one source of truth for weather scoring.
    """
    if not isinstance(request, dict):
        return score_routes(request)

    scoring_request = deepcopy(request)
    scoring_request["environment"] = get_weather_environment()

    try:
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
