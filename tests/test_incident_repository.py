"""One contract, two repositories: whatever the in-memory one does, the SQLite one does too."""

import sqlite3
from datetime import UTC, datetime, timedelta, timezone

import pytest

from nights_watch.triage.domain.alert import Alert, Fingerprint, Severity, Status
from nights_watch.triage.domain.incident import Incident, IncidentStatus, Vote
from nights_watch.triage.infrastructure.memory_incidents import MemoryIncidents
from nights_watch.triage.infrastructure.sqlite_incidents import SchemaError, SqliteIncidents

T0 = datetime(2026, 10, 24, 3, 0, tzinfo=UTC)


@pytest.fixture(params=["memory", "sqlite"])
def repository(request, tmp_path):
    if request.param == "memory":
        return MemoryIncidents()
    return SqliteIncidents(tmp_path / "data" / "nw.sqlite")


def alert(pod="a", alertname="HighErrorRate", severity=Severity.CRITICAL):
    labels = {"alertname": alertname, "pod": pod}
    return Alert("Error <rate> up", severity, labels, Status.FIRING, T0, Fingerprint(f"fp-{pod}"))


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def test_a_saved_incident_comes_back_whole(repository):
    incident = Incident.open(alert("a", severity=Severity.WARNING), "shop", T0)
    incident.attach(alert("b"), at(1))
    incident.mark_notified({"slack", "whatsapp"})
    incident.resolve("fp-a", at(2))
    incident.advance_to(IncidentStatus.DIAGNOSED)
    incident.record_feedback(Vote.DOWN)
    repository.save(incident)
    assert repository.get(incident.id) == incident


def test_an_unknown_id_is_none(repository):
    assert repository.get("0" * 32) is None


def test_saving_again_replaces(repository):
    incident = Incident.open(alert("a"), "shop", T0)
    repository.save(incident)
    incident.attach(alert("b"), at(1))
    repository.save(incident)
    assert list(repository.get(incident.id).alerts) == ["fp-a", "fp-b"]


def test_what_is_returned_is_a_copy(repository):
    incident = Incident.open(alert("a"), "shop", T0)
    repository.save(incident)
    repository.get(incident.id).attach(alert("b"), at(1))
    assert list(repository.get(incident.id).alerts) == ["fp-a"]


def test_find_open_picks_the_latest_unresolved_incident_of_the_group(repository):
    older = Incident.open(alert("a"), "shop", T0)
    newer = Incident.open(alert("b"), "shop", at(10))
    resolved = Incident.open(alert("c"), "shop", at(20))
    resolved.resolve("fp-c", at(21))
    other_rule = Incident.open(alert("d", alertname="Other"), "shop", at(30))
    other_app = Incident.open(alert("e"), "blog", at(40))
    for incident in (older, newer, resolved, other_rule, other_app):
        repository.save(incident)
    assert repository.find_open("shop", "HighErrorRate").id == newer.id
    assert repository.find_open("shop", "Nothing") is None


def test_find_firing_finds_the_unresolved_incident_where_that_alert_fires(repository):
    firing_here = Incident.open(alert("a"), "shop", T0)
    firing_here.attach(alert("b"), at(1))
    resolved_here = Incident.open(alert("c"), "shop", at(2))
    resolved_here.attach(alert("b"), at(3))
    resolved_here.resolve("fp-b", at(4))
    resolved_here.attach(alert("d"), at(10))  # newer than firing_here, but b is resolved here
    done = Incident.open(alert("e"), "shop", at(5))
    done.resolve("fp-e", at(6))
    elsewhere = Incident.open(alert("b"), "blog", at(20))
    for incident in (firing_here, resolved_here, done, elsewhere):
        repository.save(incident)
    assert repository.find_firing("shop", "fp-b").id == firing_here.id
    assert repository.find_firing("shop", "fp-e") is None
    assert repository.find_firing("shop", "fp-z") is None


def test_times_in_other_zones_come_back_as_the_same_moment_and_sort_right(repository):
    santiago = timezone(timedelta(hours=-3))
    earlier = Incident.open(alert("a"), "shop", T0.astimezone(santiago))  # 00:00 -03:00
    later = Incident.open(alert("b"), "shop", at(1))  # 03:01 UTC
    repository.save(later)
    repository.save(earlier)
    assert repository.get(earlier.id).opened_at == T0
    assert repository.find_open("shop", "HighErrorRate").id == later.id


def test_sqlite_keeps_incidents_across_connections(tmp_path):
    path = tmp_path / "nw.sqlite"
    incident = Incident.open(alert(), "shop", T0)
    SqliteIncidents(path).save(incident)
    assert SqliteIncidents(path).get(incident.id) == incident


def test_sqlite_refuses_a_database_with_an_unknown_schema_version(tmp_path):
    path = tmp_path / "future.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 99")
    with pytest.raises(SchemaError, match="future.sqlite"):
        SqliteIncidents(path)


def test_sqlite_finishes_a_schema_a_crash_left_half_made(tmp_path):
    path = tmp_path / "half.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE incidents (id TEXT PRIMARY KEY, app TEXT NOT NULL,"
                           " group_key TEXT NOT NULL, status TEXT NOT NULL,"
                           " last_alert_at TEXT NOT NULL, document TEXT NOT NULL)")
    incident = Incident.open(alert(), "shop", T0)
    SqliteIncidents(path).save(incident)
    assert SqliteIncidents(path).get(incident.id) == incident
