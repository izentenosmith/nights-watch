"""Anti-corruption layer: each source's payload stops here and becomes an Alert."""

from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator


def _to_utc(moment: datetime) -> datetime:
    """A time without a zone is read as UTC."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    try:
        return moment.astimezone(UTC)
    except OverflowError:
        raise ValueError("can't be expressed in UTC") from None


UtcDatetime = Annotated[datetime, AfterValidator(_to_utc)]


def now() -> datetime:
    return datetime.now(UTC)
