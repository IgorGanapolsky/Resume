# Resume Subproject Agent Instructions

This `AGENTS.md` governs the `Resume/` subtree.

## Mission

Help with job search and applications by producing high-quality, role-tailored materials and keeping application artifacts organized and auditable.

## Calendar Guardrails (Non-Negotiable)

1. Source of truth for current date/time is runtime environment only.
2. For any date-sensitive request (`today`, `yesterday`, `tomorrow`, `latest`, `current`, deadlines, age), the assistant must:
   - state the resolved absolute date in ISO format (`YYYY-MM-DD`)
   - include weekday and timezone when relevant
3. Relative dates must always be converted to absolute dates in responses.
4. If a user-provided date is malformed or ambiguous (example: `2026--02-19`), pause and ask for clarification before proceeding.
5. If date evidence is missing, respond with `UNVERIFIED_DATE_CLAIM` and do not assert.
6. Never report "submitted today" or equivalent without timestamped evidence (file path plus timestamp).

## Non-Negotiables (Safety/Policy)

- Do not fabricate credentials, employers, dates, degrees, titles, or metrics.
- Do not submit an application if any required field is ambiguous or unknown. Pause and ask for clarification (examples: work authorization nuances, desired compensation, availability date, legal name, address, citizenship, disability/veteran status).
- Do not store secrets (passwords, OTP codes, SSNs). Never paste them into files. Use environment variables or interactive entry only.
- Do not write high-risk PII to disk (DOB, SSN, passport/ID numbers). If a portal requires DOB, only enter it into the form UI and keep it out of repo artifacts/logs.
- Do not claim an application was submitted unless the final confirmation screen (or an email confirmation) was observed and captured.
- Stop before any irreversible step if the UI indicates a "final submit" action and the answer set has any uncertainty.

## Warm Intro & Product-First Mandate (Aakash Gupta Protocol)

1. **Warm Intro First:** Never submit a cold application for a high-value (Tier 1) company without first attempting to build a referral path.
   - For every Tier 1 `Draft`, the agent must search for 2-3 potential referrers (LinkedIn).
   - Log them in the tracker under `Referrer Name` and `Referrer Role`.
   - Draft a personalized outreach using `email_templates/referral_request_template.md`.
2. **The 90-Minute Rule (The 1-Pager):** For Tier 1 roles, replace generic cover letters with a **Product Proposal**.
   - Analyze the company's product, find one friction point, and propose a specific technical/product fix.
   - If possible, include a minimal code prototype (Gist or local snippet).
   - Save to `applications/<company>/proposals/YYYY-MM-DD_product_proposal.md`.
   - Update `Product Proposal Path` in the tracker.
3. **Outreach Cadence:** Monitor the `Outreach Cadence (1/3/7/14)` column.
   - If no response after Day 1, schedule follow-ups for Day 3, 7, and 14.
   - Move to `ReadyToSubmit` only if the referral path fails after Day 14 or if a referrer explicitly says "apply through the portal."

## Workflow (Per Job)

1. Capture the job posting:
   - Save raw page text/summary into `Resume/applications/<company>/jobs/`.
   - Include URL, date captured, and a bullet list of key requirements.
2. Tailor materials:
   - Choose the most relevant base resume from `Resume/resumes/`.
   - Create tailored resume artifacts in `Resume/applications/<company>/tailored_resumes/`.
   - Create a tailored cover letter in `Resume/applications/<company>/cover_letters/`.
3. Apply:
   - Use browser automation (`agent-browser` preferred) and re-snapshot after every navigation.
   - Save submission evidence (screenshots/PDFs/notes) in `Resume/applications/<company>/submissions/`.
4. Track:
   - Append/update `Resume/applications/job_applications/application_tracker.csv` with status, date, and notes.

## Before Starting Any Application

1. **Dedup check** — query the index first; do not create a duplicate entry:
   ```bash
   python3 Resume/rag/cli.py query "<company> <role>"
   ```
2. **Targeting signal** — check Thompson Sampling recommendations to prioritize which draft to work next:
   ```bash
   python3 Resume/rag/cli.py recommend
   ```
   Prefer arms with higher mean reward (categories/methods that have yielded responses/interviews).

## After Receiving a Response

Record the outcome so the RLHF model can learn. Do this before anything else:

```bash
# Get app_id from the query output:
python3 Resume/rag/cli.py query "<company>"

# Record outcome (blocked | no_response | rejected | response | interview | offer):
python3 Resume/rag/cli.py feedback --app-id <app_id> --outcome <outcome>
```

Update tracker CSV `Response` and `Interview Stage` columns to match.

