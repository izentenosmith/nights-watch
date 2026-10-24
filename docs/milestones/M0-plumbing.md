# M0 — Plumbing

**Goal:** an alert goes in, a placeholder message comes out on every enabled
channel above its severity floor. No LLM, no investigation, no state. This proves
the webhooks, the catalog routing and the channel credentials before any AI exists.

**Depends on:** nothing. Runs with no observability stack and no target app.

## Scope

| In | Out (later milestone) |
|---|---|
| FastAPI app, `Dockerfile`, `compose.yaml` | incidents, grouping, persistence (M1) |
| Alertmanager / Grafana and generic inputs | the agent and its tools (M1) |
| catalog domain: `WatchedApp`, `AlertRouter` | `/runs/{id}` report page (M1) |
| notification context: policy + Slack, Teams, WhatsApp | live sources (M2) |
| startup check of the env vars the catalog references | |
| `/metrics` with the unmatched-alerts counter | |

## Deliverables

```
src/nights_watch/
├── catalog/domain.py                    WatchedApp, LabelMatcher, AlertRouter (from AppFile)
├── triage/domain/alert.py               Alert, Severity, Fingerprint  (value objects)
├── triage/application/receive_alert.py  normalize → route → notify (placeholder)
├── triage/infrastructure/ingest/
│   ├── alertmanager.py                  Alertmanager v4 webhook (also Grafana) → Alert
│   └── generic.py                       {title, severity, labels, started_at} → Alert
├── notification/domain.py               Channel, NotificationPolicy, Message
├── notification/channels/               slack.py · teams.py · whatsapp.py
├── api/alerts.py · api/health.py        POST /alerts/{alertmanager,generic}, GET /healthz, GET /apps, GET /metrics
└── bootstrap.py                         env → settings → wiring; startup checks
Dockerfile · compose.yaml
```

## Design notes

- **Inputs are an anti-corruption layer.** The Alertmanager v4 payload (`status`,
  `commonLabels`, `alerts[]` with `labels`, `annotations`, `startsAt`,
  `fingerprint`, `generatorURL`) stops in `ingest/`. Grafana's webhook is a
  superset of it: ignore unknown fields, don't write a second parser.
- **One request can carry several alerts.** Each is routed on its own.
- **Routing:** an alert belongs to an app when all of the app's `match` labels
  equal the alert's labels. More than one match → the app with the most matchers
  wins, and a warning is logged. No match → drop, log, and increment
  `nightswatch_unmatched_alerts_total`.
- **Severity** comes from the `severity` label; missing or unknown → `warning`.
- **`status: resolved`** → log only, no message in M0.
- **Notification policy:** a channel gets a message when it's enabled (its env
  vars are set) and the alert's severity ≥ its floor. A per-app `notify` entry in
  the catalog overrides the global floor and, if it names an `_env`, the destination.
- **Channel payloads:**
  - Slack: incoming webhook, Block Kit.
  - Teams: Workflows webhook, an Adaptive Card wrapped in
    `{"type": "message", "attachments": [...]}`.
  - WhatsApp: Cloud API `POST /{phone_id}/messages` with a **template** message.
    Add `NW_WHATSAPP_TEMPLATE` and `NW_WHATSAPP_TEMPLATE_LANG` to `.env.example`.
- **Config:** `NW_*` come from the environment, over `.env` locally (read with
  `python-dotenv`), and are validated by a pydantic model. `pydantic-settings` was the first
  plan, but it ignores undeclared variables from the environment, which are exactly the
  per-app ones the catalog names, and it always reads `os.environ`, so tests couldn't pass
  their own values.
- **Startup fails loudly** when a catalog file names an env var that isn't set,
  naming the file, the field and the variable.
- **HTTP:** `httpx` everywhere, so tests use `httpx.MockTransport`, never the network.
- **The domain imports nothing from FastAPI, httpx or pydantic-settings.** (A test enforces it.)

## Acceptance criteria

1. `POST /alerts/alertmanager` with a firing `critical` alert labelled
   `namespace=knock-knock` sends one message to each enabled channel whose floor is
   ≤ `critical`, and none to the others.
2. The same request with `severity=warning` skips WhatsApp under the default floors.
3. An alert matching no app sends nothing, returns `202`, and increments
   `nightswatch_unmatched_alerts_total` on `/metrics`.
4. A Grafana alerting payload is accepted by the same route.
5. `POST /alerts/generic` with `{title, severity, labels, started_at}` behaves like 1.
6. A catalog `to_env` / `webhook_env` that isn't set stops the startup with a
   message naming file, field and variable.
7. A channel with no env vars set is never called, and `GET /apps` shows which
   channels are enabled for each app.
8. One failing channel (HTTP 500) doesn't stop the others; the failure is logged.
9. `docker compose up` starts the app with `.env` and `catalog/` mounted.

## Unit tests (CI)

- Alertmanager and Grafana fixture payloads (`tests/fixtures/`) → the right `Alert`s.
- `AlertRouter`: single match, no match, most-matchers wins.
- `NotificationPolicy`: floors, per-app overrides, disabled channels.
- Each channel renders the expected payload (fake transport captures the request).
- Startup check: missing referenced env var → error text.
- Architecture: no `domain` module imports `fastapi`, `httpx`, `langgraph`, `sqlite3`.

## Manual check (local, not CI)

With real webhooks in `.env`:

```bash
docker compose up --build
curl -X POST localhost:8000/alerts/alertmanager -H 'content-type: application/json' \
  -d @tests/fixtures/alertmanager_firing_critical.json
```

**Start the WhatsApp template approval in Meta Business Manager now:** it takes time
and M0 is the first milestone that needs it. Teams and Slack work the same day.

## Done when

All criteria pass, CI is green, the PR is merged into `dev`, and one real message
was seen on each channel you configured.
