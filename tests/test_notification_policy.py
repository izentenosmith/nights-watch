import pytest

from nights_watch.notification.domain import (
    Channel,
    ChannelOverride,
    ChannelSettings,
    Delivery,
    NotificationPolicy,
)
from nights_watch.triage.domain.alert import Severity

SLACK, TEAMS, WHATSAPP = Channel.SLACK, Channel.TEAMS, Channel.WHATSAPP
WARNING, CRITICAL = Severity.WARNING, Severity.CRITICAL

ALL_GLOBAL = {
    SLACK: ChannelSettings("slack-hook", WARNING),
    TEAMS: ChannelSettings("teams-hook", WARNING),
    WHATSAPP: ChannelSettings("+wa", CRITICAL),
}


def policy(globals_=None, **overrides):
    return NotificationPolicy(ALL_GLOBAL if globals_ is None else globals_, overrides)


def channels(deliveries):
    return sorted(d.channel for d in deliveries)


@pytest.mark.parametrize(
    "severity, expected",
    [
        (Severity.INFO, []),
        (WARNING, [SLACK, TEAMS]),
        (CRITICAL, [SLACK, TEAMS, WHATSAPP]),
    ],
)
def test_a_channel_gets_alerts_at_or_above_its_floor(severity, expected):
    assert channels(policy().deliveries("shop", severity)) == expected


def test_a_channel_without_a_destination_is_disabled():
    p = policy({SLACK: ChannelSettings(None, WARNING)})
    assert p.deliveries("shop", CRITICAL) == []
    assert p.view("shop")[SLACK].enabled is False


def test_a_channel_without_credentials_is_disabled_even_with_a_destination():
    p = policy({WHATSAPP: ChannelSettings("+wa", CRITICAL, has_credentials=False)})
    assert p.deliveries("shop", CRITICAL) == []
    assert p.view("shop")[WHATSAPP].enabled is False


def test_a_channel_missing_from_the_settings_is_disabled():
    assert policy({}).view("shop")[TEAMS].enabled is False


def test_an_app_destination_wins_over_the_global_one():
    p = policy(shop={SLACK: ChannelOverride(destination="shop-hook")})
    assert Delivery(SLACK, "shop-hook") in p.deliveries("shop", CRITICAL)
    assert Delivery(SLACK, "slack-hook") in p.deliveries("blog", CRITICAL)


def test_an_app_destination_enables_a_channel_only_for_that_app():
    p = policy({SLACK: ChannelSettings(None, WARNING)}, shop={SLACK: ChannelOverride("shop-hook")})
    assert p.view("shop")[SLACK].enabled is True
    assert p.view("blog")[SLACK].enabled is False


def test_an_app_floor_wins_over_the_global_floor():
    p = policy(shop={WHATSAPP: ChannelOverride(min_severity=WARNING)})
    assert WHATSAPP in channels(p.deliveries("shop", WARNING))
    assert WHATSAPP not in channels(p.deliveries("blog", WARNING))


def test_an_override_without_a_floor_inherits_the_global_floor():
    p = policy(
        {SLACK: ChannelSettings("slack-hook", CRITICAL)},
        shop={SLACK: ChannelOverride(destination="shop-hook")},
    )
    assert p.view("shop")[SLACK].min_severity is CRITICAL
    assert p.deliveries("shop", WARNING) == []


def test_the_view_lists_every_channel_with_its_floor():
    view = policy().view("shop")
    assert {c: (v.enabled, v.min_severity) for c, v in view.items()} == {
        SLACK: (True, WARNING),
        TEAMS: (True, WARNING),
        WHATSAPP: (True, CRITICAL),
    }


@pytest.mark.parametrize(
    "old, new, expected",
    [
        (WARNING, CRITICAL, [WHATSAPP]),
        (Severity.INFO, WARNING, [SLACK, TEAMS]),
        (Severity.INFO, CRITICAL, [SLACK, TEAMS, WHATSAPP]),
        (CRITICAL, CRITICAL, []),
        (CRITICAL, WARNING, []),
    ],
    ids=["warning to critical", "info to warning", "info to critical", "no change", "lower"],
)
def test_a_rising_severity_newly_reaches_only_the_channels_it_crosses(old, new, expected):
    assert channels(policy().newly_reached("shop", old, new)) == expected


def test_newly_reached_uses_the_app_destination():
    p = policy(shop={WHATSAPP: ChannelOverride(destination="+shop")})
    assert p.newly_reached("shop", WARNING, CRITICAL) == [Delivery(WHATSAPP, "+shop")]
