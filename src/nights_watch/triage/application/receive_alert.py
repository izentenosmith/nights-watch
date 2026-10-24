"""normalize → route → notify. In M0 the notification is a placeholder: no incident, no agent."""

import asyncio
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

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

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Outcome:
    alert: Alert
    app: str | None = None
    sent: tuple[Channel, ...] = ()
    failed: tuple[Channel, ...] = ()

    @property
    def unmatched(self) -> bool:
        return self.alert.status is Status.FIRING and self.app is None


class ReceiveAlert:
    def __init__(
        self,
        router: AlertRouter,
        policy: NotificationPolicy,
        notifiers: Mapping[Channel, Notifier],
        send_timeout: float = 10.0,
    ):
        self._router = router
        self._policy = policy
        self._notifiers = notifiers
        self._send_timeout = send_timeout

    async def __call__(self, alerts: Iterable[Alert]) -> list[Outcome]:
        """Each alert is handled on its own; all sends run at once."""
        return list(await asyncio.gather(*(self._receive(a) for a in alerts)))

    async def _receive(self, alert: Alert) -> Outcome:
        if alert.status is Status.RESOLVED:
            log.info("resolved alert %r: nothing to send in M0", alert.title)
            return Outcome(alert)

        candidates = self._router.candidates(alert.labels)
        if not candidates:
            log.info("alert %r matches no watched app, dropped; labels=%s", alert.title, alert.labels)
            return Outcome(alert)
        app = candidates[0].name
        if len(candidates) > 1:
            log.warning(
                "alert %r matches %s; routed to %s (most matchers)",
                alert.title, [c.name for c in candidates], app,
            )

        message = Message(alert.severity, app, alert.title, alert.starts_at, alert.generator_url)
        deliveries = self._policy.deliveries(app, alert.severity)
        if not deliveries:
            self._log_undelivered(alert, app)
        delivered = await asyncio.gather(*(self._send(d, message) for d in deliveries))
        return Outcome(
            alert,
            app,
            sent=tuple(d.channel for d, ok in zip(deliveries, delivered) if ok),
            failed=tuple(d.channel for d, ok in zip(deliveries, delivered) if not ok),
        )

    def _log_undelivered(self, alert: Alert, app: str) -> None:
        if not any(view.enabled for view in self._policy.view(app).values()):
            log.warning("%s alert %r for %s dropped: no channel is configured for %s",
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
