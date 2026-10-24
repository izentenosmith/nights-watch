"""Anti-corruption layer: each source's payload stops here and becomes an Alert."""

from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, model_validator


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


def _assert_utf8(value) -> None:
    if isinstance(value, str):
        value.encode("utf-8")  # a lone surrogate (valid JSON, not valid text) raises here
    elif isinstance(value, dict):
        for key, item in value.items():
            _assert_utf8(key)
            _assert_utf8(item)
    elif isinstance(value, list):
        for item in value:
            _assert_utf8(item)


class Payload(BaseModel):
    """Base for inbound payloads: every string must be text we can store and send."""

    @model_validator(mode="before")
    @classmethod
    def _text_only(cls, data):
        try:
            _assert_utf8(data)
        except UnicodeEncodeError:
            raise ValueError("payload holds text that isn't valid Unicode") from None
        return data
