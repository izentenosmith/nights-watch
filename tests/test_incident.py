from datetime import UTC, datetime, timedelta

import pytest

from nights_watch.triage.domain.alert import Alert, Fingerprint, Severity, Status
from nights_watch.triage.domain.incident import (
    FeedbackNotAllowed,
    Incident,
    IncidentStatus,
    NotThisIncident,
    StatusError,
    UnknownAlert,
    Vote,
    group_key_of,
)

T0 = datetime(2026, 10, 24, 3, 0, tzinfo=UTC)
WINDOW = timedelta(minutes=5)


def alert(pod="a", severity=Severity.CRITICAL, alertname="HighErrorRate", title="Error rate up"):
    labels = {"namespace": "shop", "pod": pod} | ({"alertname": alertname} if alertname else {})
    return Alert(title, severity, labels, Status.FIRING, T0, Fingerprint(f"fp-{pod}"))


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def test_the_group_is_the_alertname_or_else_the_title():
    assert group_key_of(alert()) == "HighErrorRate"
    assert group_key_of(alert(alertname=None, title="Disk full")) == "Disk full"
    assert group_key_of(alert(alertname="  ", title="Disk full")) == "Disk full"


def test_an_incident_opens_with_its_first_alert():
    incident = Incident.open(alert(), "shop", T0)
    assert len(incident.id) == 32
    assert incident.app == "shop" and incident.group_key == "HighErrorRate"
    assert incident.status is IncidentStatus.OPEN
    assert incident.severity is Severity.CRITICAL
    assert list(incident.alerts) == ["fp-a"]
    assert incident.opened_at == incident.last_alert_at == T0


def test_ids_are_unguessable():
    assert Incident.open(alert(), "shop", T0).id != Incident.open(alert(), "shop", T0).id


@pytest.mark.parametrize(
    "candidate, now, accepted",
    [
        (alert("b"), at(4), True),
        (alert("b"), at(5), True),
        (alert("b"), at(5.01), False),
        (alert("b", alertname="Other"), at(1), False),
    ],
    ids=["inside the window", "at its edge", "past it", "another rule"],
)
def test_an_incident_accepts_alerts_of_its_group_inside_the_window(candidate, now, accepted):
    assert Incident.open(alert(), "shop", T0).accepts(candidate, now, WINDOW) is accepted


def test_the_window_slides_from_the_latest_alert():
    incident = Incident.open(alert("a"), "shop", T0)
    incident.attach(alert("b"), at(4))
    assert incident.accepts(alert("c"), at(8), WINDOW)
    assert incident.last_alert_at == at(4)


def test_a_resolved_incident_accepts_nothing():
    incident = Incident.open(alert(), "shop", T0)
    incident.resolve("fp-a", at(1))
    assert not incident.accepts(alert("b"), at(2), WINDOW)


def test_attaching_returns_the_previous_severity_and_the_severity_is_the_highest():
    incident = Incident.open(alert("a", Severity.WARNING), "shop", T0)
    assert incident.attach(alert("b", Severity.CRITICAL), at(1)) is Severity.WARNING
    assert incident.severity is Severity.CRITICAL
    assert incident.attach(alert("c", Severity.INFO), at(2)) is Severity.CRITICAL
    assert incident.severity is Severity.CRITICAL


def test_attaching_the_same_alert_again_keeps_one_record():
    incident = Incident.open(alert("a"), "shop", T0)
    incident.attach(alert("a"), at(1))
    assert list(incident.alerts) == ["fp-a"]
    assert incident.alerts["fp-a"].last_seen == at(1)


def test_it_resolves_once_every_alert_has():
    incident = Incident.open(alert("a"), "shop", T0)
    incident.attach(alert("b"), at(1))
    assert incident.resolve("fp-a", at(2)) is False
    assert incident.status is IncidentStatus.OPEN
    assert incident.resolve("fp-b", at(3)) is True
    assert incident.status is IncidentStatus.RESOLVED


def test_a_resolved_alert_that_fires_again_is_firing():
    incident = Incident.open(alert("a"), "shop", T0)
    incident.attach(alert("b"), at(1))
    incident.resolve("fp-a", at(2))
    incident.attach(alert("a"), at(3))
    assert incident.alerts["fp-a"].firing
    assert incident.resolve("fp-b", at(4)) is False


