"""Reads the environment, wires adapters to ports, and runs the startup checks."""

import logging
import os
from collections.abc import Mapping
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from dotenv import dotenv_values
from fastapi import FastAPI
from prometheus_client import CollectorRegistry, Counter
from pydantic import BaseModel, ConfigDict, ValidationError

from nights_watch.api import alerts, health
from nights_watch.catalog.domain import AlertRouter
from nights_watch.catalog.yaml_catalog import AppFile, CatalogError, load_catalog
from nights_watch.catalog.yaml_catalog import Severity as SeverityName
from nights_watch.notification.channels.slack import SlackNotifier
from nights_watch.notification.channels.teams import TeamsNotifier
from nights_watch.notification.channels.whatsapp import WhatsAppNotifier
from nights_watch.notification.domain import (
    Channel,
    ChannelOverride,
    ChannelSettings,
    NotificationPolicy,
    Notifier,
)
from nights_watch.triage.application.receive_alert import ReceiveAlert
from nights_watch.triage.domain.alert import Severity

SEND_TIMEOUT_SECONDS = 10.0


class StartupError(Exception):
    pass


class Settings(BaseModel):
    """The global NW_* variables, without the prefix. Per-app variables are named by the catalog."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    slack_webhook_url: str | None = None
    slack_min_severity: SeverityName = "warning"
    teams_webhook_url: str | None = None
    teams_min_severity: SeverityName = "warning"
    whatsapp_token: str | None = None
    whatsapp_phone_id: str | None = None
    whatsapp_to: str | None = None
    whatsapp_template: str | None = None
    whatsapp_template_lang: str | None = None
    whatsapp_min_severity: SeverityName = "critical"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        return cls.model_validate(
            {k.removeprefix("NW_").lower(): v for k, v in env.items() if k.startswith("NW_")}
        )

    @property
    def whatsapp_ready(self) -> bool:
        """Everything WhatsApp needs besides the recipient."""
        return all(
            (self.whatsapp_token, self.whatsapp_phone_id, self.whatsapp_template,
             self.whatsapp_template_lang)
        )

    def channels(self) -> dict[Channel, ChannelSettings]:
        return {
            Channel.SLACK: ChannelSettings(
                self.slack_webhook_url, Severity.parse(self.slack_min_severity)
            ),
            Channel.TEAMS: ChannelSettings(
                self.teams_webhook_url, Severity.parse(self.teams_min_severity)
            ),
            Channel.WHATSAPP: ChannelSettings(
                self.whatsapp_to, Severity.parse(self.whatsapp_min_severity), self.whatsapp_ready
            ),
        }


def _notifiers(settings: Settings, client: httpx.AsyncClient) -> dict[Channel, Notifier]:
    notifiers: dict[Channel, Notifier] = {
        Channel.SLACK: SlackNotifier(client),
        Channel.TEAMS: TeamsNotifier(client),
    }
    if settings.whatsapp_ready:
        notifiers[Channel.WHATSAPP] = WhatsAppNotifier(
            client, settings.whatsapp_token, settings.whatsapp_phone_id,
            settings.whatsapp_template, settings.whatsapp_template_lang,
        )
    return notifiers


def _env_references(app: AppFile):
    """(channel, field, variable) for every *_env field the app's notify section sets."""
    for channel, settings in app.notify:
        for field, variable in settings or ():
            if field.endswith("_env") and variable:
                yield Channel(channel), field, variable


def _unset_references(catalog: Mapping[str, AppFile], env: Mapping[str, str]) -> list[str]:
    return [
        f"  {app.name}.yaml notify.{channel}.{field}: {variable}"
        for app in catalog.values()
        for channel, field, variable in _env_references(app)
        if variable not in env
    ]


def _overrides(app: AppFile, env: Mapping[str, str]) -> dict[Channel, ChannelOverride]:
    destinations = {channel: env[variable] for channel, _, variable in _env_references(app)}
    overrides = {}
    for channel in Channel:
        settings = getattr(app.notify, channel)
        if settings is not None:
            floor = Severity.parse(settings.min_severity) if settings.min_severity else None
            overrides[channel] = ChannelOverride(destinations.get(channel), floor)
    return overrides


def build_app(
    env: Mapping[str, str],
    catalog_dir: Path,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """The whole app, from explicit values: tests pass their own env and a fake transport."""
    env = {k: v for k, v in env.items() if v}  # an empty variable counts as unset
    try:
        catalog = load_catalog(catalog_dir)
        settings = Settings.from_env(env)
    except (CatalogError, ValidationError) as e:
        raise StartupError(str(e)) from e
    if missing := _unset_references(catalog, env):
        raise StartupError("catalog references unset variables:\n" + "\n".join(missing))

    apps = [app.to_watched_app() for app in catalog.values()]
    policy = NotificationPolicy(
        settings.channels(), {name: _overrides(app, env) for name, app in catalog.items()}
    )
    # httpx logs every request URL at INFO, and webhook URLs are secrets.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with httpx.AsyncClient(transport=transport, timeout=SEND_TIMEOUT_SECONDS) as client:
            app.state.receive_alert = ReceiveAlert(
                AlertRouter(apps), policy, _notifiers(settings, client)
            )
            yield

    app = FastAPI(title="Night's Watch", lifespan=lifespan)
    app.state.apps = apps
    app.state.policy = policy
    app.state.registry = CollectorRegistry()
    app.state.unmatched_alerts = Counter(
        "nightswatch_unmatched_alerts",
        "Alerts that matched no watched app and were dropped.",
        registry=app.state.registry,
    )
    app.include_router(alerts.router)
    app.include_router(health.router)
    return app


def create_app() -> FastAPI:
    """What uvicorn runs: the process environment, over .env when there is one."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    env = {**dotenv_values(".env"), **os.environ}
    return build_app(env, Path(env.get("NW_CATALOG_DIR") or "catalog"))
