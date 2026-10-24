"""Alert: what came in, in the domain's terms, whatever format the source used."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum, StrEnum


class Severity(IntEnum):
    INFO = 1
    WARNING = 2
    CRITICAL = 3

    @classmethod
    def parse(cls, label: str | None) -> Severity:
        """The `severity` label, any case; missing or unknown counts as warning."""
        try:
            return cls[(label or "").strip().upper()]
        except KeyError:
            return cls.WARNING

    def __str__(self) -> str:
        return self.name.lower()


class Status(StrEnum):
    FIRING = "firing"
    RESOLVED = "resolved"


@dataclass(frozen=True)
class Fingerprint:
    value: str

    @classmethod
    def of(cls, title: str, labels: Mapping[str, str]) -> Fingerprint:
        """For sources that don't send one: stable for the same title and labels."""
        canonical = json.dumps([title, sorted(labels.items())])
        return cls(hashlib.sha256(canonical.encode()).hexdigest()[:16])


@dataclass(frozen=True)
class Alert:
    title: str
    severity: Severity
    labels: Mapping[str, str]
    status: Status
    starts_at: datetime
    fingerprint: Fingerprint
    generator_url: str | None = None
