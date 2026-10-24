"""WhatsApp Cloud API, template message.

Messages started by us (outside the 24-hour window) must use a template approved by Meta,
so the text is fixed there. Its body takes three parameters, in order: {{1}} severity,
{{2}} app, {{3}} alert title.
"""

import httpx

from nights_watch.notification.channels import post
from nights_watch.notification.domain import Message

GRAPH_API = "https://graph.facebook.com/v23.0"
MAX_PARAM_LENGTH = 200


def _param(text: str) -> dict:
    # Meta rejects parameters with newlines, tabs or more than four consecutive spaces.
    flat = " ".join(text.split())
    if len(flat) > MAX_PARAM_LENGTH:
        flat = flat[: MAX_PARAM_LENGTH - 1] + "…"
    return {"type": "text", "text": flat}


def render(message: Message, to: str, template: str, language: str) -> dict:
    return {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template,
            "language": {"code": language},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        _param(str(message.severity)),
                        _param(message.app),
                        _param(message.title),
                    ],
                }
            ],
        },
    }


class WhatsAppNotifier:
    def __init__(
        self, client: httpx.AsyncClient, token: str, phone_id: str, template: str, language: str
    ):
        self._client = client
        self._url = f"{GRAPH_API}/{phone_id}/messages"
        self._headers = {"Authorization": f"Bearer {token}"}
        self._template = template
        self._language = language

    async def send(self, message: Message, destination: str) -> None:
        payload = render(message, destination, self._template, self._language)
        await post(self._client, self._url, payload, headers=self._headers)
