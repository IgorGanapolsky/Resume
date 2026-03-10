# Resume

Job search system: application tracking, tailored resume/cover letter generation, browser automation, and a hybrid RAG + RLHF pipeline.

## Structure

```
Resume/
├── resumes/                          # Base resume versions (HTML, PDF, DOCX)
├── cover_letters/                    # Generic cover letters
├── applications/
│   ├── job_applications/
│   │   ├── application_tracker.csv   # Master tracker (source of truth)
│   │   └── application_answers.md    # Reusable field answers
│   └── <company>/
│       ├── jobs/                     # Captured job descriptions
│       ├── tailored_resumes/         # Role-specific resume versions
│       ├── cover_letters/            # Company-specific cover letters
│       └── submissions/              # Confirmation evidence
├── linkedin/                         # LinkedIn post drafts
├── research/                         # Market research artifacts
└── rag/                              # RAG index + RLHF model
```

## RAG / RLHF Pipeline

Hybrid application intelligence: LanceDB vector search + Thompson Sampling RLHF.

```bash
# Rebuild index after tracker changes
python3 rag/cli.py build

# Search applications
python3 rag/cli.py query "AI infrastructure engineer"

# Dashboard
python3 rag/cli.py status

# Record outcome (trains Thompson model)
python3 rag/cli.py feedback --app-id <id> --outcome response

# Recommendations (Thompson Sampling)
python3 rag/cli.py recommend

# Auto-rebuild on file change
python3 rag/cli.py watch --interval 30

# PII audit
python3 rag/cli.py scan
```

## Tests

```bash
python3 -m pytest rag/tests -v
```

## Agent Workflow Contract

Repository-level agent behavior now lives in [`workflow.md`](workflow.md). It defines:

- allowed and prohibited change scope
- exact proof-of-work commands
- task intake requirements for automation-friendly issues
- done criteria for tracker, RAG, and submission changes

This file is versioned and validated by tests so agent instructions do not silently drift.

## Ralph Loop (GitHub CI)

Continuous loop runs in GitHub Actions via `.github/workflows/ralph-loop.yml`:

- Schedule: every 30 minutes
- Actions:
  - discover new jobs from remote feeds
  - add new `Draft` rows to `applications/job_applications/application_tracker.csv`
  - generate per-company artifacts under `applications/<company>/`
  - rebuild RAG index
  - open/update a PR with changes
- Live submit runtime:
  - defaults to local Playwright when no Anchor secret is configured
  - uses Anchor Browser when `ANCHOR_BROWSER_API_KEY` is present in repo secrets
  - supports Anchor profile persistence and US proxy-backed extra stealth through repo variables
  - still requires verified confirmation evidence before any tracker row becomes `Applied`

Manual run:

1. Open **Actions** -> **Ralph Loop**
2. Click **Run workflow**
3. Optionally set `max_new_jobs`

Recommended Anchor variables for autonomous submits:

- `ANCHOR_BROWSER_PROFILE_NAME=resume-ci`
- `ANCHOR_BROWSER_PROFILE_PERSIST=true`
- `ANCHOR_BROWSER_PROXY_ACTIVE=true`
- `ANCHOR_BROWSER_PROXY_COUNTRY_CODE=us`
- `ANCHOR_BROWSER_EXTRA_STEALTH_ACTIVE=true`
- `ANCHOR_BROWSER_STRICT=false`

## Quarantine Triage Sync

Blocked applications should not remain stranded in the tracker. Use the quarantined-issue sync to mirror `Quarantined` rows into GitHub issues:

```bash
python3 scripts/sync_quarantined_issues.py \
  --repo IgorGanapolsky/Resume \
  --report applications/job_applications/quarantine_issue_sync_report.json
```

There is also a GitHub Actions workflow at `.github/workflows/quarantine-issue-sync.yml` that can run on demand or every 6 hours.

## Principles

- Evidence before assertion: never mark `Applied` without a confirmation screenshot
- No fabricated facts: credentials, metrics, and dates must be real
- No high-risk PII on disk: SSN/DOB are detected and blocked at index time
