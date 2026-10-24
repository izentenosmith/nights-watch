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
    ):
        self._router = router
        self._policy = policy
        self._notifiers = notifiers

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
        delivered = await asyncio.gather(*(self._send(d, message) for d in deliveries))
        return Outcome(
            alert,
            app,
            sent=tuple(d.channel for d, ok in zip(deliveries, delivered) if ok),
            failed=tuple(d.channel for d, ok in zip(deliveries, delivered) if not ok),
        )

    async def _send(self, delivery: Delivery, message: Message) -> bool:
        """One channel failing never stops the others; the failure is logged without secrets."""
        try:
            await self._notifiers[delivery.channel].send(message, delivery.destination)
            return True
        except Exception as e:  # noqa: BLE001 — an adapter bug mustn't take the others down
            # only a DeliveryError's text is known to be free of URLs and tokens
            reason = str(e) if isinstance(e, DeliveryError) else type(e).__name__
            log.warning("%s: message for %s failed: %s", delivery.channel, message.app, reason)
            return False
