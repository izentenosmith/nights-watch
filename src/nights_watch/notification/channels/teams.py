"""Teams Workflows webhook: an Adaptive Card wrapped in a message.

The old Office 365 connector webhooks are being retired; Workflows expects this envelope.
"""

import httpx

from nights_watch.notification.channels import PLACEHOLDER, headline, post
from nights_watch.notification.domain import Message
from nights_watch.triage.domain.alert import Severity


def render(message: Message) -> dict:
    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            {
                "type": "TextBlock",
                "text": headline(message),
                "weight": "Bolder",
                "size": "Medium",
                "wrap": True,
                "color": "Attention" if message.severity is Severity.CRITICAL else "Default",
            },
            {
                "type": "FactSet",
                "facts": [
                    {"title": "Severity", "value": str(message.severity)},
                    {"title": "App", "value": message.app},
                    {"title": "Started", "value": f"{message.starts_at:%Y-%m-%d %H:%M UTC}"},
                ],
            },
            {"type": "TextBlock", "text": PLACEHOLDER, "isSubtle": True, "wrap": True},
        ],
    }
    actions = [
        {"type": "Action.OpenUrl", "title": title, "url": url}
        for title, url in (("Open the run", message.run_url), ("Source", message.generator_url))
        if url
    ]
    if actions:
        card["actions"] = actions
    return {
        "type": "message",
        "attachments": [
            {"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": card}
        ],
    }


class TeamsNotifier:
    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def send(self, message: Message, destination: str) -> None:
        await post(self._client, destination, render(message))
