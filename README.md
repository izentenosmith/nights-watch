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

🚧 **Bootstrapping.** The app catalog and its validation are in place; the agent
is next. See [PLAN.md](PLAN.md).

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
