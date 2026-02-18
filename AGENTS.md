# Resume Subproject Agent Instructions

This `AGENTS.md` governs the `Resume/` subtree.

## Mission

Help with job search and applications by producing high-quality, role-tailored materials and keeping application artifacts organized and auditable.

## Non-Negotiables (Safety/Policy)

- Do not fabricate credentials, employers, dates, degrees, titles, or metrics.
- Do not submit an application if any required field is ambiguous or unknown. Pause and ask for clarification (examples: work authorization nuances, desired compensation, availability date, legal name, address, citizenship, disability/veteran status).
- Do not store secrets (passwords, OTP codes, SSNs). Never paste them into files. Use environment variables or interactive entry only.
- Do not write high-risk PII to disk (DOB, SSN, passport/ID numbers). If a portal requires DOB, only enter it into the form UI and keep it out of repo artifacts/logs.
- Do not claim an application was submitted unless the final confirmation screen (or an email confirmation) was observed and captured.
- Stop before any irreversible step if the UI indicates a "final submit" action and the answer set has any uncertainty.

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

| Trigger | Command |
|---------|---------|
| Tracker CSV changed | `python3 Resume/rag/cli.py build` |
| New outcome received | `python3 Resume/rag/cli.py feedback --app-id <id> --outcome <outcome>` |
| Dashboard | `python3 Resume/rag/cli.py status` |
| PII audit | `python3 Resume/rag/cli.py scan` |
| Auto-watch (background) | `python3 Resume/rag/cli.py watch --interval 30` |

## Definition of Done

| Task | Done when |
|------|-----------|
| Application submitted | Confirmation screenshot in `submissions/`; tracker row `Status=Applied` |
| Resume generated | HTML file in `tailored_resumes/`; renders correctly |
| Cover letter written | File in `cover_letters/`; no invented facts |
| Outcome recorded | `feedback` command run; `arms.json` updated; tracker CSV updated |
| Index current | `build` run after last tracker change |

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
