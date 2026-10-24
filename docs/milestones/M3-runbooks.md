# M3 — Runbooks and retrieval

**Goal:** the agent searches the app's runbooks and cites them, and retrieval is
measured on its own, not only through the agent's final answer.

**Depends on:** M1 (the tool contract). Runs on recorded data; M2 is not required.

## Scope

| In | Out |
|---|---|
| `runbooks/knock-knock/*.md` | a vector database |
| hybrid search: BM25 + embeddings + RRF | reranker (add if recall@3 stays low) |
| `search_runbooks` tool scoped by the catalog | runbooks from wikis / Confluence |
| retrieval eval: recall@k, MRR | |

## Deliverables

```
runbooks/knock-knock/        one file per component + common failure modes (10–20 files)
src/nights_watch/triage/infrastructure/sources/runbooks.py
evals/retrieval/queries.yaml golden set: query → expected file#section
evals/retrieval/run.py       `make eval-retrieval`
```

## Design notes

- **Chunk on headings**, not fixed token sizes: one chunk per `##` section, with
  the file title prepended. Runbooks are written to be read by section.
- **Index in memory at startup.** A few hundred sections don't need a database.
  Embeddings are cached on disk keyed by content hash, so restarts don't re-embed.
- **Embeddings:** `NW_EMBEDDING_MODEL`, through the same provider abstraction as
  the LLM. Runbook text is English; queries may be Spanish, so pick a multilingual
  model.
- **Hybrid:** BM25 catches exact terms (`OOMKilled`, `ImagePullBackOff`, metric
  names); embeddings catch paraphrases. Merge with reciprocal rank fusion (k = 60).
- **Scope:** the tool only searches the app's `runbooks.path` from the catalog.
- **Output** follows the M1 contract: file, section, score, and the section text,
  so evidence quotes can cite a runbook like any other tool output.

## Acceptance criteria

1. `make eval-retrieval` prints recall@1, recall@3, recall@5 and MRR for BM25
   only, embeddings only and hybrid, over at least 30 golden queries.
2. Hybrid is at least as good as the better of the two on recall@3, or the
   write-up explains why not.
3. M1/M2 scenarios re-run with runbooks on and off; the difference in root-cause
   accuracy and tool calls is recorded in `evals/BASELINE.md`.
4. A query for one app never returns another app's runbooks.

## Unit tests (CI)

Heading chunker, BM25 ranking on a tiny corpus, RRF merge, scope filter, embedding
cache keyed by content hash (with a fake embedder). No real embedding calls in CI.

## Done when

All criteria pass, CI is green, the PR is merged into `dev`.
