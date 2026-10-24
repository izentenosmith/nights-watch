"""👍/👎 on a run. The incident decides whether feedback is allowed (only after a diagnosis)."""

import asyncio

from nights_watch.triage.domain.incident import Vote
from nights_watch.triage.domain.ports import IncidentRepository


class UnknownRun(Exception):
    pass


class RecordFeedback:
    def __init__(self, incidents: IncidentRepository, lock: asyncio.Lock):
        self._incidents = incidents
        self._lock = lock  # the same lock as ReceiveAlert: both load, change and save incidents

    async def __call__(self, run_id: str, vote: Vote) -> None:
        """Raises UnknownRun, or FeedbackNotAllowed while there is no diagnosis."""
        async with self._lock:
            incident = self._incidents.get(run_id)
            if incident is None:
                raise UnknownRun(run_id)
            incident.record_feedback(vote)
            self._incidents.save(incident)
