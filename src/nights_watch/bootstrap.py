"""Reads the environment, wires adapters to ports, and runs the startup checks."""

import asyncio
import logging
import os
import sqlite3
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from dotenv import dotenv_values
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from prometheus_client import CollectorRegistry, Counter
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from nights_watch.api import alerts, health, runs
from nights_watch.api.errors import validation_failed
from nights_watch.catalog.domain import AlertRouter
from nights_watch.catalog.yaml_catalog import AppFile, CatalogError, describe, load_catalog
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
from nights_watch.triage.application.record_feedback import RecordFeedback
from nights_watch.triage.domain.alert import Severity
from nights_watch.triage.infrastructure.sqlite_incidents import SchemaError, SqliteIncidents

SEND_TIMEOUT_SECONDS = 10.0
WHATSAPP_CREDENTIALS = ("whatsapp_token", "whatsapp_phone_id", "whatsapp_template",
                        "whatsapp_template_lang")

log = logging.getLogger(__name__)


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
    group_window: int = Field(900, ge=1, le=7 * 24 * 3600)  # seconds
    alert_stale_after: int = Field(86400, ge=60, le=30 * 24 * 3600)  # seconds
    db_path: Path = Path("data/nightswatch.sqlite")
    public_url: str = "http://localhost:8000"

    @field_validator(
        "slack_min_severity", "teams_min_severity", "whatsapp_min_severity", mode="before"
    )
    @classmethod
    def _any_case(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("public_url")
    @classmethod
    def _absolute_http_url(cls, value: str) -> str:
        # Every message links here, so it must be a plain address: no credentials, query or
        # fragment. The error never quotes the value (urlsplit's own errors would).
        try:
            url = urlsplit(value)
            plain = (
                url.scheme in ("http", "https") and url.hostname and url.port != 0
                # urlsplit drops tabs and newlines and reads a bare "?" or "#" as empty, so the
                # raw text is checked too; "@" means credentials, "\\" and "|" break links.
                and not any(c in value for c in "?#@\\|")
                and not any(c.isspace() or not c.isprintable() for c in value)
            )
        except ValueError:  # urlsplit's own errors quote the URL, so they never get out
            plain = False
        if not plain:
            raise ValueError(
                "must be a plain absolute http(s) URL, e.g. https://nightswatch.example.com"
            )
        return value

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        try:
            return cls.model_validate(
                {k.removeprefix("NW_").lower(): v for k, v in env.items() if k.startswith("NW_")}
            )
        except ValidationError as e:  # raised below, unchained: the original quotes the value
            problem = describe(e, lambda loc: f"NW_{str(loc[0]).upper()}")
        raise StartupError(problem)

    def check(self) -> None:
        """Rules between settings."""
        if self.alert_stale_after <= self.group_window:
            raise StartupError(
                "NW_ALERT_STALE_AFTER must be above NW_GROUP_WINDOW: an alert would go stale "
                "before its group's window closed"
            )

    @property
    def whatsapp_ready(self) -> bool:
        """Everything WhatsApp needs besides the recipient."""
        return not self.missing_whatsapp_credentials()

    def missing_whatsapp_credentials(self) -> list[str]:
        return [f"NW_{name.upper()}" for name in WHATSAPP_CREDENTIALS if not getattr(self, name)]

    def run_url(self, run_id: str) -> str:
        return f"{self.public_url.rstrip('/')}/runs/{run_id}"

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
    send_timeout: float = SEND_TIMEOUT_SECONDS,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    """The whole app, from explicit values: tests pass their own env and a fake transport."""
    env = _set_only(env)
    try:
        catalog = load_catalog(catalog_dir)
    except CatalogError as e:
        raise StartupError(str(e)) from e  # CatalogError's text never holds a value
    if not catalog:
        raise StartupError(f"no apps in catalog directory {catalog_dir}: nothing to watch")
    settings = Settings.from_env(env)
    settings.check()
    missing = settings.missing_whatsapp_credentials()
    if 0 < len(missing) < len(WHATSAPP_CREDENTIALS):
        log.warning("whatsapp is disabled: partly configured, missing %s", ", ".join(missing))
    if missing := _unset_references(catalog, env):
        raise StartupError("catalog references unset variables:\n" + "\n".join(missing))

    incidents = _open_incidents(settings.db_path)
    lock = asyncio.Lock()  # ReceiveAlert and RecordFeedback both load, change and save incidents
    apps = [app.to_watched_app() for app in catalog.values()]
    policy = NotificationPolicy(
        settings.channels(), {name: _overrides(app, env) for name, app in catalog.items()}
    )
    # httpx logs every request URL at INFO, and webhook URLs are secrets.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with httpx.AsyncClient(transport=transport, timeout=send_timeout) as client:
            app.state.receive_alert = ReceiveAlert(
                AlertRouter(apps), policy, _notifiers(settings, client),
                incidents=incidents, lock=lock,
                window=timedelta(seconds=settings.group_window),
                stale_after=timedelta(seconds=settings.alert_stale_after),
                clock=clock or (lambda: datetime.now(UTC)),
                run_url=settings.run_url, send_timeout=send_timeout,
            )
            yield

    app = FastAPI(title="Night's Watch", lifespan=lifespan)
    app.state.apps = apps
    app.state.policy = policy
    app.state.incidents = incidents
    app.state.record_feedback = RecordFeedback(incidents, lock)
    app.state.registry = CollectorRegistry()
    app.state.unmatched_alerts = Counter(
        "nightswatch_unmatched_alerts",
        "Alerts that matched no watched app and were dropped.",
        registry=app.state.registry,
    )
    app.add_exception_handler(RequestValidationError, validation_failed)
    app.include_router(alerts.router)
    app.include_router(health.router)
    app.include_router(runs.router)
    return app


def _open_incidents(path: Path) -> SqliteIncidents:
    try:
        return SqliteIncidents(path)
    except SchemaError as e:
        problem = str(e)
    except (sqlite3.Error, OSError) as e:
        problem = f"{path}: can't open the database ({type(e).__name__})"
    raise StartupError(problem)


def _set_only(env: Mapping[str, str | None]) -> dict[str, str]:
    """A blank variable counts as unset."""
    return {k: v.strip() for k, v in env.items() if v and v.strip()}


def read_environment() -> dict[str, str]:
    """The process environment over .env (when there is one), blank variables dropped from
    each first, so an empty exported variable doesn't hide a value set in .env."""
    return _set_only(dotenv_values(".env")) | _set_only(os.environ)


def create_app() -> FastAPI:
    """What uvicorn runs."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    env = read_environment()
    return build_app(env, Path(env.get("NW_CATALOG_DIR", "catalog")))
