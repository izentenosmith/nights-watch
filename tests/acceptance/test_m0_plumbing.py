"""M0 acceptance tests: black-box, through the HTTP app that bootstrap builds.

Slack, Teams and WhatsApp are faked at the HTTP boundary (see support.py).
"""

import json
import logging
import time
import traceback

import httpx
import pytest
import yaml
from support import (
    ALL_CHANNELS,
    CATALOG,
    ROOT,
    SLACK_URL,
    TEAMS_URL,
    alert,
    alertmanager,
    app_view,
    assert_no_secrets_in,
    channel_of,
    fixture,
    knock_knock,
    running,
    unmatched,
    whatsapp_params,
    write_app,
)

from nights_watch.bootstrap import StartupError, build_app

# --- Acceptance criteria from docs/milestones/M0-plumbing.md -------------------------------


def test_ac1_critical_alert_reaches_each_enabled_channel_once():
    with running() as (client, channels):
        response = client.post(
            "/alerts/alertmanager", json=fixture("alertmanager_firing_critical.json")
        )
    assert response.status_code == 202
    assert channels.called() == ["slack", "teams", "whatsapp"]


def test_ac2_warning_alert_skips_whatsapp_under_default_floors():
    with running() as (client, channels):
        response = client.post("/alerts/alertmanager", json=alertmanager(alert(knock_knock("warning"))))
    assert response.status_code == 202
    assert channels.called() == ["slack", "teams"]


def test_ac3_unmatched_alert_sends_nothing_returns_202_and_is_counted():
    with running() as (client, channels):
        before = unmatched(client)
        response = client.post(
            "/alerts/alertmanager",
            json=alertmanager(alert({"alertname": "X", "namespace": "nobody", "severity": "critical"})),
        )
        after = unmatched(client)
    assert response.status_code == 202
    assert channels.called() == []
    assert after == before + 1


def test_ac4_grafana_payload_is_accepted_by_the_alertmanager_route():
    with running() as (client, channels):
        response = client.post("/alerts/alertmanager", json=fixture("grafana_firing_critical.json"))
    assert response.status_code == 202
    assert channels.called() == ["slack", "teams", "whatsapp"]


def test_ac5_generic_alert_behaves_like_alertmanager():
    with running() as (client, channels):
        response = client.post("/alerts/generic", json=fixture("generic_firing_critical.json"))
    assert response.status_code == 202
    assert channels.called() == ["slack", "teams", "whatsapp"]


def test_ac6_unset_referenced_env_vars_stop_startup_naming_file_field_and_variable(tmp_path):
    write_app(
        tmp_path,
        "shop",
        {"namespace": "shop"},
        notify={
            "slack": {"webhook_env": "NW_SHOP_SLACK_WEBHOOK_URL"},
            "whatsapp": {"to_env": "NW_SHOP_WHATSAPP_TO"},
        },
    )
    with pytest.raises(StartupError) as error:
        build_app(ALL_CHANNELS | {"NW_SHOP_WHATSAPP_TO": ""}, tmp_path)
    lines = [line.strip() for line in str(error.value).splitlines()]
    assert "shop.yaml notify.slack.webhook_env: NW_SHOP_SLACK_WEBHOOK_URL" in lines
    assert "shop.yaml notify.whatsapp.to_env: NW_SHOP_WHATSAPP_TO" in lines


def test_ac6_the_sample_catalog_needs_its_whatsapp_variable():
    env = {k: v for k, v in ALL_CHANNELS.items() if k != "NW_KNOCK_KNOCK_WHATSAPP_TO"}
    with pytest.raises(StartupError, match="knock-knock.yaml.*NW_KNOCK_KNOCK_WHATSAPP_TO"):
        build_app(env, CATALOG)


def test_ac6_a_missing_or_empty_catalog_directory_stops_startup(tmp_path):
    with pytest.raises(StartupError, match="nowhere does not exist"):
        build_app(ALL_CHANNELS, tmp_path / "nowhere")
    with pytest.raises(StartupError, match="no apps"):
        build_app(ALL_CHANNELS, tmp_path)
    (tmp_path / "_template.yaml").write_text((CATALOG / "_template.yaml").read_text())
    with pytest.raises(StartupError, match="no apps"):
        build_app(ALL_CHANNELS, tmp_path)


