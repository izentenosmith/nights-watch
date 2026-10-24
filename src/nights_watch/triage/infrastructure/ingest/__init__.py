"""Anti-corruption layer: each source's payload stops here and becomes an Alert."""

from datetime import UTC, datetime


def utc(moment: datetime | None) -> datetime:
    """In UTC; a time without a zone is read as UTC, a missing one is now."""
    if moment is None:
        return datetime.now(UTC)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)
