"""IncidentRepository in memory, for tests. Hands out copies, like a real store would."""

import copy

from nights_watch.triage.domain.incident import Incident, IncidentStatus


class MemoryIncidents:
    def __init__(self):
        self._incidents: dict[str, Incident] = {}

    def get(self, incident_id: str) -> Incident | None:
        return copy.deepcopy(self._incidents.get(incident_id))

    def find_open(self, app: str, group_key: str) -> Incident | None:
        return self._latest(lambda i: i.app == app and i.group_key == group_key)

    def find_holding(self, app: str, fingerprint: str) -> Incident | None:
        return self._latest(lambda i: i.app == app and i.holds(fingerprint))

    def save(self, incident: Incident) -> None:
        self._incidents[incident.id] = copy.deepcopy(incident)

    def _latest(self, matches) -> Incident | None:
        candidates = [
            i for i in self._incidents.values()
            if i.status is not IncidentStatus.RESOLVED and matches(i)
        ]
        latest = max(candidates, key=lambda i: i.last_alert_at, default=None)
        return copy.deepcopy(latest)
