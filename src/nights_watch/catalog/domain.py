"""Watched apps and which one an alert belongs to. Routing looks at labels only."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class LabelMatcher:
    labels: Mapping[str, str]

    def matches(self, labels: Mapping[str, str]) -> bool:
        return all(labels.get(k) == v for k, v in self.labels.items())


@dataclass(frozen=True)
class WatchedApp:
    name: str
    matcher: LabelMatcher


class AlertRouter:
    def __init__(self, apps: Iterable[WatchedApp]):
        self._apps = sorted(apps, key=lambda a: a.name)

    def candidates(self, labels: Mapping[str, str]) -> list[WatchedApp]:
        """Every matching app, best first: most matchers wins, a tie goes to the first by name.

        The alert belongs to the first one; more than one is worth a warning.
        """
        matching = [a for a in self._apps if a.matcher.matches(labels)]
        return sorted(matching, key=lambda a: -len(a.matcher.labels))
