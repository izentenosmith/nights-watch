"""Slack incoming webhook, Block Kit."""

import httpx

from nights_watch.notification.channels import PLACEHOLDER, post
from nights_watch.notification.domain import Message


def _escape(text: str) -> str:
    # mrkdwn treats these three as control characters
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render(message: Message) -> dict:
    context = f"Started {message.starts_at:%Y-%m-%d %H:%M %Z}"
    if message.generator_url:
        context += f" · <{message.generator_url}|source>"
    return {
        "text": f"[{message.severity}] {message.app}: {message.title}",
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": message.title[:150]}},
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Severity*\n{message.severity}"},
                    {"type": "mrkdwn", "text": f"*App*\n{_escape(message.app)}"},
                ],
            },
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"{context} · {PLACEHOLDER}"}],
            },
        ],
    }


class SlackNotifier:
    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def send(self, message: Message, destination: str) -> None:
        await post(self._client, destination, render(message))
