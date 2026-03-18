# Resume Subproject (`Resume/`)

## PR Management & System Hygiene

- Use a dedicated git worktree for implementation, branch cleanup, and verification work. Never edit from the primary checkout.
- Start maintenance sessions by reading repo directives, querying `python3 rag/cli.py query "<topic>"` for relevant lessons, reviewing open PRs and orphan branches, and checking CI on `main`.
- Merge only review-ready PRs. Green CI does not override unresolved review findings, scope drift, or unsafe artifacts.
- Close stale or non-ready PRs with a short evidence-based explanation.
- Delete only branches and worktrees that are demonstrably merged, stale, or duplicated. Leave active dirty worktrees alone.
- Before declaring completion, verify `main` CI again, run a local dry run of the operational pipeline, and summarize the evidence.

## Enhanced Session Directives (2026-03-18)

### PR & Branch Management
- Strictly manage branches and PRs following the CTO protocol.
- Merge only review-ready PRs with passing CI.
- Prune stale worktrees and branches post-merge.
- Verify main branch CI and run readiness dry-runs.

### CTO Mandates
- Full autonomy: execute all commands without CEO intervention.
- Proof of work: provide specific evidence for completion.
- RAG integration: mandatory query at start and log at end.
