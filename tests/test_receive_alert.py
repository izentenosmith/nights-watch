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


def receive(notifiers, alerts, **kwargs):
    return asyncio.run(ReceiveAlert(ROUTER, POLICY, notifiers, **kwargs)(alerts))


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
    (record,) = [r for r in caplog.records if "Disk full" in r.getMessage()]
    assert record.levelno == logging.INFO and "shop" in record.getMessage()


def test_an_alert_for_an_app_with_no_channel_configured_is_a_warning(caplog):
    unconfigured = NotificationPolicy({c: ChannelSettings(None, Severity.WARNING) for c in Channel}, {})
    asyncio.run(ReceiveAlert(ROUTER, unconfigured, {})([alert()]))
    (record,) = [r for r in caplog.records if "Disk full" in r.getMessage()]
    assert record.levelno == logging.WARNING
    assert "no channel is configured" in record.getMessage()


def test_a_single_match_logs_no_routing_warning(caplog):
    receive({c: SlowNotifier(0) for c in Channel}, [alert()])
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_resolved_and_unmatched_alerts_are_logged(caplog):
    caplog.set_level(logging.INFO)
    receive({}, [alert(status=Status.RESOLVED), alert(ns="blog")])
    messages = [r.getMessage() for r in caplog.records]
    assert any("resolved" in m for m in messages)
    assert any("matches no watched app" in m for m in messages)
