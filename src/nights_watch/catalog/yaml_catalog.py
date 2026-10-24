"""Loads and validates catalog/<app>.yaml files: which apps Night's Watch watches."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from nights_watch.catalog.domain import LabelMatcher, WatchedApp

# Catalog files hold variable NAMES, never values: the values live in .env or GitHub secrets.
ENV_NAME = r"^NW_[A-Z0-9_]+$"
APP_NAME = r"^[a-z0-9]+(-[a-z0-9]+)*$"

Severity = Literal["info", "warning", "critical"]


class CatalogError(Exception):
    pass


class _Strict(BaseModel):
    # forbid unknown keys so a typo fails loudly instead of being ignored
    model_config = ConfigDict(extra="forbid", frozen=True)


class Component(_Strict):
    name: str
    selector: dict[str, str]


class LabelSelector(_Strict):
    selector: str


class KubernetesScope(_Strict):
    namespace: str


class GitHubScope(_Strict):
    repo: str = Field(pattern=r"^[\w.-]+/[\w.-]+$")


class RunbooksScope(_Strict):
    path: str


class Sources(_Strict):
    prometheus: LabelSelector | None = None
    loki: LabelSelector | None = None
    kubernetes: KubernetesScope | None = None
    github: GitHubScope | None = None
    runbooks: RunbooksScope | None = None


class WebhookChannel(_Strict):
    webhook_env: str | None = Field(None, pattern=ENV_NAME)  # None → global NW_*_WEBHOOK_URL
    min_severity: Severity | None = None  # None → global NW_*_MIN_SEVERITY


class WhatsAppChannel(_Strict):
    to_env: str | None = Field(None, pattern=ENV_NAME)  # None → global NW_WHATSAPP_TO
    min_severity: Severity | None = None  # None → global NW_WHATSAPP_MIN_SEVERITY


class Notify(_Strict):
    slack: WebhookChannel | None = None
    teams: WebhookChannel | None = None
    whatsapp: WhatsAppChannel | None = None


class AppFile(_Strict):
    name: str = Field(pattern=APP_NAME)
    owner: str
    description: str
    match: dict[str, str] = Field(min_length=1)
    components: list[Component] = []
    sources: Sources
    notify: Notify = Notify()

    def to_watched_app(self) -> WatchedApp:
        return WatchedApp(self.name, LabelMatcher(self.match))


def describe(error: ValidationError, name=lambda loc: ".".join(map(str, loc))) -> str:
    """Where and why, never the rejected value: a value pasted by mistake may be a secret."""
    return "; ".join(
        f"{name(e['loc'])}: {e['msg']}" for e in error.errors(include_input=False, include_url=False)
    )


def parse_app_file(path: Path) -> AppFile:
    try:
        return AppFile.model_validate(yaml.safe_load(path.read_text()))
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" at line {mark.line + 1}" if mark else ""
        raise CatalogError(f"{path.name}: invalid YAML{where}") from None
    except ValidationError as e:
        raise CatalogError(f"{path.name}: {describe(e)}") from None


def load_catalog(directory: Path) -> dict[str, AppFile]:
    """Every <name>.yaml in the directory; files starting with _ (the template) are skipped."""
    if not directory.is_dir():
        raise CatalogError(f"catalog directory {directory} does not exist")
    apps = {}


    for path in sorted(directory.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        app = parse_app_file(path)
        if app.name != path.stem:
            raise CatalogError(f"{path.name}: name '{app.name}' must match the filename")
        apps[app.name] = app
    return apps
