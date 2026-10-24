# M2 — Live sources against knock-knock

**Goal:** the same agent, now reading real Prometheus, Loki, Kubernetes and GitHub
data while knock-knock breaks on purpose in a local cluster. Real incidents become
new recorded scenarios.

**Depends on:** M1, and **knock-knock running in k3d (its level 2 or later)**.
Today knock-knock is at level 0, so this milestone is blocked on that repo.

## No entanglement

knock-knock gets **no change** for this milestone. Night's Watch observes it from
the outside, with infrastructure-level telemetry only:

- container CPU / memory, restarts, `OOMKilled` (kube-state-metrics, cAdvisor);
- request rate, latency and status codes per backend (Traefik's metrics in k3d);
- pod stdout (Loki, collected by Alloy);
- Kubernetes events and rollout history (the API).

Everything needed to observe and break it lives in this repo under `lab/`.

## Scope

| In | Out |
|---|---|
| live clients: `prometheus.py`, `loki.py`, `k8s.py`, `github.py` | traces |
| record mode: live calls also write fixtures | runbook search (M3) |
| startup checks: ping sources, run each app's selectors once | cloud deployment (M4) |
| `lab/`: observability stack, alert rules, fault scripts | |

## Deliverables

```
src/nights_watch/triage/infrastructure/sources/
├── prometheus.py   Prometheus HTTP API → `metrics` contract
├── loki.py         Loki query_range → `logs` contract (patterns, not raw lines)
├── k8s.py          official kubernetes client → k8s_events, pod_restarts, rollout_history
├── github.py       REST → recent_commits, recent_workflow_runs
└── recording.py    wraps the live toolbox, writes evals/scenarios/<name>/tools/*.json
lab/
├── README.md       how to bring the lab up and tear it down
├── install.sh      Prometheus + Alertmanager + Loki + Alloy into the k3d cluster
├── alerts.yaml     knock-knock alert rules, routed to Night's Watch
└── faults/         one script per fault, with its undo
```

## Design notes

- **Same contracts as M1.** The live clients return exactly the shapes the
  replay toolbox returns; the agent can't tell them apart.
- **Scope injection:** each client builds the query from the app's catalog
  selector plus the structured input. The LLM never sends PromQL or LogQL.
- **Read-only:** clients only issue `GET` (a test asserts it per client); the
  Kubernetes service account gets the built-in `view` ClusterRole.
- **Startup:** ping every configured source (fail to start if one is down); run
  each app's selectors once and report `knock-knock: loki selector matched 0 streams`.
- **Third-party lab components may use their Helm charts.** PLAN's "no Helm"
  is about shipping Night's Watch, not installing Prometheus.
- **Verify the catalog against reality.** `catalog/knock-knock.yaml` assumes
  `namespace: knock-knock` and `app: knock-<lang>` labels. Check knock-knock's
  real manifests once level 2 exists and fix the catalog, not knock-knock.

## Alert rules (lab/alerts.yaml)

| Alert | Severity | Signal |
|---|---|---|
| `KnockBackendRestarting` | critical | restarts in 10 min > 2 |
| `KnockBackendDown` | critical | a backend has no ready endpoints |
| `KnockLatencyHigh` | warning | p95 per backend > 200 ms for 2 min |
| `KnockErrorRate` | warning | 5xx ratio per backend > 5% for 2 min |

## Faults (lab/faults/)

| Script | What it does | Expected diagnosis |
|---|---|---|
| `oom.sh` | memory limit far below usage on python | OOMKilled |
| `delay.sh` | `DELAY_MS=500` on rust | latency in rust only |
| `readiness.sh` | readiness probe on a wrong path for ts | ts out of endpoints |
| `netpol.sh` | deny-all NetworkPolicy on go | go unreachable |
| `bad-image.sh` | rollout to a tag that doesn't exist | ImagePullBackOff after a rollout |

## Acceptance criteria

1. With the lab up, each fault fires its alert, the alert reaches Night's Watch,
   and a diagnosis is posted within 2 minutes.
2. The top hypothesis names the expected root cause for at least 4 of 5 faults.
3. Each live run was recorded; the recordings are new scenarios under
   `evals/scenarios/live-*`, and `make eval` replays them.
4. Startup reports a wrong selector (edit the catalog to break one, see the report).
5. `GET`-only tests pass for every client.

## Unit tests (CI)

Each client against recorded HTTP responses (`httpx.MockTransport` or the
kubernetes client's fakes): query building with scope injection, shrinking,
`GET`-only. The lab itself is never part of CI.

## Done when

All criteria pass, `evals/BASELINE.md` gains the live numbers, CI is green, the PR
is merged into `dev`.
