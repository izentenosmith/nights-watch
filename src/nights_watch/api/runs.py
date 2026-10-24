"""The run report: one page per incident, linked from every message, and its 👍/👎."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader

from nights_watch.triage.application.record_feedback import UnknownRun
from nights_watch.triage.domain.incident import FeedbackNotAllowed, Vote

# Autoescape on: every value on the page comes from alert payloads, which we don't control.
templates = Jinja2Templates(
    env=Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=True)
)
templates.env.filters["utc"] = lambda moment: (
    f"{moment.astimezone(UTC):%Y-%m-%d %H:%M:%S} UTC" if isinstance(moment, datetime) else ""
)

router = APIRouter(prefix="/runs")


@router.get("/{run_id}", response_class=HTMLResponse)
async def run(run_id: str, request: Request):
    incident = request.app.state.incidents.get(run_id)
    if incident is None:
        raise HTTPException(404, "no such run")
    return templates.TemplateResponse(request, "run.html", {"incident": incident})


@router.post("/{run_id}/feedback")
async def feedback(run_id: str, vote: Annotated[Vote, Form()], request: Request):
    try:
        await request.app.state.record_feedback(run_id, vote)
    except UnknownRun:
        raise HTTPException(404, "no such run") from None
    except FeedbackNotAllowed:
        raise HTTPException(409, "feedback opens once the run has a diagnosis") from None
    return RedirectResponse(f"/runs/{run_id}", status_code=303)
