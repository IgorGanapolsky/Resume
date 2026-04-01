# Resume Agent Workflow

This file is the machine-readable workflow contract for AI agents operating on the `Resume/` repository.

## Scope

- Allowed to change:
  - `scripts/`
  - `rag/`
  - `.github/`
  - `README.md`
  - `workflow.md`
  - `applications/<company>/` artifacts that are directly produced by the active task
- Allowed tracker mutations:
  - `Draft -> ReadyToSubmit`
  - `ReadyToSubmit -> Applied` only after verified submission evidence exists
  - `ReadyToSubmit -> Quarantined` for anti-bot or manual blockers
  - `Applied -> Draft` only through audited integrity repair when evidence is missing
- Always preserve unrelated user changes already present in the worktree.

## Prohibited Changes

- Never commit secrets, auth cookies, tokens, OTP codes, SSNs, DOBs, passport numbers, or raw browser storage state.
- Never fabricate employers, dates, degrees, titles, compensation, or outcome claims.
- Never mark an application as submitted without a confirmation screenshot or email-backed proof.
- Never edit infrastructure, workflow secrets, or repository settings unless the task explicitly requires it.
- Never revert unrelated local modifications.

## Setup

Run from the repo root.

```bash
python3 scripts/check_calendar_guardrails.py
python3 scripts/scrub_job_captures.py --dry-run
```

## Live Submit Auth

- `CI_SUBMIT_PROFILE_JSON` and `CI_SUBMIT_ANSWERS_JSON` are required for live CI submit.
- `CI_SUBMIT_AUTH_JSON` is optional. When present, it must be an adapter-keyed Playwright storage-state object:

```json
{
  "ashby": { "storage_state": { "cookies": [], "origins": [] } },
  "greenhouse": { "storage_state": { "cookies": [], "origins": [] } },
  "lever": { "storage_state": { "cookies": [], "origins": [] } }
}
```

- Use `python3 scripts/capture_submit_auth.py` to refresh this optional secret instead of committing raw browser storage state.

## Test Commands

Use these exact commands unless the task says otherwise.

```bash
python3 -m pytest rag/tests -v
python3 rag/cli.py build
python3 rag/cli.py status
python3 scripts/sync_quarantined_issues.py --report applications/job_applications/quarantine_issue_sync_report.json
```

## Proof of Work

Every automation or code-change task must produce:

1. Passing test evidence from the relevant commands in `Test Commands`.
2. A short change summary with affected files and why they changed.
3. For tracker mutations, a rebuilt RAG index via `python3 rag/cli.py build`.
4. For application submission claims, an evidence path under `applications/<company>/submissions/`.
5. For issue-sync changes, a dry-run or apply report from `scripts/sync_quarantined_issues.py`.

## Task Intake

Preferred GitHub issue types:

- `Agent-ready task`: bounded repo automation or bugfix work with explicit scope and acceptance criteria.
- `Quarantined application triage`: a blocked application that needs manual or adapter follow-up.

Tasks handed to agents must include:

- goal
- allowed files or directories
- validation commands
- done criteria
- supporting links or evidence paths

## Done Criteria

A task is done only when all applicable checks pass:

- Lint/test commands are green.
- **For Tier 1 roles:** A `Referrer Name` is identified and `Referral Status` is tracked.
- **For Tier 1 roles:** A `Product Proposal Path` points to a company-specific 1-pager.
- The tracker state is internally consistent.
- RAG index is rebuilt after tracker changes.
- New docs are versioned and machine-verifiable.
- No new secrets or high-risk PII appear in git diff.

## PR Requirements

PR descriptions must include:

- 1-3 bullet summary
- proof-of-work commands that were run
- residual risks or blockers, if any
