# Night's Watch: the plan

An alert fires, and the agent investigates using read-only tools: metrics, logs,
traces, recent deploys and runbooks. Within about two minutes it posts a ranked
root-cause hypothesis to Slack and/or Teams and/or WhatsApp, each enabled by its
environment variables.

It's **plug and play**: one container that works with the most common alert
sources and observability backends. It **diagnoses only**: it never changes
anything in the systems it watches.

---

## 1. The contract

### Alerts in

| Input | Route | Notes |
|---|---|---|
| Alertmanager webhook | `POST /alerts/alertmanager` | the de-facto standard |
| Grafana alerting webhook | `POST /alerts/alertmanager` | Grafana's payload is Alertmanager-compatible; one parser |
| Generic JSON | `POST /alerts/generic` | `{title, severity, labels, started_at}` |

Every input is converted into one internal `Alert` (the anti-corruption layer in
`triage/infrastructure/ingest/`). Severity comes from the `severity` label
(`info < warning < critical`); a missing label counts as `warning`.

### Sources (the agent's tools)

A tool exists only when its variable is set. Per-app scoping comes from the catalog.

| Variable | Covers | Tools |
|---|---|---|
| `NW_PROMETHEUS_URL` | Prometheus, Mimir, Thanos, VictoriaMetrics, Amazon Managed Prometheus, Azure Monitor managed Prometheus | `metrics` |
| `NW_LOKI_URL` | Loki | `logs` (error patterns with counts, not raw lines) |
| `NW_KUBECONFIG` or in-cluster | Kubernetes | `k8s_events`, `rollout_history`, `pod_restarts` |
| `NW_GITHUB_TOKEN` | GitHub | `recent_commits`, `recent_workflow_runs` |
| catalog `runbooks.path` | markdown folder | `search_runbooks` (hybrid: keyword + embeddings) |

At startup it pings each configured source and runs each app's selectors once. A
failing source stops the startup; a selector that matches nothing is reported.

### Channels out

| Channel | Enabled by | Severity floor |
|---|---|---|
| Slack | `NW_SLACK_WEBHOOK_URL` | `NW_SLACK_MIN_SEVERITY` (default `warning`) |
| Teams | `NW_TEAMS_WEBHOOK_URL` (Workflows webhook) | `NW_TEAMS_MIN_SEVERITY` (default `warning`) |
| WhatsApp | `NW_WHATSAPP_TOKEN` + `NW_WHATSAPP_PHONE_ID` + `NW_WHATSAPP_TO` | `NW_WHATSAPP_MIN_SEVERITY` (default `critical`) |

One result is rendered per channel (Block Kit, Adaptive Card, plain text). Every
message links to `/runs/{id}`, the full report, which also holds the 👍/👎
feedback, so feedback works the same on every channel.

- **WhatsApp** messages you initiate outside the 24-hour window must use a
  template approved by Meta, and recipients must opt in. The message is a fixed
  template; the detail stays on the report page.
- **Teams**: use a Workflows webhook; the old Office 365 connector webhooks are
  being retired.

---

## 2. The catalog: which apps are watched

One YAML file per app in `catalog/`, loaded from `NW_CATALOG_DIR`. Copy
[`catalog/_template.yaml`](catalog/_template.yaml) to add one;
[`catalog/knock-knock.yaml`](catalog/knock-knock.yaml) is the sample.

- **Only listed apps are investigated.** An alert matching no app is dropped,
  logged and counted in `nightswatch_unmatched_alerts_total`.
- **Matching:** an alert belongs to an app when all its `match` labels match. If
  more than one app matches, the one with the most matchers wins and a warning
  is logged.
- **Scope is enforced in code, not in the prompt.** Tools inject the app's
  selectors themselves; the LLM picks a metric, a component and a time range, never
  raw PromQL or LogQL. An investigation of app A can't read app B's data.
- **No secrets, ever.** Fields ending in `_env` hold a variable name (`NW_...`).
  A unit test fails the build on anything that looks like a secret.
- **A referenced variable that isn't set stops the startup**, naming the file,
  the field and the variable.

