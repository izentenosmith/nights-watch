"""What the triage core needs from the outside world, as Protocols the adapters implement."""

from typing import Protocol

from nights_watch.triage.domain.incident import Incident


class IncidentRepository(Protocol):
    def get(self, incident_id: str) -> Incident | None: ...

    def find_open(self, app: str, group_key: str) -> Incident | None:
        """The app's latest unresolved incident of that group."""
        ...

    def find_holding(self, app: str, fingerprint: str) -> Incident | None:
        """The app's latest unresolved incident holding that alert."""
        ...

    def save(self, incident: Incident) -> None: ...