def test_ac6_a_bad_severity_floor_names_the_variable_without_its_value():
    env = ALL_CHANNELS | {"NW_SLACK_MIN_SEVERITY": "hooks-secret"}  # not on the raising line
    with pytest.raises(StartupError) as error:
        build_app(env, CATALOG)
    assert "NW_SLACK_MIN_SEVERITY" in str(error.value)
    assert "hooks-secret" not in "".join(traceback.format_exception(error.value))
    assert error.value.__cause__ is None and error.value.__context__ is None


def test_ac6_a_catalog_error_stops_startup_without_echoing_the_value(tmp_path):
    write_app(tmp_path, "shop", {"namespace": "shop"},
              {"slack": {"webhook_env": "https://hooks.slack.com/services/T0/B0/CATALOGSECRET"}})
    with pytest.raises(StartupError) as error:
        build_app(ALL_CHANNELS, tmp_path)
    assert "shop.yaml" in str(error.value)
    printed, pending = "".join(traceback.format_exception(error.value)), [error.value]
    while pending:
        chained = pending.pop()
        if chained is not None:
            printed += repr(chained) + str(chained)
            pending += [chained.__cause__, chained.__context__]
    assert "CATALOGSECRET" not in printed


def test_ac6_severity_floors_ignore_case(tmp_path):
    write_app(tmp_path, "shop", {"namespace": "shop"})
    env = {"NW_SLACK_WEBHOOK_URL": SLACK_URL, "NW_SLACK_MIN_SEVERITY": "Critical"}
    with running(env, tmp_path) as (client, _):
        assert app_view(client, "shop")["channels"]["slack"]["min_severity"] == "critical"


def test_ac7_channel_with_no_env_vars_is_never_called():
    env = {k: v for k, v in ALL_CHANNELS.items() if not k.startswith("NW_TEAMS_")}
    with running(env) as (client, channels):
        client.post("/alerts/alertmanager", json=fixture("alertmanager_firing_critical.json"))
    assert channels.called() == ["slack", "whatsapp"]


def test_ac7_apps_shows_which_channels_are_enabled_for_each_app_without_destinations():
    env = {k: v for k, v in ALL_CHANNELS.items() if not k.startswith("NW_TEAMS_")}
    with running(env) as (client, _):
        response = client.get("/apps")
    assert response.status_code == 200
    app = next(a for a in response.json() if a["name"] == "knock-knock")
    assert app["match"] == {"namespace": "knock-knock"}
    assert app["channels"] == {
        "slack": {"enabled": True, "min_severity": "warning"},
        "teams": {"enabled": False, "min_severity": "warning"},
        "whatsapp": {"enabled": True, "min_severity": "critical"},
    }
    for value in env.values():
        if value not in ("nw_alert", "en"):
            assert value not in response.text


def test_ac8_one_failing_channel_does_not_stop_the_others_and_is_logged(caplog):
    caplog.set_level(logging.INFO)
    with running(fail={"slack": 500}) as (client, channels):
        response = client.post(
            "/alerts/alertmanager", json=fixture("alertmanager_firing_critical.json")
        )
    assert response.status_code == 202
    assert channels.called() == ["slack", "teams", "whatsapp"]
    failures = [
        r.getMessage() for r in caplog.records
        if r.levelno >= logging.WARNING and "slack" in r.getMessage()
    ]
    assert failures and "knock-knock" in failures[0]
    assert_no_secrets_in(caplog.text)


def test_ac9_compose_runs_the_app_with_env_and_catalog_on_localhost():
    assert (ROOT / "Dockerfile").is_file()
    assert (ROOT / "compose.yaml").is_file()
    (service,) = yaml.safe_load((ROOT / "compose.yaml").read_text())["services"].values()
    assert "build" in service
    env_files = service["env_file"] if isinstance(service["env_file"], list) else [service["env_file"]]
    assert ".env" in [f["path"] if isinstance(f, dict) else f for f in env_files]
    (catalog_mount,) = [v for v in service["volumes"] if str(v).startswith("./catalog:")]
    assert catalog_mount.split(":")[1] == service["environment"]["NW_CATALOG_DIR"]
    assert service["ports"] == ["127.0.0.1:8000:8000"]
    ignored = [line.strip() for line in (ROOT / ".dockerignore").read_text().splitlines()]
    assert {"**/.env", "**/.env.*", "**/*.env"} <= set(ignored)  # no env file in the image
    assert not [line for line in ignored if line.startswith("!")]  # nothing re-included


