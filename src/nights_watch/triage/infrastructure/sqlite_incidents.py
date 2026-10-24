"""IncidentRepository on SQLite: one row per incident, the aggregate stored whole as JSON.

The indexed columns are only what the queries filter on; everything else lives in the document,
so the aggregate can grow (M1b's diagnosis) without a schema change.
"""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from nights_watch.triage.domain.alert import Severity
from nights_watch.triage.domain.incident import AlertRecord, Incident, IncidentStatus, Vote

SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id            TEXT PRIMARY KEY,
    app           TEXT NOT NULL,
    group_key     TEXT NOT NULL,
    status        TEXT NOT NULL,
    last_alert_at TEXT NOT NULL,
    document      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS incidents_by_group ON incidents (app, group_key, status, last_alert_at);
"""


class SchemaError(Exception):
    pass


class SqliteIncidents:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        # One event-loop thread uses it, and the use case serializes writes under its lock.
        self._db = sqlite3.connect(path, check_same_thread=False)
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:  # new, or a creation a crash cut short: every statement is idempotent
            with self._db:
                self._db.executescript(SCHEMA)
                self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        elif version != SCHEMA_VERSION:
            raise SchemaError(
                f"{path.name}: schema version {version}, this build knows {SCHEMA_VERSION}"
            )

    def get(self, incident_id: str) -> Incident | None:
        return self._one("SELECT document FROM incidents WHERE id = ?", incident_id)

    def find_open(self, app: str, group_key: str) -> Incident | None:
        return self._one(
            "SELECT document FROM incidents WHERE app = ? AND group_key = ? AND status != ?"
            " ORDER BY last_alert_at DESC LIMIT 1",
            app, group_key, str(IncidentStatus.RESOLVED),
        )

    def find_firing(self, app: str, fingerprint: str) -> Incident | None:
        return self._one(
            "SELECT document FROM incidents, json_each(incidents.document, '$.alerts') AS alert"
            " WHERE app = ? AND status != ? AND alert.key = ?"
            " AND json_extract(alert.value, '$.firing')"
            " ORDER BY last_alert_at DESC LIMIT 1",
            app, str(IncidentStatus.RESOLVED), fingerprint,
        )

    def save(self, incident: Incident) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO incidents (id, app, group_key, status, last_alert_at, document)"
                " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO UPDATE SET"
                " status = excluded.status, last_alert_at = excluded.last_alert_at,"
                " document = excluded.document",
                (incident.id, incident.app, incident.group_key, str(incident.status),
                 _time(incident.last_alert_at), json.dumps(_document(incident))),
            )

    def _one(self, query: str, *params) -> Incident | None:
        row = self._db.execute(query, params).fetchone()
        return _incident(json.loads(row[0])) if row else None


def _time(moment: datetime) -> str:
    # One format and one zone, so the text columns sort in time order
    return moment.astimezone(UTC).isoformat()


def _document(incident: Incident) -> dict:
    return {
        "id": incident.id,
        "app": incident.app,
        "group_key": incident.group_key,
        "opened_at": _time(incident.opened_at),
        "last_alert_at": _time(incident.last_alert_at),
        "status": str(incident.status),
        "notified": sorted(incident.notified),
        "diagnosed": incident.diagnosed,
        "feedback": incident.feedback and str(incident.feedback),
        "alerts": {
            fingerprint: {
                "title": record.title,
                "severity": str(record.severity),
                "firing": record.firing,
                "first_seen": _time(record.first_seen),
                "last_seen": _time(record.last_seen),
            }
            for fingerprint, record in incident.alerts.items()
        },
    }


def _incident(document: dict) -> Incident:
    return Incident(
        id=document["id"],
        app=document["app"],
        group_key=document["group_key"],
        opened_at=datetime.fromisoformat(document["opened_at"]),
        last_alert_at=datetime.fromisoformat(document["last_alert_at"]),
        status=IncidentStatus[document["status"].upper()],
        notified=set(document["notified"]),
        diagnosed=document["diagnosed"],
        feedback=Vote(document["feedback"]) if document["feedback"] else None,
        alerts={
            fingerprint: AlertRecord(
                fingerprint,
                alert["title"],
                Severity.parse(alert["severity"]),
                alert["firing"],
                datetime.fromisoformat(alert["first_seen"]),
                datetime.fromisoformat(alert["last_seen"]),
            )
            for fingerprint, alert in document["alerts"].items()
        },
    )
