# M4 — AWS reference deployment

**Goal:** Night's Watch and its lab target running in AWS, deployed from `main`
by GitHub Actions, using Bedrock as the model provider. This is the "deployed, not
just experimented" proof.

**Depends on:** M2 (a lab worth deploying). M3 is optional.

## The constraint that shapes it

The agent needs to reach the systems it watches. A cloud agent can't query
Prometheus on your laptop, so **the lab target moves to AWS too**.

## Hosting options

| | A. One EC2 with k3s (recommended) | B. ECS Fargate + RDS |
|---|---|---|
| Runs | knock-knock + `lab/` + Night's Watch, all in one k3s cluster | Night's Watch on Fargate; the lab still needs its own host |
| Storage | SQLite on an EBS-backed volume, as designed | needs a Postgres `IncidentRepository` + `PostgresSaver` |
| Ships as | the `k8s/nights-watch.yaml` manifest from PLAN | a task definition |
| Cost | one instance, stop it when idle | Fargate + RDS + the lab host |
| Story | same manifest locally and in the cloud | more AWS-managed services |

A keeps the design as it is and costs the least. B is worth it only if you want the
managed-services line on the CV, and it adds a second persistence adapter.

## Scope (option A)

| In | Out |
|---|---|
| Terraform: VPC, one EC2, EBS, security group, IAM | multi-AZ, autoscaling |
| `k8s/nights-watch.yaml` (Deployment, Service, PVC, `view` RBAC) | a domain name / TLS (use SSM port forwarding) |
| `publish.yml`: image to GHCR on `main` | |
| `deploy.yml`: GitHub OIDC → AWS → apply the manifest | |
| Bedrock access through the instance role | |

## Deliverables

```
infra/aws/                   Terraform (state in S3, documented in infra/aws/README.md)
k8s/nights-watch.yaml        the plug-and-play manifest
.github/workflows/publish.yml   build + push ghcr.io/izentenosmith/nights-watch on main
.github/workflows/deploy.yml    on main after publish; uses the `prod` GitHub Environment
```

## Design notes

- **Secrets:** the `prod` GitHub Environment holds the `NW_*` values
  (`gh secret set --env prod -f .env.prod`); `deploy.yml` turns them into a
  Kubernetes Secret. No secret is ever in Terraform state or the repo.
- **AWS auth from GitHub:** OIDC federation to a deploy role. No AWS keys stored
  in GitHub.
- **Deploy mechanism:** SSM Run Command on the instance (`kubectl apply`), so no
  Kubernetes API is exposed to the internet.
- **Bedrock:** `NW_LLM_MODEL=bedrock_converse:<model id>`; the instance role allows
  `bedrock:InvokeModel` on that model only. Set the instance metadata hop limit to
  2 so pods can reach the role's credentials.
- **The report page** (`/runs/{id}`) is reached through SSM port forwarding until
  there's a reason to expose it; message links point at `NW_PUBLIC_URL`.
- **Teardown:** `terraform destroy` removes everything; the README shows the
  monthly cost and how to stop the instance.

## Acceptance criteria

1. A merge into `main` publishes the image and deploys it, with no manual step.
2. A fault injected in the AWS lab produces a diagnosis on Slack (and WhatsApp
   for critical) within 2 minutes, using Bedrock.
3. No AWS key or `NW_*` value appears in the repo, Terraform state or workflow logs.
4. `terraform destroy` leaves nothing billable behind.
5. The README documents the measured monthly cost.

## Unit tests (CI)

Nothing new in the app. `terraform fmt -check` and `terraform validate` can run in
CI without AWS credentials; anything that needs AWS stays out of CI.

## Done when

All criteria pass, a write-up (`docs/write-up.md`) shows the architecture, the eval
tables from M1–M3 and what failed, and `main` is deployed.