# --- Channel payloads ----------------------------------------------------------------------


def test_slack_gets_a_block_kit_message():
    with running() as (client, channels):
        client.post("/alerts/alertmanager", json=fixture("alertmanager_firing_critical.json"))
    (request,) = [r for r in channels.requests if channel_of(r) == "slack"]
    assert str(request.url) == SLACK_URL
    body = json.loads(request.content)
    blocks = json.dumps(body["blocks"])
    assert body["text"]
    for expected in ("critical", "knock-knock", "Error rate above 5% on knock-knock"):
        assert expected in blocks


def test_teams_gets_an_adaptive_card_wrapped_in_a_message():
    with running() as (client, channels):
        client.post("/alerts/alertmanager", json=fixture("alertmanager_firing_critical.json"))
    (request,) = [r for r in channels.requests if channel_of(r) == "teams"]
    assert str(request.url) == TEAMS_URL
    body = json.loads(request.content)
    assert body["type"] == "message"
    (attachment,) = body["attachments"]
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert attachment["content"]["type"] == "AdaptiveCard"
    card = json.dumps(attachment["content"])
    for expected in ("critical", "knock-knock", "Error rate above 5% on knock-knock"):
        assert expected in card


def test_whatsapp_gets_the_template_with_severity_app_and_title():
    with running() as (client, channels):
        client.post("/alerts/alertmanager", json=fixture("alertmanager_firing_critical.json"))
    (request,) = [r for r in channels.requests if channel_of(r) == "whatsapp"]
    assert request.method == "POST"
    assert request.url.path.endswith("/1234567890/messages")
    assert request.headers["authorization"] == "Bearer wa-token"
    body = json.loads(request.content)
    assert body["messaging_product"] == "whatsapp"
    assert body["to"] == "56900000002"  # knock-knock's own to_env wins over NW_WHATSAPP_TO
    assert body["type"] == "template"
    assert body["template"]["name"] == "nw_alert"
    assert body["template"]["language"] == {"code": "en"}
    assert body["template"]["components"][0]["type"] == "body"
    assert whatsapp_params(channels) == ["critical", "knock-knock", "Error rate above 5% on knock-knock"]


def test_slack_and_teams_show_the_app_and_the_placeholder_whatever_the_title():
    with running() as (client, channels):
        client.post("/alerts/alertmanager", json=alertmanager(
            alert(knock_knock(), annotations={"summary": "Disk full"})))
    (slack,) = channels.bodies("slack")
    (teams,) = channels.bodies("teams")
    for channel, shown in (("slack", slack["blocks"]), ("teams", teams["attachments"])):
        text = json.dumps(shown)
        assert "knock-knock" in text, channel
        assert "investigation arrives in M1" in text, channel


# --- Routing -------------------------------------------------------------------------------


def test_routing_the_app_with_most_matchers_wins_and_a_warning_is_logged(tmp_path, caplog):
    write_app(tmp_path, "shop", {"namespace": "shop"},
              {"slack": {"webhook_env": "NW_SHOP_SLACK_WEBHOOK_URL"}})
    write_app(tmp_path, "shop-api", {"namespace": "shop", "app": "api"},
              {"slack": {"webhook_env": "NW_SHOP_API_SLACK_WEBHOOK_URL"}})
    env = {
        "NW_SHOP_SLACK_WEBHOOK_URL": "https://shop.test/hook",
        "NW_SHOP_API_SLACK_WEBHOOK_URL": "https://shop-api.test/hook",
    }
    with running(env, tmp_path) as (client, channels):
        client.post("/alerts/alertmanager", json=alertmanager(
            alert({"alertname": "X", "namespace": "shop", "app": "api", "severity": "critical"})))
    assert channels.called() == ["shop-api.test"]
    assert any(r.levelno == logging.WARNING and "shop-api" in r.getMessage() for r in caplog.records)


