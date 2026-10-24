import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta

from nights_watch.catalog.domain import AlertRouter, LabelMatcher, WatchedApp
from nights_watch.notification.domain import Channel, ChannelSettings, NotificationPolicy
from nights_watch.triage.application.receive_alert import ReceiveAlert
from nights_watch.triage.domain.alert import Alert, Fingerprint, Severity, Status
from nights_watch.triage.infrastructure.memory_incidents import MemoryIncidents

ROUTER = AlertRouter([WatchedApp("shop", LabelMatcher({"ns": "shop"}))])
POLICY = NotificationPolicy({c: ChannelSettings(f"{c}-dest", Severity.WARNING) for c in Channel}, {})


class SlowNotifier:
    in_flight = 0
    most_in_flight = 0

    def __init__(self, seconds: float):
        self.seconds = seconds

    async def send(self, message, destination):
        SlowNotifier.in_flight += 1
        SlowNotifier.most_in_flight = max(SlowNotifier.most_in_flight, SlowNotifier.in_flight)
        try:
            await asyncio.sleep(self.seconds)
        finally:
            SlowNotifier.in_flight -= 1


def alert(severity=Severity.CRITICAL, status=Status.FIRING, **labels):
    labels = labels or {"ns": "shop"}
    return Alert("Disk full", severity, labels, status, datetime.now(UTC), Fingerprint("f"))


def use_case(policy, notifiers, **kwargs):
    defaults = {
        "incidents": MemoryIncidents(),
        "lock": asyncio.Lock(),
        "window": timedelta(minutes=5),
        "clock": lambda: datetime.now(UTC),
        "run_url": lambda run_id: f"https://nw.test/runs/{run_id}",
    }
    return ReceiveAlert(ROUTER, policy, notifiers, **(defaults | kwargs))


def receive(notifiers, alerts, **kwargs):
    return asyncio.run(use_case(POLICY, notifiers, **kwargs)(alerts))


def test_sends_to_several_channels_run_at_the_same_time():
    SlowNotifier.most_in_flight = 0
    (outcome,) = receive({c: SlowNotifier(0.05) for c in Channel}, [alert()])
    assert SlowNotifier.most_in_flight == len(Channel)
    assert sorted(outcome.sent) == sorted(Channel)


def test_a_send_slower_than_the_timeout_is_abandoned_and_logged(caplog):
    notifiers = {c: SlowNotifier(5 if c is Channel.SLACK else 0) for c in Channel}
    started = time.perf_counter()
    (outcome,) = receive(notifiers, [alert()], send_timeout=0.1)
    assert time.perf_counter() - started < 1
    assert outcome.failed == (Channel.SLACK,)
    assert any("slack" in r.getMessage() and "timed out" in r.getMessage() for r in caplog.records)


def test_an_alert_below_every_floor_is_logged(caplog):
    caplog.set_level(logging.INFO)
    (outcome,) = receive({c: SlowNotifier(0) for c in Channel}, [alert(Severity.INFO)])
    assert outcome.sent == ()
    (record,) = [r for r in caplog.records if "floor" in r.getMessage()]
    assert record.levelno == logging.INFO
    assert "shop" in record.getMessage() and "Disk full" in record.getMessage()


def test_an_alert_for_an_app_with_no_channel_configured_is_a_warning(caplog):
    unconfigured = NotificationPolicy({c: ChannelSettings(None, Severity.WARNING) for c in Channel}, {})
    asyncio.run(use_case(unconfigured, {})([alert()]))
    (record,) = [r for r in caplog.records if "Disk full" in r.getMessage()]
    assert record.levelno == logging.WARNING
    assert "no channel is enabled" in record.getMessage()


def test_a_single_match_logs_no_routing_warning(caplog):
    receive({c: SlowNotifier(0) for c in Channel}, [alert()])
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_resolved_and_unmatched_alerts_are_logged(caplog):
    caplog.set_level(logging.INFO)
    receive({}, [alert(status=Status.RESOLVED), alert(ns="blog")])
    messages = [r.getMessage() for r in caplog.records]
    assert any("resolved" in m for m in messages)
    assert any("matches no watched app" in m for m in messages)


class Recorder:
    def __init__(self, fail=False):
        self.messages = []
        self.fail = fail

    async def send(self, message, destination):
        self.messages.append(message)
        if self.fail:
            raise RuntimeError("down")


def run(use_case, *alerts):
    return asyncio.run(use_case(list(alerts)))


def labelled(pod, severity=Severity.CRITICAL, status=Status.FIRING):
    return Alert("Disk full", severity, {"ns": "shop", "alertname": "DiskFull", "pod": pod},
                 status, datetime.now(UTC), Fingerprint(f"fp-{pod}"))


def test_the_outcome_names_what_happened_to_each_alert():
    case = use_case(POLICY, {c: Recorder() for c in Channel})
    (opened,) = run(case, labelled("a", Severity.WARNING))
    (attached,) = run(case, labelled("b", Severity.WARNING))
    (escalated,) = run(case, labelled("c", Severity.CRITICAL))
    (alert_resolved,) = run(case, labelled("a", status=Status.RESOLVED))
    (ignored,) = run(case, labelled("z", status=Status.RESOLVED))
    run(case, labelled("b", status=Status.RESOLVED))
    (resolved,) = run(case, labelled("c", status=Status.RESOLVED))
    assert [o.event for o in (opened, attached, escalated, alert_resolved, ignored, resolved)] == [
        "opened", "attached", "escalated", "alert resolved", "ignored", "resolved"]
    assert {o.incident_id for o in (opened, attached, escalated, alert_resolved, resolved)} == {
        opened.incident_id}
    assert ignored.incident_id is None


def test_a_channel_that_failed_is_told_on_the_next_alert_and_the_others_are_not_told_twice():
    slack, teams = Recorder(fail=True), Recorder()
    case = use_case(POLICY, {Channel.SLACK: slack, Channel.TEAMS: teams,
                             Channel.WHATSAPP: Recorder()})
    run(case, labelled("a", Severity.WARNING))
    slack.fail = False
    (outcome,) = run(case, labelled("b", Severity.WARNING))
    assert outcome.sent == (Channel.SLACK,)
    assert len(slack.messages) == 2 and len(teams.messages) == 1


def test_alerts_of_one_incident_in_one_request_tell_each_channel_once():
    recorders = {c: Recorder() for c in Channel}
    run(use_case(POLICY, recorders), *(labelled(p) for p in "abcd"))
    assert [len(r.messages) for r in recorders.values()] == [1, 1, 1]


def test_an_alert_that_fails_to_store_doesnt_stop_the_others(caplog):
    class Flaky(MemoryIncidents):
        def save(self, incident):
            if "fp-bad" in incident.alerts:
                raise OSError("disk full")
            super().save(incident)

    recorders = {c: Recorder() for c in Channel}
    case = use_case(POLICY, recorders, incidents=Flaky())
    bad = Alert("Disk full", Severity.CRITICAL, {"ns": "shop", "alertname": "Other"},
                Status.FIRING, datetime.now(UTC), Fingerprint("fp-bad"))
    good, failed = run(case, labelled("a"), bad)
    assert good.event == "opened" and len(recorders[Channel.SLACK].messages) == 1
    assert failed.event == "failed"
    assert any(r.levelno == logging.ERROR and "Disk full" in r.getMessage() for r in caplog.records)
