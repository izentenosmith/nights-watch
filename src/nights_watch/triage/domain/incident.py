"""Incident: one problem in one watched app, made of the alerts that belong to it.

Alerts of the same rule (the same group) for the same app share an incident while they keep
coming within the grouping window; the incident resolves once every one of them has.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import IntEnum, StrEnum

from nights_watch.triage.domain.alert import Alert, Severity


class IncidentStatus(IntEnum):
    OPEN = 1
    INVESTIGATING = 2
    DIAGNOSED = 3
    RESOLVED = 4

    def __str__(self) -> str:
        return self.name.lower()


class Vote(StrEnum):
    UP = "up"
    DOWN = "down"


class StatusError(Exception):
    """A change the incident's status doesn't allow: statuses only move forward."""


class FeedbackNotAllowed(Exception):
    """Feedback is about a diagnosis; there is none yet."""


def group_key_of(alert: Alert) -> str:
    """The rule that fired: its alertname, or the title when a source sends none."""
    return (alert.labels.get("alertname") or "").strip() or alert.title


@dataclass
class AlertRecord:
    fingerprint: str
    title: str
    severity: Severity
    firing: bool
    first_seen: datetime
    last_seen: datetime


@dataclass
class Incident:
    id: str
    app: str
    group_key: str
    opened_at: datetime
    last_alert_at: datetime
    status: IncidentStatus = IncidentStatus.OPEN
    alerts: dict[str, AlertRecord] = field(default_factory=dict)
    diagnosed: bool = False
    feedback: Vote | None = None

    @classmethod
    def open(cls, alert: Alert, app: str, now: datetime) -> Incident:
        incident = cls(uuid.uuid4().hex, app, group_key_of(alert), now, now)
        incident.attach(alert, now)
        return incident

    @property
    def severity(self) -> Severity:
        return max(record.severity for record in self.alerts.values())

    def accepts(self, alert: Alert, now: datetime, window: timedelta) -> bool:
        """Same group, not resolved, and within the window of the latest alert."""
        return (
            self.status is not IncidentStatus.RESOLVED
            and group_key_of(alert) == self.group_key
            and now - self.last_alert_at <= window
        )

    def attach(self, alert: Alert, now: datetime) -> Severity | None:
        """Adds or refreshes the alert; returns the severity the incident had before."""
        if self.status is IncidentStatus.RESOLVED:
            raise StatusError(f"incident {self.id} is resolved")
        previous = self.severity if self.alerts else None
        fingerprint = alert.fingerprint.value
        record = self.alerts.get(fingerprint)
        if record is None:
            self.alerts[fingerprint] = AlertRecord(
                fingerprint, alert.title, alert.severity, True, now, now
            )
        else:
            record.title, record.severity, record.firing, record.last_seen = (
                alert.title, alert.severity, True, now
            )
        self.last_alert_at = now
        return previous

    def holds(self, fingerprint: str) -> bool:
        return fingerprint in self.alerts

    def resolve(self, fingerprint: str, now: datetime) -> bool:
        """Marks one alert resolved; returns whether that resolved the whole incident."""
        record = self.alerts[fingerprint]
        record.firing, record.last_seen = False, now
        if any(r.firing for r in self.alerts.values()):
            return False
        self.advance_to(IncidentStatus.RESOLVED)
        return True

    def advance_to(self, status: IncidentStatus) -> None:
        if status < self.status:
            raise StatusError(f"incident {self.id} can't go from {self.status} back to {status}")
        if status is IncidentStatus.DIAGNOSED:
            self.diagnosed = True
        self.status = status

    def record_feedback(self, vote: Vote) -> None:
        if not self.diagnosed:
            raise FeedbackNotAllowed(f"incident {self.id} has no diagnosis yet")
        self.feedback = vote