Adding an app: the app emits telemetry with stable labels (in its own repo,
nothing Night's Watch specific) → add `catalog/<name>.yaml` → route its alerts here
→ restart → check `GET /apps`. Removing: delete the file; its open incidents close
as `app unwatched`.

knock-knock is a separate repo and knows nothing about Night's Watch. The only
contract is its telemetry labels.

---

## 3. The agent

```
START
  └─► normalize          Alertmanager / Grafana / generic JSON → Alert
  └─► group              same fingerprint already open? attach to that run → END
  └─► gather_basics      code only, no LLM; all configured sources in parallel:
  │                        alert query (30 min) · k8s events/restarts · deploys (2 h)
  └─► investigate ⇄ tools   LLM loop; budget check stops it at 10 calls / 90 s / $0.25
  └─► rank               structured output: ranked hypotheses + checked / not_checked
  └─► verify_evidence    code: every quote must appear in its tool output, or it's dropped
  └─► notify             one result → every enabled channel above its severity floor
END
```

- **LangGraph**, with the SQLite checkpointer: a crash mid-investigation resumes.
- **Tool output is shrunk before the model sees it**: a metric series becomes
  min / max / latest / when and how much it jumped; logs become top error patterns.
- **The report says what it could not check** (`not_checked: ["traces"]`), so a
  missing source never looks like nothing was wrong.
- `NW_MAX_CONCURRENT_INVESTIGATIONS` queues the rest; cost is tracked per app.

### Safety

- **Diagnose only.** Source clients send `GET` only (a test checks each one);
  Kubernetes access is the `view` ClusterRole. The report offers a `next_check`,
  never a command.
- **Logs are untrusted input**: tool output is wrapped as data and never chooses a
  tool by itself.
- **Secrets and personal data are redacted** before anything goes to the LLM and
  before anything is posted.

---

## 4. Design: DDD

Three bounded contexts:

| Context | Its language | Main type |
|---|---|---|
| **catalog** | watched app, matcher, scope, component | `WatchedApp` (read-only) |
| **triage** (the core) | alert, incident, fingerprint, hypothesis, evidence, diagnosis | **`Incident`** (aggregate root) |
| **notification** | channel, severity floor, message, template | `NotificationPolicy` |

The domain speaks operations language. The Game of Thrones name stays on the brand.

`Incident` rules: one open incident per (app, fingerprint group) within the
grouping window; a diagnosis is accepted only if all its evidence passes
`EvidenceVerifier`; feedback only after a diagnosis; status only moves forward
(`open → investigating → diagnosed → resolved`).

```
nights-watch/
├── catalog/                          watched apps (_template.yaml, knock-knock.yaml)
├── src/nights_watch/
│   ├── catalog/
│   │   ├── domain.py                 WatchedApp, LabelMatcher, SourceScope, AlertRouter
│   │   └── yaml_catalog.py           loads + validates NW_CATALOG_DIR
│   ├── triage/
│   │   ├── domain/                   incident.py, diagnosis.py, ports.py (Protocols)
│   │   ├── application/              receive_alert.py, investigate.py, record_feedback.py
│   │   └── infrastructure/
│   │       ├── ingest/               alertmanager.py, generic.py  ← anti-corruption layer
│   │       ├── sources/              prometheus.py, loki.py, k8s.py, github.py, runbooks.py, replay.py
│   │       ├── langgraph_investigator.py   the ONLY module that imports langgraph
│   │       └── sqlite_incidents.py
│   ├── notification/
│   │   ├── domain.py                 Channel, NotificationPolicy, Message
│   │   └── channels/                 slack.py, teams.py, whatsapp.py
│   ├── api/                          FastAPI routers (entry point only, no logic)
│   └── bootstrap.py                  reads env, wires adapters to ports
├── evals/                            scenarios + recorded tool responses (run locally)
└── tests/                            unit tests (run in CI)
```

- **Dependency rule:** domain ← application ← infrastructure and api. A test
  checks that no `domain` module imports `langgraph`, `fastapi`, `httpx` or `sqlite3`.
- **Every port has at least two implementations**: `Toolbox` (live, replay),
  `Notifier` (Slack, Teams, WhatsApp), `Investigator` (LangGraph, scripted fake),
  `IncidentRepository` (SQLite, in-memory). Ports are `typing.Protocol`.

---

## 5. Configuration and secrets

- **Locally:** `.env` (gitignored), copied from the committed `.env.example`.
- **GitHub:** Environments `dev` and `prod`, uploaded with
  `gh secret set --env dev -f .env`. Only a future deploy workflow uses them;
  **unit tests need no secret**.
- **Every variable starts with `NW_`**: GitHub refuses secret names starting with
  `GITHUB_`, and the prefix avoids collisions.

---

## 6. Branches and CI

`dev → main`. Work lands on `dev` through pull requests; `main` only accepts pull
requests from `dev`.

[`.github/workflows/ci.yml`](.github/workflows/ci.yml), on pushes and pull requests
to both branches:
- `unit`: `ruff check` + `pytest`. **Unit tests only, no e2e.**
- `only-dev-to-main`: fails a pull request into `main` whose head isn't `dev`.

Branch protection (GitHub settings): `dev` requires `unit`; `main` requires `unit`
and `only-dev-to-main`.

Unit tests cover: the domain rules, the catalog (schema, filename = name, no
secrets), input parsing from fixture payloads, channel rendering with a fake HTTP
transport, the domain import rule, and `GET`-only source clients.

LLM evals call a real model, so they aren't unit tests: they run locally
(`make eval`), never in CI.

---

## 7. Milestones

Each milestone has its own doc with scope, deliverables, acceptance criteria and
its definition of done.

| # | Milestone | Needs a running system? |
|---|---|---|
| **M0** | [Plumbing](docs/milestones/M0-plumbing.md): FastAPI receives an Alertmanager payload and posts a placeholder to each enabled channel, by severity | no |
| **M1** | [The agent on recorded data](docs/milestones/M1-agent-on-recorded-data.md): the full graph with replayed tools, 5 scenarios, the local eval harness | no |
| **M2** | [Live sources](docs/milestones/M2-live-sources.md) (Prometheus, Loki, Kubernetes, GitHub) against knock-knock | yes, knock-knock in k3d |
| **M3** | [Runbooks](docs/milestones/M3-runbooks.md): hybrid search and a retrieval eval (recall@k) | no |
| **M4** | [AWS reference deployment](docs/milestones/M4-aws.md): Bedrock, one EC2 with k3s, deployed from `main` | yes, in AWS |

---

## 8. Decisions

| Decision | Choice | Why |
|---|---|---|
| Agent | LangGraph | checkpoints, parallel tools, resume; isolated in one module |
| Runtime | local first, one container | fastest loop, zero cloud cost |
| Storage | SQLite on a volume | plug and play needs no database server; Postgres when running more than one replica |
| Routing | severity floor per channel | phones only buzz for real incidents |
| Autonomy | diagnose only | no write credentials, nothing to go wrong |
| Catalog | YAML in this repo | one place to see what's watched; secrets stay in env |
| Model | `NW_LLM_MODEL` via `init_chat_model` | any provider, no switching code |
| CI | GitHub Actions, unit tests only | fast, free, needs no secrets |

## 9. Out of scope (for now)

Fixing anything, multiple customers, auth on the report page, traces, hot-reload of
the catalog, Helm, e2e tests in CI.
