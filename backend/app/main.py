"""FastAPI application: JSON API under /api, plus the built React app when present."""
from __future__ import annotations

from datetime import date
from functools import lru_cache

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import analysis
from .config import DEFAULT_CUTOFF, DECISION_END, DECISION_START, LOOKBACK_DAYS, STATIC_DIR
from .data import DataNotFound, get_dataset

app = FastAPI(title="Wind forecast desk API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])


def _dataset():
    try:
        return get_dataset()
    except DataNotFound as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _cutoff(value: str):
    try:
        return analysis.parse_cutoff(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="cutoff must be HH:MM (UTC), e.g. 09:00") from exc


@lru_cache(maxsize=64)
def _accuracy(cutoff: str) -> dict:
    return analysis.accuracy_report(_dataset(), _cutoff(cutoff))


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/meta")
def meta():
    ds = _dataset()
    return {
        "source": ds.meta.get("source", "unknown"),
        "fetched_at": ds.meta.get("fetched_at"),
        "decision_dates": [d.isoformat() for d in analysis.decision_dates()],
        "decision_start": DECISION_START.isoformat(),
        "decision_end": DECISION_END.isoformat(),
        "default_cutoff": DEFAULT_CUTOFF,
        "lookback_days": LOOKBACK_DAYS,
        "actual_slots": len(ds.actuals),
        "forecast_targets": len(ds.forecasts),
    }


@app.get("/api/decision")
def decision(decision_date: date = Query(..., description="Day the purchase decision is made"),
             cutoff: str = Query(DEFAULT_CUTOFF, description="HH:MM UTC on decision day")):
    """Latest forecast available at the cutoff for the next day's delivery, with uncertainty band."""
    if not (DECISION_START <= decision_date <= DECISION_END):
        raise HTTPException(422, f"decision_date must be within {DECISION_START} to {DECISION_END}")
    result = analysis.decision_view(_dataset(), decision_date, _cutoff(cutoff))
    if result is None:
        raise HTTPException(404, "No forecast was published before the cutoff for that delivery day")
    return result


@app.get("/api/accuracy")
def accuracy(cutoff: str = Query(DEFAULT_CUTOFF, description="HH:MM UTC on decision day")):
    """Quantified error of the forecasts the desk had at each January 2026 decision."""
    return _accuracy(cutoff)


# ---- Serve the built React app (production image / Heroku) ----
if (STATIC_DIR / "index.html").exists():
    if (STATIC_DIR / "assets").exists():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        candidate = (STATIC_DIR / path).resolve()
        if path and candidate.is_file() and STATIC_DIR.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
