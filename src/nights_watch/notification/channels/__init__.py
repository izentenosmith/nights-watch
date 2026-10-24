"""Notifier adapters. Each renders a Message in its channel's format and POSTs it."""

import httpx

from nights_watch.notification.domain import DeliveryError

PLACEHOLDER = "Placeholder: the investigation arrives in M1."


async def post(client: httpx.AsyncClient, url: str, payload: dict, **kwargs) -> None:
    """POST JSON; any non-2xx or transport error becomes a DeliveryError that names no URL.

    Webhook URLs and tokens are secrets, and httpx puts the URL in its exception messages.
    """
    try:
        response = await client.post(url, json=payload, **kwargs)
    except httpx.HTTPError as e:
        raise DeliveryError(type(e).__name__) from None
    if not response.is_success:
        raise DeliveryError(f"HTTP {response.status_code}")
