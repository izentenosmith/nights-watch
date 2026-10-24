"""Operational endpoints: liveness, what is watched, metrics."""

from fastapi import APIRouter, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@router.get("/apps")
async def apps(request: Request) -> list[dict]:
    """Each watched app, its matchers and which channels are enabled. Never a destination."""
    policy = request.app.state.policy
    return [
        {
            "name": app.name,
            "match": dict(app.matcher.labels),
            "channels": {
                str(channel): {"enabled": view.enabled, "min_severity": str(view.min_severity)}
                for channel, view in policy.view(app.name).items()
            },
        }
        for app in request.app.state.apps
    ]


@router.get("/metrics")
async def metrics(request: Request) -> Response:
    return Response(generate_latest(request.app.state.registry), media_type=CONTENT_TYPE_LATEST)