def test_routing_a_tie_goes_to_the_first_app_by_name(tmp_path):
    write_app(tmp_path, "beta", {"env": "prod"}, {"slack": {"webhook_env": "NW_BETA_SLACK"}})
    write_app(tmp_path, "alpha", {"team": "x"}, {"slack": {"webhook_env": "NW_ALPHA_SLACK"}})
    env = {"NW_ALPHA_SLACK": "https://alpha.test/hook", "NW_BETA_SLACK": "https://beta.test/hook"}
    with running(env, tmp_path) as (client, channels):
        client.post("/alerts/alertmanager", json=alertmanager(
            alert({"alertname": "X", "env": "prod", "team": "x", "severity": "critical"})))
    assert channels.called() == ["alpha.test"]


# --- Notification policy -------------------------------------------------------------------


def test_policy_an_app_override_without_a_floor_inherits_the_global_floor(tmp_path):
    write_app(tmp_path, "shop", {"namespace": "shop"},
              {"slack": {"webhook_env": "NW_SHOP_SLACK_WEBHOOK_URL"}})
    env = {"NW_SHOP_SLACK_WEBHOOK_URL": "https://shop.test/hook", "NW_SLACK_MIN_SEVERITY": "critical"}
    with running(env, tmp_path) as (client, channels):
        assert app_view(client, "shop")["channels"]["slack"] == {
            "enabled": True, "min_severity": "critical"}
        client.post("/alerts/alertmanager", json=alertmanager(
            alert({"alertname": "X", "namespace": "shop", "severity": "warning"})))
    assert channels.called() == []


def test_policy_an_app_floor_overrides_the_global_floor(tmp_path):
    write_app(tmp_path, "shop", {"namespace": "shop"}, {"slack": {"min_severity": "critical"}})
    with running({"NW_SLACK_WEBHOOK_URL": SLACK_URL}, tmp_path) as (client, channels):
        client.post("/alerts/alertmanager", json=alertmanager(
            alert({"alertname": "X", "namespace": "shop", "severity": "warning"})))
    assert channels.called() == []


def test_policy_an_app_only_destination_enables_the_channel_for_that_app_only(tmp_path):
    write_app(tmp_path, "shop", {"namespace": "shop"},
              {"slack": {"webhook_env": "NW_SHOP_SLACK_WEBHOOK_URL"}})
    write_app(tmp_path, "blog", {"namespace": "blog"})
    with running({"NW_SHOP_SLACK_WEBHOOK_URL": "https://shop.test/hook"}, tmp_path) as (client, _):
        assert app_view(client, "shop")["channels"]["slack"]["enabled"] is True
        assert app_view(client, "blog")["channels"]["slack"]["enabled"] is False


def test_policy_an_app_destination_wins_over_the_global_one(tmp_path):
    write_app(tmp_path, "shop", {"namespace": "shop"},
              {"slack": {"webhook_env": "NW_SHOP_SLACK_WEBHOOK_URL"}})
    env = {"NW_SLACK_WEBHOOK_URL": SLACK_URL, "NW_SHOP_SLACK_WEBHOOK_URL": "https://shop.test/hook"}
    with running(env, tmp_path) as (client, channels):
        client.post("/alerts/alertmanager", json=alertmanager(
            alert({"alertname": "X", "namespace": "shop", "severity": "critical"})))
    assert channels.called() == ["shop.test"]


def test_policy_whatsapp_without_token_or_phone_id_is_disabled():
    env = {k: v for k, v in ALL_CHANNELS.items() if k != "NW_WHATSAPP_TOKEN"}
    with running(env) as (client, channels):
        assert app_view(client, "knock-knock")["channels"]["whatsapp"]["enabled"] is False
        client.post("/alerts/alertmanager", json=fixture("alertmanager_firing_critical.json"))
    assert channels.called() == ["slack", "teams"]


