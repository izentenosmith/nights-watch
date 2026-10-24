"""Generic JSON `{title, severity, labels, started_at}` → Alert, for sources without a standard."""

from typing import Annotated

from pydantic import Field, StringConstraints

from nights_watch.triage.domain.alert import Alert, Fingerprint, Severity, Status
from nights_watch.triage.infrastructure.ingest import Payload, UtcDatetime, now


class GenericAlert(Payload):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    severity: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    started_at: UtcDatetime | None = None

    def to_alert(self) -> Alert:
        return Alert(
            title=self.title,
            severity=Severity.parse((self.severity or "").strip() or self.labels.get("severity")),
            labels=self.labels,
            status=Status.FIRING,
            starts_at=self.started_at or now(),
            fingerprint=Fingerprint.of(self.title, self.labels),
        )