@pytest.mark.parametrize(
    "path",
    [
        ["investigating", "diagnosed", "resolved"],
        ["resolved"],
        ["diagnosed"],
        ["investigating", "investigating"],
    ],
)
def test_status_only_moves_forward_and_may_skip(path):
    incident = Incident.open(alert(), "shop", T0)
    for status in path:
        incident.advance_to(IncidentStatus[status.upper()])
    assert str(incident.status) == path[-1]


@pytest.mark.parametrize(
    "first, then", [("investigating", "open"), ("resolved", "diagnosed"), ("diagnosed", "open")]
)
def test_status_never_moves_back(first, then):
    incident = Incident.open(alert(), "shop", T0)
    incident.advance_to(IncidentStatus[first.upper()])
    with pytest.raises(StatusError):
        incident.advance_to(IncidentStatus[then.upper()])


def test_attaching_to_a_resolved_incident_is_refused():
    incident = Incident.open(alert(), "shop", T0)
    incident.resolve("fp-a", at(1))
    with pytest.raises(StatusError):
        incident.attach(alert("b"), at(2))


@pytest.mark.parametrize("status", ["open", "investigating"])
def test_feedback_is_refused_before_a_diagnosis(status):
    incident = Incident.open(alert(), "shop", T0)
    incident.advance_to(IncidentStatus[status.upper()])
    with pytest.raises(FeedbackNotAllowed):
        incident.record_feedback(Vote.UP)


def test_feedback_is_refused_when_resolved_without_a_diagnosis():
    incident = Incident.open(alert(), "shop", T0)
    incident.resolve("fp-a", at(1))
    with pytest.raises(FeedbackNotAllowed):
        incident.record_feedback(Vote.UP)


def test_feedback_after_a_diagnosis_is_kept_and_a_later_vote_replaces_it():
    incident = Incident.open(alert(), "shop", T0)
    incident.advance_to(IncidentStatus.DIAGNOSED)
    incident.record_feedback(Vote.UP)
    incident.advance_to(IncidentStatus.RESOLVED)
    incident.record_feedback(Vote.DOWN)
    assert incident.feedback is Vote.DOWN


def test_an_alert_still_firing_in_the_incident_is_accepted_whatever_the_window():
    incident = Incident.open(alert("a"), "shop", T0)
    assert incident.accepts(alert("a"), at(240), WINDOW)  # Alertmanager's 4 h reminder
    assert not incident.accepts(alert("b"), at(240), WINDOW)


def test_an_alert_that_resolved_is_not_kept_past_the_window():
    incident = Incident.open(alert("a"), "shop", T0)
    incident.attach(alert("b"), T0)
    incident.resolve("fp-a", at(1))
    assert not incident.accepts(alert("a"), at(60), WINDOW)


def test_it_knows_which_alerts_are_firing():
    incident = Incident.open(alert("a"), "shop", T0)
    incident.attach(alert("b"), at(1))
    incident.resolve("fp-a", at(2))
    assert not incident.firing("fp-a") and incident.firing("fp-b") and not incident.firing("fp-z")


def test_attaching_an_alert_of_another_group_is_refused():
    incident = Incident.open(alert("a"), "shop", T0)
    with pytest.raises(NotThisIncident):
        incident.attach(alert("b", alertname="Other"), at(1))


def test_resolving_an_alert_it_doesnt_hold_is_refused():
    incident = Incident.open(alert("a"), "shop", T0)
    with pytest.raises(UnknownAlert):
        incident.resolve("fp-z", at(1))


def test_resolving_again_changes_nothing():
    incident = Incident.open(alert("a"), "shop", T0)
    assert incident.resolve("fp-a", at(1)) is True
    assert incident.resolve("fp-a", at(2)) is False
    assert incident.alerts["fp-a"].last_seen == at(1)


@pytest.mark.parametrize("step", ["open", "attach", "resolve"])
def test_times_without_a_zone_are_refused(step):
    naive = T0.replace(tzinfo=None)
    incident = Incident.open(alert("a"), "shop", T0)
    with pytest.raises(ValueError, match="time zone"):
        if step == "open":
            Incident.open(alert("a"), "shop", naive)
        elif step == "attach":
            incident.attach(alert("b"), naive)
        else:
            incident.resolve("fp-a", naive)


def test_channels_told_are_remembered_and_can_be_released():
    incident = Incident.open(alert("a"), "shop", T0)
    assert incident.notified == set()
    incident.mark_notified({"slack", "teams"})
    incident.unmark_notified({"teams"})
    assert incident.notified == {"slack"}
