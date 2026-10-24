"""M1a acceptance tests: incidents, grouping, persistence and the /runs page, black-box through
the HTTP app. Run ids are read from the links in the messages, as a person would follow them."""

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from support import (
    ALL_CHANNELS,
    CATALOG,
    ROOT,
    alert,
    alertmanager,
    knock_knock,
    running,
    unmatched,
    whatsapp_params,
)

from nights_watch.bootstrap import StartupError, build_app

PUBLIC_URL = "https://nw.test"
RUN_LINK = re.compile(re.escape(PUBLIC_URL) + r"/runs/([0-9a-f]{32})")
WINDOW = 300


class FakeClock:
    def __init__(self):
        self.now = datetime(2026, 10, 24, 3, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@contextmanager
def watching(db: Path | None = None, clock: FakeClock | None = None, **build):
    env = ALL_CHANNELS | {"NW_PUBLIC_URL": PUBLIC_URL}
    if db is not None:
        env |= {"NW_DB_PATH": str(db)}
    clock = clock or FakeClock()
    with running(env, clock=clock, **build) as (client, channels):
        yield client, channels, clock


def pod(name: str, severity="critical", status="firing", summary=None) -> dict:
    a = alert(knock_knock(severity, pod=name), status=status,
              annotations={"summary": summary} if summary else None)
    a["fingerprint"] = f"fp-{name}"
    return a


def post(client, *alerts):
    response = client.post("/alerts/alertmanager", json=alertmanager(*alerts))
    assert response.status_code == 202
    return response


def run_ids(channels, channel="slack") -> list[str]:
    return [RUN_LINK.search(json.dumps(body)).group(1) for body in channels.bodies(channel)]


def status_on(html: str) -> str:
    return re.search(r'id="status">([^<]*)<', html).group(1)


def page(client, run_id: str) -> str:
    response = client.get(f"/runs/{run_id}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    return response.text


# --- Happy path ------------------------------------------------------------------------------


def test_a_firing_alert_opens_an_incident_linked_from_every_message():
    with watching() as (client, channels, _):
        post(client, pod("a"))
        (run_id,) = run_ids(channels, "slack")
        assert run_ids(channels, "teams") == [run_id]
        html = page(client, run_id)
    assert channels.called() == ["slack", "teams", "whatsapp"]
    for expected in ("knock-knock", "Error rate above 5% on knock-knock", "critical", "open",
                     "fp-a"):
        assert expected in html


def test_the_same_alert_twice_inside_the_window_is_one_incident_and_one_message():
    with watching() as (client, channels, clock):
        post(client, pod("a"))
        clock.advance(60)
        post(client, pod("a"))
    assert channels.called() == ["slack", "teams", "whatsapp"]


def test_alerts_of_one_rule_on_different_pods_share_the_incident():
    with watching() as (client, channels, clock):
        post(client, pod("a"))
        clock.advance(30)
        post(client, pod("b"))
        (run_id,) = run_ids(channels)
        html = page(client, run_id)
    assert channels.called() == ["slack", "teams", "whatsapp"]
    assert "fp-a" in html and "fp-b" in html


def test_the_window_slides_from_the_latest_alert():
    with watching() as (client, channels, clock):
        post(client, pod("a"))
        clock.advance(WINDOW - 60)
        post(client, pod("b"))  # within the window of a
        clock.advance(WINDOW - 60)
        post(client, pod("c"))  # past the window from a, within it from b
        assert len(run_ids(channels)) == 1
        clock.advance(WINDOW + 1)
        post(client, pod("d"))  # past the window from c: a new incident
        first, second = run_ids(channels)
    assert first != second


def test_a_rising_severity_reaches_only_the_newly_reached_channels():
    with watching() as (client, channels, clock):
        post(client, pod("a", "warning"))
        assert channels.called() == ["slack", "teams"]
        clock.advance(30)
        post(client, pod("b", "critical"))
        (run_id,) = run_ids(channels)
        html = page(client, run_id)
    assert channels.called() == ["slack", "teams", "whatsapp"]
    assert whatsapp_params(channels)[0] == "critical"
    assert "critical" in html


def test_an_incident_resolves_when_all_its_alerts_have():
    with watching() as (client, channels, clock):
        post(client, pod("a"), pod("b"))
        (run_id,) = run_ids(channels)
        clock.advance(30)
        post(client, pod("a", status="resolved"))
        assert status_on(page(client, run_id)) == "open"
        post(client, pod("b", status="resolved"))
        assert status_on(page(client, run_id)) == "resolved"
        clock.advance(30)
        post(client, pod("c"))
        first, second = run_ids(channels)
    assert first != second


def test_incidents_survive_a_restart(tmp_path):
    db, clock = tmp_path / "nw.sqlite", FakeClock()
    with watching(db, clock) as (client, channels, _):
        post(client, pod("a"))
        (run_id,) = run_ids(channels)
    clock.advance(60)
    with watching(db, clock) as (client, channels, _):
        assert "fp-a" in page(client, run_id)
        post(client, pod("b"))
        html = page(client, run_id)
    assert channels.called() == []  # attached to the incident from before the restart
    assert "fp-b" in html


def test_alerts_of_one_rule_in_one_request_open_one_incident():
    with watching() as (client, channels, _):
        post(client, pod("a"), pod("b"), pod("c"), pod("d"))
        (run_id,) = run_ids(channels)
        html = page(client, run_id)
    assert channels.called() == ["slack", "teams", "whatsapp"]
    assert all(f"fp-{p}" in html for p in "abcd")


# --- Feedback --------------------------------------------------------------------------------


def test_feedback_is_refused_before_a_diagnosis():
    with watching() as (client, channels, _):
        post(client, pod("a"))
        (run_id,) = run_ids(channels)
        assert client.post(f"/runs/{run_id}/feedback", data={"vote": "up"}).status_code == 409
        assert client.post(f"/runs/{run_id}/feedback", data={"vote": "meh"}).status_code == 422
        assert client.post(f"/runs/{'0' * 32}/feedback", data={"vote": "up"}).status_code == 404


# --- Edge cases ------------------------------------------------------------------------------


def test_alert_text_is_escaped_on_the_run_page():
    with watching() as (client, channels, _):
        post(client, pod("a", summary="<script>alert(1)</script>"))
        (run_id,) = run_ids(channels)
        html = page(client, run_id)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_an_unknown_run_is_404():
    with watching() as (client, channels, _):
        post(client, pod("a"))
        (run_id,) = run_ids(channels)
        assert client.get(f"/runs/{run_id}").status_code == 200
        assert client.get(f"/runs/{'0' * 32}").status_code == 404


def test_a_resolve_for_an_alert_no_incident_holds_is_ignored():
    with watching() as (client, channels, _):
        post(client, pod("z", status="resolved"))
        assert unmatched(client) == 0
    assert channels.called() == []


def test_a_database_with_an_unknown_schema_version_stops_startup(tmp_path):
    db = tmp_path / "future.sqlite"
    with sqlite3.connect(db) as connection:
        connection.execute("PRAGMA user_version = 99")
    env = ALL_CHANNELS | {"NW_DB_PATH": str(db)}
    with pytest.raises(StartupError, match="future.sqlite"):
        build_app(env, CATALOG)


# --- Config & container ----------------------------------------------------------------------


def test_the_new_settings_are_in_env_example_with_their_defaults():
    lines = (ROOT / ".env.example").read_text().splitlines()
    for line in ("NW_GROUP_WINDOW=300", "NW_DB_PATH=data/nightswatch.sqlite",
                 "NW_PUBLIC_URL=http://localhost:8000"):
        assert line in lines


def test_the_container_keeps_the_database_on_a_named_volume_with_one_worker():
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())
    (service,) = compose["services"].values()
    db_dir = str(Path(service["environment"]["NW_DB_PATH"]).parent)
    mounts = {v.split(":")[1]: v.split(":")[0] for v in service["volumes"]}
    assert db_dir in mounts and mounts[db_dir] in compose["volumes"]  # named, not a bind mount
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert '"--workers", "1"' in dockerfile
