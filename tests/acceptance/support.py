"""What the acceptance tests share: fake channels, the app under test, payload builders.

Slack, Teams and WhatsApp are faked at the HTTP boundary with httpx.MockTransport;
nothing here touches the network, the real environment or a .env file.
"""

import asyncio
import copy
import json
from contextlib import contextmanager
from pathlib import Path

import httpx
import yaml
from fastapi.testclient import TestClient

from nights_watch.bootstrap import build_app

ROOT = Path(__file__).parent.parent.parent
CATALOG = ROOT / "catalog"
FIXTURES = Path(__file__).parent.parent / "fixtures"

SLACK_URL = "https://hooks.slack.test/services/T0/B0/slack-secret"
TEAMS_URL = "https://teams.test/workflows/abc?sig=teams-secret"
ALL_CHANNELS = {
    "NW_SLACK_WEBHOOK_URL": SLACK_URL,
    "NW_TEAMS_WEBHOOK_URL": TEAMS_URL,
    "NW_WHATSAPP_TOKEN": "wa-token",
    "NW_WHATSAPP_PHONE_ID": "1234567890",
    "NW_WHATSAPP_TO": "56900000001",
    "NW_WHATSAPP_TEMPLATE": "nw_alert",
    "NW_WHATSAPP_TEMPLATE_LANG": "en",
    "NW_KNOCK_KNOCK_WHATSAPP_TO": "56900000002",
}
HOSTS = {"hooks.slack.test": "slack", "teams.test": "teams", "graph.facebook.com": "whatsapp"}


class FakeChannels:
    """Answers like Slack, Teams and the WhatsApp Cloud API, and records every request.

    `fail` maps a channel (or host) to the HTTP status or exception it answers with;
    `slow`, to the seconds it takes to answer.
    """

    def __init__(self, fail=None, slow=None):
        self.requests: list[httpx.Request] = []
        self.fail = fail or {}
        self.slow = slow or {}

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        channel = channel_of(request)
        await asyncio.sleep(self.slow.get(channel, 0))
        failure = self.fail.get(channel)
        if isinstance(failure, Exception):
            raise failure
        if failure:
            return httpx.Response(failure)
        if channel == "whatsapp":
            return httpx.Response(200, json={"messages": [{"id": "wamid.1"}]})
        return httpx.Response(202 if channel == "teams" else 200, text="ok")

    def called(self) -> list[str]:
        return sorted(channel_of(r) for r in self.requests)

    def bodies(self, channel: str) -> list[dict]:
        return [json.loads(r.content) for r in self.requests if channel_of(r) == channel]


def channel_of(request: httpx.Request) -> str:
    return HOSTS.get(request.url.host, request.url.host)


@contextmanager
def running(env=None, catalog=CATALOG, fail=None, slow=None, **build):
    channels = FakeChannels(fail, slow)
    env = ALL_CHANNELS if env is None else env
    app = build_app(env, catalog, transport=httpx.MockTransport(channels.handler), **build)
    with TestClient(app) as client:
        yield client, channels


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def alertmanager(*alerts: dict) -> dict:
    payload = fixture("alertmanager_firing_critical.json")
    payload["alerts"] = list(alerts)
    return payload


def alert(labels: dict, status="firing", annotations=None) -> dict:
    base = copy.deepcopy(fixture("alertmanager_firing_critical.json")["alerts"][0])
    base.update(status=status, labels=labels)
    if annotations is not None:
        base["annotations"] = annotations
    return base


def knock_knock(severity="critical", **extra) -> dict:
    return {"alertname": "HighErrorRate", "namespace": "knock-knock", "severity": severity} | extra


def unmatched(client: TestClient) -> float:
    for line in client.get("/metrics").text.splitlines():
        if line.startswith("nightswatch_unmatched_alerts_total "):
            return float(line.split()[1])
    raise AssertionError("nightswatch_unmatched_alerts_total is not on /metrics")


# Distinctive pieces of every secret: a log line holding only part of one still leaks it.
SECRET_FRAGMENTS = (
    "slack-secret", "/services/", "teams-secret", "sig=", "wa-token",
    ALL_CHANNELS["NW_WHATSAPP_PHONE_ID"], ALL_CHANNELS["NW_WHATSAPP_TO"],
    ALL_CHANNELS["NW_KNOCK_KNOCK_WHATSAPP_TO"],
)


def assert_no_secrets_in(text: str) -> None:
    for fragment in SECRET_FRAGMENTS:
        assert fragment not in text, fragment


def whatsapp_params(channels: FakeChannels) -> list[str]:
    (body,) = channels.bodies("whatsapp")
    (component,) = body["template"]["components"]
    return [p["text"] for p in component["parameters"]]


def write_app(directory: Path, name: str, match: dict, notify: dict | None = None) -> None:
    app = {"name": name, "owner": "team", "description": "an app", "match": match, "sources": {}}
    if notify:
        app["notify"] = notify
    (directory / f"{name}.yaml").write_text(yaml.safe_dump(app))


def app_view(client: TestClient, name: str) -> dict:
    (app,) = [a for a in client.get("/apps").json() if a["name"] == name]
    return app
