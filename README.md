# Night's Watch

> *An alert fires at 3 a.m.* — *Night's Watch already looked: the python pod was
> OOMKilled 4 times since deploy sha-3f9c2a1.*

An on-call triage agent. When an alert fires, it investigates with **read-only**
tools (metrics, logs, Kubernetes, recent deploys, runbooks) and, within about two
minutes, posts a ranked root-cause hypothesis with its evidence to **Slack**,
**Teams** and/or **WhatsApp**.

It's plug and play: one container. Point your alert webhooks at it, give it the
URLs of your observability tools, and set the credentials of the channels you want.
Anything you don't configure doesn't exist for the agent.

## Status

🚧 **M0, plumbing.** Alerts come in (Alertmanager, Grafana, generic JSON), are routed
to a watched app through the catalog, and a placeholder message goes out to every enabled
channel above its severity floor. The agent is next. See [PLAN.md](PLAN.md) and
[docs/milestones](docs/milestones/).

## Which apps it watches

Every watched app is one file in [`catalog/`](catalog/). To add one, copy
[`catalog/_template.yaml`](catalog/_template.yaml) to `catalog/<app-name>.yaml`.
[`catalog/knock-knock.yaml`](catalog/knock-knock.yaml) is the sample.

The catalog holds **no secrets**, only the names of environment variables
(`NW_...`). A unit test fails the build if anything that looks like a secret lands
there.

## Configuration

```bash
cp .env.example .env              # fill in values; .env is gitignored
gh secret set --env dev -f .env   # upload the same values to GitHub
```

## Running it

```bash
cp .env.example .env              # set the channels you want; see the comments
docker compose up --build         # listens on 127.0.0.1:8000
curl -X POST localhost:8000/alerts/alertmanager -H 'content-type: application/json' \
  -d @tests/fixtures/alertmanager_firing_critical.json
curl localhost:8000/apps          # which channels are enabled for each app
```

Point Alertmanager or Grafana webhooks at `POST /alerts/alertmanager`; anything else can
post `{title, severity, labels, started_at}` to `POST /alerts/generic`. `GET /metrics`
exposes `nightswatch_unmatched_alerts_total`.

## Development

Needs [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest
uv run ruff check .
```

Branches: work lands on `dev` through pull requests; `main` only accepts pull
requests from `dev`. CI runs lint and unit tests on both. There are no e2e tests.

## License

[MIT](LICENSE)
