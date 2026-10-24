"""route → open or attach to an incident → notify. Each channel the incident's severity reaches
hears about it once: when it opens, or when its severity rises to the channel's floor. A channel
whose message failed is tried again on the incident's next alert. No agent yet (M1b)."""

import asyncio
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from nights_watch.catalog.domain import AlertRouter
from nights_watch.notification.domain import (
    Channel,
    Delivery,
    DeliveryError,
    Message,
    NotificationPolicy,
    Notifier,
)
from nights_watch.triage.domain.alert import Alert, Status
from nights_watch.triage.domain.incident import Incident, group_key_of
from nights_watch.triage.domain.ports import IncidentRepository

log = logging.getLogger(__name__)


class Event(StrEnum):
    UNMATCHED = "unmatched"
    IGNORED = "ignored"  # a resolve for an alert no unresolved incident holds
    OPENED = "opened"
    ATTACHED = "attached"
    ESCALATED = "escalated"
    ALERT_RESOLVED = "alert resolved"
    RESOLVED = "resolved"
    FAILED = "failed"  # couldn't be stored; logged, and the request answers 500 so it's re-sent


@dataclass(frozen=True)
class Outcome:
    alert: Alert
    event: Event
    app: str | None = None
    incident_id: str | None = None
    sent: tuple[Channel, ...] = ()
    failed: tuple[Channel, ...] = ()

    @property
    def unmatched(self) -> bool:
        return self.event is Event.UNMATCHED


