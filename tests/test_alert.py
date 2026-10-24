import pytest

from nights_watch.triage.domain.alert import Fingerprint, Severity


@pytest.mark.parametrize(
    "label, severity",
    [
        ("info", Severity.INFO),
        ("warning", Severity.WARNING),
        ("critical", Severity.CRITICAL),
        ("Critical", Severity.CRITICAL),
        (" CRITICAL ", Severity.CRITICAL),
        ("page", Severity.WARNING),
        ("", Severity.WARNING),
        (None, Severity.WARNING),
    ],
)
def test_severity_comes_from_the_label_and_defaults_to_warning(label, severity):
    assert Severity.parse(label) is severity


def test_severities_are_ordered():
    assert Severity.INFO < Severity.WARNING < Severity.CRITICAL


def test_severity_reads_as_its_label():
    assert str(Severity.CRITICAL) == "critical"


def test_fingerprint_does_not_depend_on_label_order():
    a = Fingerprint.of("Disk full", {"namespace": "shop", "app": "api"})
    b = Fingerprint.of("Disk full", {"app": "api", "namespace": "shop"})
    assert a == b
    assert len(a.value) == 16


def test_fingerprint_tells_apart_different_alerts():
    assert Fingerprint.of("Disk full", {"app": "api"}) != Fingerprint.of("Disk full", {"app": "db"})
    assert Fingerprint.of("Disk full", {"app": "api"}) != Fingerprint.of("CPU high", {"app": "api"})
