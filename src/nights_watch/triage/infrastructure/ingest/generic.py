"""Generic JSON `{title, severity, labels, started_at}` → Alert, for sources without a standard."""

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from nights_watch.triage.domain.alert import Alert, Fingerprint, Severity, Status


class GenericAlert(BaseModel):
    title: str = Field(min_length=1)
    severity: str | None = None
    labels: dict[str, str] = {}
    started_at: datetime | None = None

    def to_alert(self) -> Alert:
        return Alert(
            title=self.title,
            severity=Severity.parse(self.severity),
            labels=self.labels,
            status=Status.FIRING,
            starts_at=self.started_at or datetime.now(UTC),
            fingerprint=Fingerprint.of(self.title, self.labels),
        )