class ReceiveAlert:
    def __init__(
        self,
        router: AlertRouter,
        policy: NotificationPolicy,
        notifiers: Mapping[Channel, Notifier],
        *,
        incidents: IncidentRepository,
        lock: asyncio.Lock,
        window: timedelta,
        clock: Callable[[], datetime],
        run_url: Callable[[str], str],
        send_timeout: float = 10.0,
    ):
        self._router = router
        self._policy = policy
        self._notifiers = notifiers
        self._incidents = incidents
        # One writer at a time: find, attach-or-open and save must not interleave, or two
        # alerts of one group arriving together would open two incidents.
        self._lock = lock
        self._window = window
        self._clock = clock
        self._run_url = run_url
        self._send_timeout = send_timeout

    async def __call__(self, alerts: Iterable[Alert]) -> list[Outcome]:
        """Each alert is handled on its own, and one failing doesn't stop the others."""
        alerts = list(alerts)
        results = await asyncio.gather(*(self._receive(a) for a in alerts), return_exceptions=True)
        return [self._failed(a, r) if isinstance(r, Exception) else r
                for a, r in zip(alerts, results)]

    def _failed(self, alert: Alert, error: Exception) -> Outcome:
        log.error("alert %r couldn't be handled: %s", alert.title, type(error).__name__)
        return Outcome(alert, Event.FAILED)

    async def _receive(self, alert: Alert) -> Outcome:
        app = self._route(alert)
        if app is None:
            unrouted = Event.IGNORED if alert.status is Status.RESOLVED else Event.UNMATCHED
            return Outcome(alert, unrouted)
        async with self._lock:
            if alert.status is Status.RESOLVED:
                return self._resolve(alert, app)
            incident, event, deliveries = self._open_or_attach(alert, app)
        log.info("%s incident %s for %s: %r (%s)", event, incident.id, app, alert.title,
                 incident.severity)
        if event is Event.OPENED and not deliveries:
            self._log_undelivered(alert, app)
        message = Message(
            incident.severity, app, alert.title, alert.starts_at, alert.generator_url,
            run_url=self._run_url(incident.id), escalated=event is Event.ESCALATED,
        )
        delivered = await asyncio.gather(*(self._send(d, message) for d in deliveries))
        failed = tuple(d.channel for d, ok in zip(deliveries, delivered) if not ok)
        if failed:
            await self._release(incident.id, failed)
        return Outcome(
            alert, event, app, incident.id,
            sent=tuple(d.channel for d, ok in zip(deliveries, delivered) if ok),
            failed=failed,
        )

    async def _release(self, incident_id: str, channels: tuple[Channel, ...]) -> None:
        """A channel whose message failed hasn't been told: the incident's next alert retries."""
        async with self._lock:
            incident = self._incidents.get(incident_id)
            incident.unmark_notified({str(c) for c in channels})
            self._incidents.save(incident)

    def _route(self, alert: Alert) -> str | None:
        candidates = self._router.candidates(alert.labels)
        if not candidates:
            if alert.status is Status.RESOLVED:
                log.info("resolved alert %r matches no watched app, ignored", alert.title)
            else:
                log.info("alert %r matches no watched app, dropped; labels=%s",
                         alert.title, alert.labels)
            return None
        if len(candidates) > 1:
            log.warning(
                "alert %r matches %s; routed to %s (most matchers)",
                alert.title, [c.name for c in candidates], candidates[0].name,
            )
        return candidates[0].name

    def _open_or_attach(self, alert: Alert, app: str) -> tuple[Incident, Event, list[Delivery]]:
        now = self._clock()
        incident = (self._incidents.find_firing(app, alert.fingerprint.value)
                    or self._incidents.find_open(app, group_key_of(alert)))
        if incident is not None and incident.accepts(alert, now, self._window):
            previous = incident.attach(alert, now)
            event = Event.ESCALATED if incident.severity > previous else Event.ATTACHED
        else:
            incident = Incident.open(alert, app, now)
            event = Event.OPENED
        # The channels this severity reaches that haven't been told yet. They're marked told now,
        # under the lock, so another alert of this incident in the same batch doesn't tell them too.
        deliveries = [d for d in self._policy.deliveries(app, incident.severity)
                      if str(d.channel) not in incident.notified]
        incident.mark_notified({str(d.channel) for d in deliveries})
        self._incidents.save(incident)
        return incident, event, deliveries

    def _resolve(self, alert: Alert, app: str) -> Outcome:
        fingerprint = alert.fingerprint.value
        incident = self._incidents.find_firing(app, fingerprint)
        if incident is None:
            log.info("resolved alert %r for %s: no open incident holds it, ignored",
                     alert.title, app)
            return Outcome(alert, Event.IGNORED, app)
        resolved = incident.resolve(fingerprint, self._clock())
        self._incidents.save(incident)
        event = Event.RESOLVED if resolved else Event.ALERT_RESOLVED
        log.info("%s on incident %s for %s: %r", event, incident.id, app, alert.title)
        return Outcome(alert, event, app, incident.id)

    def _log_undelivered(self, alert: Alert, app: str) -> None:
        if not any(view.enabled for view in self._policy.view(app).values()):
            log.warning("%s alert %r for %s dropped: no channel is enabled for %s (see GET /apps)",
                        alert.severity, alert.title, app, app)
        else:
            log.info("%s alert %r for %s: no enabled channel has a floor that low",
                     alert.severity, alert.title, app)

    async def _send(self, delivery: Delivery, message: Message) -> bool:
        """One channel failing never stops the others; the failure is logged without secrets."""
        try:
            async with asyncio.timeout(self._send_timeout):  # in total, whatever the client does
                await self._notifiers[delivery.channel].send(message, delivery.destination)
            return True
        except Exception as e:  # noqa: BLE001 — an adapter bug mustn't take the others down
            log.warning(
                "%s: message for %s failed: %s", delivery.channel, message.app, self._reason(e)
            )
            return False

    def _reason(self, error: Exception) -> str:
        """Safe to log: only a DeliveryError's text is known to hold no URL or token."""
        if isinstance(error, TimeoutError):
            return f"timed out after {self._send_timeout:g}s"
        if isinstance(error, DeliveryError):
            return str(error)
        return type(error).__name__