@pytest.mark.parametrize(
    "extra_env, severity, expected",
    [
        ({}, "critical", ["slack", "teams", "whatsapp"]),
        ({}, "warning", ["slack", "teams"]),
        ({"NW_WHATSAPP_MIN_SEVERITY": "warning"}, "warning", ["slack", "teams", "whatsapp"]),
    ],
    ids=["default floor: critical", "default floor: warning skipped", "global floor lowered"],
)
def test_policy_an_app_without_its_own_whatsapp_settings_uses_the_global_ones(
    tmp_path, extra_env, severity, expected
):
    write_app(tmp_path, "shop", {"namespace": "shop"})
    with running(ALL_CHANNELS | extra_env, tmp_path) as (client, channels):
        client.post("/alerts/alertmanager", json=alertmanager(
            alert({"alertname": "X", "namespace": "shop", "severity": severity})))
    assert channels.called() == expected
    for body in channels.bodies("whatsapp"):
        assert body["to"] == ALL_CHANNELS["NW_WHATSAPP_TO"]


def test_policy_a_partly_configured_whatsapp_is_named_at_startup(caplog):
    env = {k: v for k, v in ALL_CHANNELS.items() if k != "NW_WHATSAPP_TEMPLATE"}
    build_app(env, CATALOG)
    (warning,) = [r.getMessage() for r in caplog.records if "whatsapp" in r.getMessage()]
    assert "NW_WHATSAPP_TEMPLATE" in warning and "disabled" in warning
    assert_no_secrets_in(caplog.text)


@pytest.mark.parametrize(
    "missing",
    ["NW_WHATSAPP_TOKEN", "NW_WHATSAPP_PHONE_ID", "NW_WHATSAPP_TEMPLATE", "NW_WHATSAPP_TEMPLATE_LANG"],
)
def test_policy_whatsapp_needs_every_credential(missing):
    env = {k: v for k, v in ALL_CHANNELS.items() if k != missing}
    with running(env) as (client, channels):
        assert app_view(client, "knock-knock")["channels"]["whatsapp"]["enabled"] is False
        client.post("/alerts/alertmanager", json=fixture("alertmanager_firing_critical.json"))
    assert channels.called() == ["slack", "teams"]


# --- Edge cases ----------------------------------------------------------------------------


def test_edge_a_resolved_alert_sends_nothing_and_is_not_counted():
    with running() as (client, channels):
        response = client.post("/alerts/alertmanager", json=alertmanager(
            alert(knock_knock(), status="resolved"),
            alert({"alertname": "X", "namespace": "nobody"}, status="resolved"),
        ))
        assert unmatched(client) == 0
    assert response.status_code == 202
    assert channels.called() == []


def test_edge_each_alert_in_one_request_is_routed_on_its_own():
    with running() as (client, channels):
        client.post("/alerts/alertmanager", json=alertmanager(
            alert(knock_knock()),
            alert({"alertname": "X", "namespace": "nobody", "severity": "critical"}),
        ))
        assert unmatched(client) == 1
    assert channels.called() == ["slack", "teams", "whatsapp"]


@pytest.mark.parametrize(
    "severity, expected",
    [
        ("CRITICAL", ["slack", "teams", "whatsapp"]),
        ("info", []),
        ("page", ["slack", "teams"]),  # unknown → warning
        (None, ["slack", "teams"]),  # missing → warning
    ],
)
def test_edge_severity_label_is_case_insensitive_and_defaults_to_warning(severity, expected):
    labels = knock_knock()
    labels.pop("severity")
    if severity:
        labels["severity"] = severity
    with running() as (client, channels):
        client.post("/alerts/alertmanager", json=alertmanager(alert(labels)))
    assert channels.called() == expected


def test_edge_the_severity_sent_is_normalized():
    with running() as (client, channels):
        client.post("/alerts/alertmanager", json=alertmanager(alert(knock_knock("CRITICAL"))))
    assert whatsapp_params(channels)[0] == "critical"


def test_edge_generic_alert_without_severity_is_a_warning():
    payload = fixture("generic_firing_critical.json")
    del payload["severity"]
    with running() as (client, channels):
        assert client.post("/alerts/generic", json=payload).status_code == 202
    assert channels.called() == ["slack", "teams"]


