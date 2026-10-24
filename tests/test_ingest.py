import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from nights_watch.triage.domain.alert import Fingerprint, Severity, Status
from nights_watch.triage.infrastructure.ingest.alertmanager import AlertmanagerPayload
from nights_watch.triage.infrastructure.ingest.generic import GenericAlert

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.mark.parametrize(
    "name, fingerprint, generator_url",
    [
        ("alertmanager_firing_critical.json", "c8d1f5e3a2b4c6d7", "http://prometheus:9090/graph"),
        ("grafana_firing_critical.json", "57c6d9296de2ad39", "http://grafana:3000/alerting"),
    ],
)
def test_alertmanager_and_grafana_payloads_become_alerts(name, fingerprint, generator_url):
    (alert,) = AlertmanagerPayload.model_validate(fixture(name)).to_alerts()
    assert alert.title == "Error rate above 5% on knock-knock"
    assert alert.severity is Severity.CRITICAL
    assert alert.status is Status.FIRING
    assert alert.labels["namespace"] == "knock-knock"
    assert alert.starts_at == datetime(2026, 10, 24, 3, 0, tzinfo=UTC)
    assert alert.fingerprint == Fingerprint(fingerprint)
    assert alert.generator_url.startswith(generator_url)


def test_each_alert_keeps_its_own_status():
    payload = fixture("alertmanager_firing_critical.json")
    resolved = dict(payload["alerts"][0], status="resolved")
    payload["alerts"].append(resolved)
    alerts = AlertmanagerPayload.model_validate(payload).to_alerts()
    assert [a.status for a in alerts] == [Status.FIRING, Status.RESOLVED]


def test_an_alert_without_a_fingerprint_gets_a_derived_one():
    payload = {"alerts": [{"labels": {"alertname": "X", "namespace": "shop"}}]}
    (alert,) = AlertmanagerPayload.model_validate(payload).to_alerts()
    assert alert.fingerprint == Fingerprint.of("X", {"alertname": "X", "namespace": "shop"})
    assert alert.status is Status.FIRING
    assert alert.starts_at.tzinfo is not None


def test_an_alert_needs_labels():
    with pytest.raises(ValidationError):
        AlertmanagerPayload.model_validate({"alerts": [{"annotations": {}}]})


def test_a_generic_alert_becomes_an_alert():
    alert = GenericAlert.model_validate(fixture("generic_firing_critical.json")).to_alert()
    assert alert.title == "Error rate above 5% on knock-knock"
    assert alert.severity is Severity.CRITICAL
    assert alert.status is Status.FIRING
    assert alert.labels == {"namespace": "knock-knock", "alertname": "HighErrorRate"}
    assert alert.starts_at == datetime(2026, 10, 24, 3, 0, tzinfo=UTC)
    assert alert.fingerprint == Fingerprint.of(alert.title, alert.labels)


def test_a_generic_alert_needs_only_a_title():
    alert = GenericAlert.model_validate({"title": "Disk full"}).to_alert()
    assert alert.severity is Severity.WARNING
    assert alert.labels == {}


def test_a_generic_alert_needs_a_title():
    with pytest.raises(ValidationError):
        GenericAlert.model_validate({"title": ""})
