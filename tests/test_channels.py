import json
from datetime import UTC, datetime

from nights_watch.notification.channels import slack
from nights_watch.notification.domain import Message
from nights_watch.triage.domain.alert import Severity


def message(title="Disk full", url=None):
    return Message(Severity.CRITICAL, "shop", title, datetime(2026, 10, 24, 3, tzinfo=UTC), url)


def mrkdwn_texts(payload):
    texts = [payload["text"]]
    for block in payload["blocks"]:
        texts += [f["text"] for f in block.get("fields", [])]
        texts += [e["text"] for e in block.get("elements", [])]
    return texts


def test_slack_stays_within_block_kit_limits():
    payload = slack.render(message("t" * 500, "https://prometheus/graph?g0.expr=" + "x" * 5000))
    (header,) = [b for b in payload["blocks"] if b["type"] == "header"]
    assert len(header["text"]["text"]) <= 150
    for block in payload["blocks"]:
        for element in block.get("elements", []):
            assert len(element["text"]) <= 3000
        for field in block.get("fields", []):
            assert len(field["text"]) <= 2000


def test_slack_escapes_alert_text_in_every_mrkdwn_field():
    payload = slack.render(message("<!channel> rate <5% & >2%", "https://x/?a=1&b=<2>"))
    for text in mrkdwn_texts(payload):
        assert "<!channel>" not in text
        assert "<5%" not in text and "<2>" not in text
    assert "&lt;!channel&gt; rate &lt;5% &amp; &gt;2%" in payload["text"]
    assert "https://x/?a=1&amp;b=&lt;2&gt;" in json.dumps(payload)
