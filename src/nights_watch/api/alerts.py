"""Alert webhooks. Entry points only: parse, hand over to the use case, count what was dropped."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from nights_watch.triage.domain.alert import Alert
from nights_watch.triage.infrastructure.ingest.alertmanager import AlertmanagerPayload
from nights_watch.triage.infrastructure.ingest.generic import GenericAlert

router = APIRouter(prefix="/alerts")


@router.post("/alertmanager", status_code=202)
async def alertmanager(payload: AlertmanagerPayload, request: Request) -> dict:
    """Alertmanager v4 webhook; Grafana alerting webhooks post here too."""
    return await _receive(request, payload.to_alerts())


@router.post("/generic", status_code=202)
async def generic(payload: GenericAlert, request: Request) -> dict:
    return await _receive(request, [payload.to_alert()])


async def _receive(request: Request, alerts: list[Alert]):
    outcomes = await request.app.state.receive_alert(alerts)
    unmatched = sum(o.unmatched for o in outcomes)
    request.app.state.unmatched_alerts.inc(unmatched)
    body = {"received": len(outcomes), "unmatched": unmatched}
    if failed := sum(o.event == "failed" for o in outcomes):
        # Ask the sender to try again; what already went through attaches without re-paging.
        return JSONResponse(body | {"failed": failed}, status_code=500)
    return body
