"""Alertmanager v4 webhook → Alerts. Grafana's webhook is a superset of it: same parser.

Unknown fields are ignored on purpose, so Grafana's extras (values, silenceURL, …) pass.
"""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from nights_watch.triage.domain.alert import Alert, Fingerprint, Severity, Status


class _Alert(BaseModel):
    status: Literal["firing", "resolved"] = "firing"
    labels: dict[str, str]
    annotations: dict[str, str] = {}
    starts_at: datetime | None = Field(None, alias="startsAt")
    generator_url: str | None = Field(None, alias="generatorURL")
    fingerprint: str | None = None

    def to_alert(self) -> Alert:
        title = (
            self.annotations.get("summary") or self.labels.get("alertname") or "unnamed alert"
        )
        return Alert(
            title=title,
            severity=Severity.parse(self.labels.get("severity")),
            labels=self.labels,
            status=Status(self.status),
            starts_at=self.starts_at or datetime.now(UTC),
            fingerprint=Fingerprint(self.fingerprint)
            if self.fingerprint
            else Fingerprint.of(title, self.labels),
            generator_url=self.generator_url or None,
        )


class AlertmanagerPayload(BaseModel):
    alerts: list[_Alert]

    def to_alerts(self) -> list[Alert]:
        return [a.to_alert() for a in self.alerts]
