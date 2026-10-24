"""Incident: one problem in one watched app, made of the alerts that belong to it.

Alerts of the same rule (the same group) for the same app share an incident while they keep
coming within the grouping window; an alert still firing in it always stays with it. The incident
resolves once every one of its alerts has.
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


class NotThisIncident(ValueError):
    """The alert belongs to another group."""


class UnknownAlert(KeyError):
    """The incident holds no alert with that fingerprint."""


class FeedbackNotAllowed(Exception):
    """Feedback is about a diagnosis; there is none yet."""


def group_key_of(alert: Alert) -> str:
    """The rule that fired: its alertname, or the title when a source sends none."""
    return (alert.labels.get("alertname") or "").strip() or alert.title


def _aware(moment: datetime) -> datetime:
    # Windows and ordering compare times; a time without a zone can't be compared safely.
    if moment.tzinfo is None:
        raise ValueError("incident times need a time zone")
    return moment


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
    # Channels told about this incident, or being told right now; a send that fails is released.
    notified: set[str] = field(default_factory=set)
    diagnosed: bool = False
    feedback: Vote | None = None

    @classmethod
    def open(cls, alert: Alert, app: str, now: datetime) -> Incident:
        incident = cls(uuid.uuid4().hex, app, group_key_of(alert), _aware(now), now)
        incident.attach(alert, now)
        return incident

    @property
    def severity(self) -> Severity:
        return max(record.severity for record in self.alerts.values())

    def firing(self, fingerprint: str) -> bool:
        record = self.alerts.get(fingerprint)
        return record is not None and record.firing

    def accepts(self, alert: Alert, now: datetime, window: timedelta) -> bool:
        """Same group, not resolved, and either the alert is still firing here or it arrives
        within the window of the latest alert."""
        return (
            self.status is not IncidentStatus.RESOLVED
            and group_key_of(alert) == self.group_key
            and (self.firing(alert.fingerprint.value) or now - self.last_alert_at <= window)
        )

    def attach(self, alert: Alert, now: datetime) -> Severity | None:
        """Adds or refreshes the alert; returns the severity the incident had before."""
        _aware(now)
        if self.status is IncidentStatus.RESOLVED:
            raise StatusError(f"incident {self.id} is resolved")
        if group_key_of(alert) != self.group_key:
            raise NotThisIncident(f"incident {self.id} is for {self.group_key!r}")
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

    def resolve(self, fingerprint: str, now: datetime) -> bool:
        """Marks one alert resolved; returns whether that resolved the whole incident."""
        _aware(now)
        if fingerprint not in self.alerts:
            raise UnknownAlert(fingerprint)
        if self.status is IncidentStatus.RESOLVED:
            return False
        record = self.alerts[fingerprint]
        record.firing, record.last_seen = False, now
        if any(r.firing for r in self.alerts.values()):
            return False
        self.advance_to(IncidentStatus.RESOLVED)
        return True

    def mark_notified(self, channels: set[str]) -> None:
        self.notified |= channels

    def unmark_notified(self, channels: set[str]) -> None:
        self.notified -= channels

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
