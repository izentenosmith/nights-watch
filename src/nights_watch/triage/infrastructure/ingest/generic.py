"""Generic JSON `{title, severity, labels, started_at}` → Alert, for sources without a standard."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, StringConstraints

from nights_watch.triage.domain.alert import Alert, Fingerprint, Severity, Status
from nights_watch.triage.infrastructure.ingest import utc


class GenericAlert(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    severity: str | None = None
    labels: dict[str, str] = {}
    started_at: datetime | None = None

    def to_alert(self) -> Alert:
        return Alert(
            title=self.title,
            severity=Severity.parse(self.severity or self.labels.get("severity")),
            labels=self.labels,
            status=Status.FIRING,
            starts_at=utc(self.started_at),
            fingerprint=Fingerprint.of(self.title, self.labels),
        )
