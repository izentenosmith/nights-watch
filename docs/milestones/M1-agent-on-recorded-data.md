# M1 — The agent on recorded data

**Goal:** the full triage agent, end to end, with every tool reading recorded
responses instead of live systems. This is where the agent is designed, tuned and
measured. The tool contracts defined here are the ones M2's live clients implement.

**Depends on:** M0.

## Scope

| In | Out (later milestone) |
|---|---|
| `Incident` aggregate, grouping, SQLite persistence | live Prometheus / Loki / k8s / GitHub clients (M2) |
| the LangGraph graph from PLAN §3 | runbook search (M3), a stub returns nothing |
| tool contracts + the replay toolbox | |
| evidence verification, budget, redaction | |
| `/runs/{id}` report page with 👍/👎 | |
| 5 knock-knock scenarios + local eval harness | |

## Deliverables

```
src/nights_watch/triage/
├── domain/
│   ├── incident.py          Incident (aggregate root), status, grouping rule
│   ├── diagnosis.py         Hypothesis, Evidence, Diagnosis, EvidenceVerifier
│   ├── redaction.py         secrets / personal data scrubbing (pure functions)
│   └── ports.py             Toolbox, Investigator, IncidentRepository, Notifier (Protocols)
├── application/
│   ├── receive_alert.py     now: route → group → open or attach → enqueue investigation
│   ├── investigate.py
│   └── record_feedback.py
└── infrastructure/
    ├── sources/replay.py    Toolbox over evals/scenarios/<name>/tools/*.json
    ├── langgraph_investigator.py   the only module importing langgraph
    └── sqlite_incidents.py
src/nights_watch/api/runs.py GET /runs/{id} (HTML), POST /runs/{id}/feedback
evals/
├── scenarios/<name>/scenario.yaml   alert + expected root cause
├── scenarios/<name>/tools/*.json    recorded tool responses
└── run.py                           `make eval`
Makefile                             eval target
```

## Design notes

- **`Incident` rules** (unit-tested, no LLM): one open incident per
  (app, fingerprint group) within `NW_GROUP_WINDOW` (default 5 min); new alerts in
  the window attach; status only moves forward
  `open → investigating → diagnosed → resolved`; a diagnosis is accepted only after
  `EvidenceVerifier`; feedback only after a diagnosis.
- **Tool contracts** — inputs are structured, never raw PromQL/LogQL; the
  toolbox injects the app's scope from the catalog:

  | Tool | Input | Output (already shrunk) |
  |---|---|---|
  | `metrics` | metric, component?, range | per series: min, max, latest, change point (when, how much) |
  | `logs` | component?, pattern?, range | top error patterns with counts and one example line each |
  | `k8s_events` | component?, range | events (reason, object, count, last seen) |
  | `pod_restarts` | component? | pod, restarts, last termination reason (e.g. `OOMKilled`) |
  | `rollout_history` | component? | revisions with image tag and time |
  | `recent_commits` | since | sha, time, author, title |
  | `search_runbooks` | query | stub in M1, real in M3 |

  Every call gets an id (`t1`, `t2`…). Outputs are wrapped as data in the prompt
  and redacted before the model sees them.
- **`gather_basics`** runs in code before the LLM: the alert's own metric (30 min),
  k8s events and restarts for the app, deploys in the last 2 h.
- **Budget:** `NW_MAX_TOOL_CALLS=10`, `NW_MAX_SECONDS=90`, `NW_MAX_COST_USD=0.25`.
  Cost = token usage × a per-model price table. Hitting a limit moves to `rank`
  with what was gathered; it's never an error.
- **`rank`** uses structured output: ranked hypotheses (`cause`, `component`,
  `category`, `confidence`, `evidence[]`, `next_check`) plus `checked` /
  `not_checked`.
- **`verify_evidence`** (domain): every `evidence.tool_call` must exist and its
  `quote` must appear in that call's output; otherwise the evidence is dropped,
  and a hypothesis left with none is dropped too.
- **Notifications** now carry the top hypothesis and a link to
  `NW_PUBLIC_URL/runs/{id}`. Add `NW_PUBLIC_URL` to `.env.example`.
- **Checkpoints:** LangGraph `SqliteSaver`; a restart resumes an investigation.
- **Tests never call a model:** use LangChain's fake chat models with scripted
  tool calls and answers.

## Scenarios (synthetic, modelled on knock-knock's levels)

| Scenario | Alert | Expected root cause |
|---|---|---|
| `oom-after-deploy` | python backend restarts | python OOMKilled since a new image tag |
| `slow-backend` | p95 latency high | `DELAY_MS` set on rust (latency in one backend only) |
| `readiness-failing` | backend unavailable | ts readiness probe failing, pod out of endpoints |
| `network-policy` | 5xx from go | NetworkPolicy blocks traffic to go |
| `red-herring` | error rate up | bad rollout of go, while frontend CPU spike is unrelated |

## Acceptance criteria

1. Each scenario runs end to end with the replay toolbox and posts a diagnosis.
2. `make eval` runs every scenario 3 times and prints, per scenario: root-cause
   match, category match, evidence valid, tool calls, seconds, cost; plus totals.
   The first run's numbers are written to `evals/BASELINE.md`.
3. A run never exceeds the budget.
4. A fabricated quote (scripted fake model) is dropped by `verify_evidence`.
5. Two alerts with the same fingerprint inside the window produce one incident.
6. `/runs/{id}` shows hypotheses, evidence, what was and wasn't checked, and cost;
   👍/👎 is stored on the incident.
7. Killing the process mid-investigation and restarting resumes it.

## Unit tests (CI)

Incident rules, `EvidenceVerifier`, redaction, budget guard, the graph with a
scripted fake model (happy path, budget hit, fabricated evidence), the replay
toolbox, SQLite repository. `make eval` is **not** in CI: it calls a real model.

## Done when

All criteria pass, `evals/BASELINE.md` exists, CI is green, the PR is merged into `dev`.
