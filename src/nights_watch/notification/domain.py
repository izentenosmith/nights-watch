"""Which channels get a message about an app's alert, and what the message says."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from nights_watch.triage.domain.alert import Severity


class Channel(StrEnum):
    SLACK = "slack"
    TEAMS = "teams"
    WHATSAPP = "whatsapp"


DEFAULT_FLOORS = {
    Channel.SLACK: Severity.WARNING,
    Channel.TEAMS: Severity.WARNING,
    Channel.WHATSAPP: Severity.CRITICAL,
}


@dataclass(frozen=True)
class ChannelSettings:
    """A channel's global settings. `has_credentials` covers what isn't the destination
    (WhatsApp's token, phone id and template); Slack and Teams need only their webhook."""

    destination: str | None
    min_severity: Severity
    has_credentials: bool = True


@dataclass(frozen=True)
class ChannelOverride:
    """An app's own settings for a channel; whatever is None falls back to the global one."""

    destination: str | None = None
    min_severity: Severity | None = None


@dataclass(frozen=True)
class ChannelView:
    enabled: bool
    min_severity: Severity


@dataclass(frozen=True)
class Delivery:
    channel: Channel
    destination: str


@dataclass(frozen=True)
class Message:
    severity: Severity
    app: str
    title: str
    starts_at: datetime
    generator_url: str | None = None
    run_url: str | None = None  # the incident's report page
    escalated: bool = False  # the incident's severity just rose to `severity`


class DeliveryError(Exception):
    """A channel refused or never got the message. The text is safe to log: no URL, no token."""


class Notifier(Protocol):
    async def send(self, message: Message, destination: str) -> None:
        """Raises DeliveryError when the message didn't go out."""
        ...


class NotificationPolicy:
    def __init__(
        self,
        channels: Mapping[Channel, ChannelSettings],
        overrides: Mapping[str, Mapping[Channel, ChannelOverride]],
    ):
        self._channels = channels
        self._overrides = overrides

    def deliveries(self, app: str, severity: Severity) -> list[Delivery]:
        """A channel gets the message when it's enabled for the app and the severity meets its floor."""
        return [
            Delivery(channel, destination)
            for channel in Channel
            if (destination := self._destination(app, channel))
            and severity >= self._floor(app, channel)
        ]

    def newly_reached(self, app: str, old: Severity, new: Severity) -> list[Delivery]:
        """When an incident's severity rises: the channels the new severity reaches and the old
        one didn't. Channels already told aren't told again."""
        already = {d.channel for d in self.deliveries(app, old)}
        return [d for d in self.deliveries(app, new) if d.channel not in already]

    def view(self, app: str) -> dict[Channel, ChannelView]:
        return {
            channel: ChannelView(self._destination(app, channel) is not None, self._floor(app, channel))
            for channel in Channel
        }

    def _destination(self, app: str, channel: Channel) -> str | None:
        """Where the message goes; None when the channel is disabled for this app."""
        settings = self._channels.get(channel)
        if settings is None or not settings.has_credentials:
            return None
        return self._override(app, channel).destination or settings.destination

    def _floor(self, app: str, channel: Channel) -> Severity:
        settings = self._channels.get(channel)
        global_floor = settings.min_severity if settings else DEFAULT_FLOORS[channel]
        return self._override(app, channel).min_severity or global_floor

    def _override(self, app: str, channel: Channel) -> ChannelOverride:
        return self._overrides.get(app, {}).get(channel, ChannelOverride())