## RAG & RLHF Maintenance

| Trigger                 | Command                                                                |
| ----------------------- | ---------------------------------------------------------------------- |
| Tracker CSV changed     | `python3 Resume/rag/cli.py build`                                      |
| New outcome received    | `python3 Resume/rag/cli.py feedback --app-id <id> --outcome <outcome>` |
| Dashboard               | `python3 Resume/rag/cli.py status`                                     |
| PII audit               | `python3 Resume/rag/cli.py scan`                                       |
| Auto-watch (background) | `python3 Resume/rag/cli.py watch --interval 30`                        |

## Definition of Done

| Task                  | Done when                                                               |
| --------------------- | ----------------------------------------------------------------------- |
| Application submitted | Confirmation screenshot in `submissions/`; tracker row `Status=Applied` |
| Resume generated      | HTML file in `tailored_resumes/`; renders correctly                     |
| Cover letter written  | File in `cover_letters/`; no invented facts                             |
| Outcome recorded      | `feedback` command run; `arms.json` updated; tracker CSV updated        |
| Index current         | `build` run after last tracker change                                   |

## Naming Conventions

- Use ASCII filenames.
- Prefer this pattern:
  - `jobs/YYYY-MM-DD_<company>_<role>_<jobid>.md`
  - `cover_letters/YYYY-MM-DD_<company>_<role>.md`
  - `tailored_resumes/YYYY-MM-DD_<company>_<role>.html` (and `.pdf` if generated)
  - `submissions/YYYY-MM-DD_<company>_<role>_<stage>.png|pdf|md`

## Browser Automation Notes

- Prefer `agent-browser` sessions per target site to reduce ref churn.
- Always `snapshot -i` after any click that could change the DOM.
- When a site requires login/2FA, proceed up to the prompt, then stop and ask the user to complete the authentication step.

## Ralph Loop Protection

1. Treat in-progress `Ralph Loop` and `Ralph Local Submit` runs on the current `origin/main` commit as protected operational runs.
2. Do not cancel protected runs for workspace hygiene, stale PR cleanup, duplicate branch cleanup, or routine maintenance.
3. Cancellation is allowed only when:
   - the user explicitly requests cancellation
   - the run is on a superseded commit that is not the current `origin/main` commit
   - a duplicate run on the exact same head SHA has already completed successfully and the remaining run is redundant
4. If an automation PR is stale, close the PR or branch without cancelling a protected current-`main` run.
5. Preserve proxy-backed or Anchor Browser submit paths whenever available; they may be the only viable route after reCAPTCHA trust has degraded on the local IP.

## PR Management & System Hygiene

1. Start every PR or branch maintenance session from a dedicated git worktree.
2. Before acting, read scoped directives, query `python3 Resume/rag/cli.py query "<topic>"`, inspect open PRs and branches, and check CI on `main`.
3. Treat merge readiness as checks plus review quality:
   - do not merge PRs with unresolved substantive review findings
   - do not merge PRs with obvious scope drift or unsafe/non-technical artifacts
4. Close stale or unsafe PRs with a short evidence-based reason instead of forcing a merge.
5. Delete only branches and worktrees that are proven merged, obsolete, or duplicated. Preserve dirty active worktrees.
6. Before claiming completion, verify `main` CI and run a local dry-run of the operational pipeline.

## Enhanced Session Directives (2026-03-18)

### PR & Branch Management

- Follow the multi-step inspection and cleanup protocol for all PR sessions.
- Document blockers and merge ready PRs with SHAs.
- Maintain workspace hygiene by removing dormant code and old logs.

### Agentic Standards

- Act as a fully autonomous CTO.
- Never ask for CEO manual steps if automatable.
- Log all lessons and mistakes to RAG for continuous system improvement.

## Enhanced Session Directives (2026-04-09)

### PR Management & Branch Hygiene

- Inspect all open PRs at session start and classify each as merge-ready, blocked, stale, or unsafe with evidence.
- Identify orphan branches and worktrees, then delete only the ones that are merged, duplicated, stale, or otherwise proven unnecessary.
- Publish verified local maintenance branches instead of leaving them unpushed unless a concrete blocker prevents publication.
- When a branch has no open PR but still carries unique commits, inspect whether those commits are still required before deletion.

### Verification & Communication

- Do not say "Done merging PRs" until merges, branch cleanup, CI checks, and the local operational dry-run are all verified.
- Use evidence-based completion language while verification is in flight instead of declaring completion early.
- If RAG or memory logging is unavailable, report the tool failure explicitly and do not claim the lesson was logged.