@pytest.mark.parametrize(
    "route, body",
    [
        ("/alerts/alertmanager", {}),
        ("/alerts/alertmanager", {"alerts": "not a list"}),
        ("/alerts/alertmanager", {"alerts": [{"status": "firing", "annotations": {}}]}),
        ("/alerts/generic", {"severity": "critical", "labels": {"namespace": "knock-knock"}}),
    ],
    ids=["no alerts", "alerts not a list", "alert without labels", "generic without title"],
)
def test_edge_a_malformed_payload_is_rejected_with_422(route, body):
    with running() as (client, channels):
        response = client.post(route, json=body)
    assert response.status_code == 422
    assert channels.called() == []


def test_edge_an_empty_alert_list_is_accepted_and_sends_nothing():
    with running() as (client, channels):
        response = client.post("/alerts/alertmanager", json=alertmanager())
    assert response.status_code == 202
    assert channels.called() == []


@pytest.mark.parametrize(
    "annotations, labels, title",
    [
        ({"summary": "Disk full"}, knock_knock(), "Disk full"),
        ({}, knock_knock(), "HighErrorRate"),
        ({}, {"namespace": "knock-knock", "severity": "critical"}, "unnamed alert"),
    ],
    ids=["summary", "alertname", "neither"],
)
def test_edge_the_title_falls_back_from_summary_to_alertname(annotations, labels, title):
    with running() as (client, channels):
        client.post("/alerts/alertmanager", json=alertmanager(alert(labels, annotations=annotations)))
    assert whatsapp_params(channels)[2] == title


def test_edge_whatsapp_params_are_one_line_and_capped():
    summary = "line one\nline two\t\tand      spaces " + "x" * 1000
    with running() as (client, channels):
        client.post("/alerts/alertmanager", json=alertmanager(
            alert(knock_knock(), annotations={"summary": summary})))
    title = whatsapp_params(channels)[2]
    assert title.startswith("line one line two and spaces x")
    assert "\n" not in title and "\t" not in title and "    " not in title
    assert len(title) <= 200


@pytest.mark.parametrize(
    "failure",
    [httpx.ReadTimeout(f"slow: {SLACK_URL}"), httpx.ConnectError(f"cannot reach {SLACK_URL}")],
    ids=["timeout", "connection error"],
)
def test_edge_a_transport_failure_is_isolated_like_a_500(failure, caplog):
    caplog.set_level(logging.INFO)
    with running(fail={"slack": failure}) as (client, channels):
        response = client.post(
            "/alerts/alertmanager", json=fixture("alertmanager_firing_critical.json")
        )
    assert response.status_code == 202
    assert channels.called() == ["slack", "teams", "whatsapp"]
    assert any(r.levelno >= logging.WARNING and "slack" in r.getMessage() for r in caplog.records)
    assert_no_secrets_in(caplog.text)


def test_edge_a_start_time_outside_utc_range_is_rejected_with_422():
    with running() as (client, channels):
        response = client.post("/alerts/alertmanager", json=alertmanager(
            alert(knock_knock()) | {"startsAt": "0001-01-01T00:00:00+01:00"}))
    assert response.status_code == 422
    assert channels.called() == []


def test_edge_a_channel_slower_than_the_send_timeout_doesnt_hold_the_response(caplog):
    with running(slow={"slack": 2}, send_timeout=0.1) as (client, channels):
        started = time.perf_counter()
        response = client.post(
            "/alerts/alertmanager", json=fixture("alertmanager_firing_critical.json")
        )
        elapsed = time.perf_counter() - started
    assert response.status_code == 202
    assert elapsed < 1.5
    assert channels.called() == ["slack", "teams", "whatsapp"]
    failures = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(failures) == 1 and "slack" in failures[0] and "timed out" in failures[0]


def test_edge_the_send_timeout_also_bounds_each_http_phase():
    with running(send_timeout=0.3) as (client, channels):
        client.post("/alerts/alertmanager", json=fixture("alertmanager_firing_critical.json"))
    for request in channels.requests:
        assert set(request.extensions["timeout"].values()) == {0.3}


def test_healthz_answers_200():
    with running() as (client, _):
        assert client.get("/healthz").status_code == 200
