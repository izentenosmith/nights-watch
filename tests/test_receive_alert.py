import asyncio
import logging
import time
from datetime import UTC, datetime

from nights_watch.catalog.domain import AlertRouter, LabelMatcher, WatchedApp
from nights_watch.notification.domain import Channel, ChannelSettings, NotificationPolicy
from nights_watch.triage.application.receive_alert import ReceiveAlert
from nights_watch.triage.domain.alert import Alert, Fingerprint, Severity, Status

ROUTER = AlertRouter([WatchedApp("shop", LabelMatcher({"ns": "shop"}))])
POLICY = NotificationPolicy({c: ChannelSettings(f"{c}-dest", Severity.WARNING) for c in Channel}, {})


class SlowNotifier:
    def __init__(self, seconds: float):
        self.seconds = seconds
        self.sent = 0

    async def send(self, message, destination):
        await asyncio.sleep(self.seconds)
        self.sent += 1


def alert(severity=Severity.CRITICAL, status=Status.FIRING, **labels):
    labels = labels or {"ns": "shop"}
    return Alert("Disk full", severity, labels, status, datetime.now(UTC), Fingerprint("f"))


def receive(notifiers, alerts, **kwargs):
    return asyncio.run(ReceiveAlert(ROUTER, POLICY, notifiers, **kwargs)(alerts))


def test_sends_to_several_channels_run_at_the_same_time():
    notifiers = {c: SlowNotifier(0.2) for c in Channel}
    started = time.perf_counter()
    (outcome,) = receive(notifiers, [alert()])
    assert time.perf_counter() - started < 0.4
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
    assert any("shop" in r.getMessage() and "Disk full" in r.getMessage() for r in caplog.records)


def test_a_single_match_logs_no_routing_warning(caplog):
    receive({c: SlowNotifier(0) for c in Channel}, [alert()])
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_resolved_and_unmatched_alerts_are_logged(caplog):
    caplog.set_level(logging.INFO)
    receive({}, [alert(status=Status.RESOLVED), alert(ns="blog")])
    messages = [r.getMessage() for r in caplog.records]
    assert any("resolved" in m for m in messages)
    assert any("matches no watched app" in m for m in messages)
