# Resume Subproject (`Resume/`)

## Purpose

This folder contains resumes, cover letters, application trackers, and job research artifacts used to apply for roles.

## Operating Principles

- Act, don’t instruct: run the work end-to-end (generate artifacts, drive the browser, capture evidence, update trackers).
- Be explicit and accurate: never claim submission/completion without verifying via UI confirmation (and capture).
- Tailor with integrity: highlight relevant experience; do not invent facts.
- Minimize sensitive data: avoid persisting secrets or regulated personal data (especially DOB/SSN) to disk.

## Where Things Live

- Resumes: `Resume/resumes/`
- Generic and company-specific cover letters: `Resume/cover_letters/` and `Resume/applications/<company>/cover_letters/`
- Application tracker + reusable answers: `Resume/applications/job_applications/`
- Company-specific application work:
  - `Resume/applications/<company>/jobs/`
  - `Resume/applications/<company>/tailored_resumes/`
  - `Resume/applications/<company>/submissions/`
- LinkedIn drafts/queue: `Resume/linkedin/`
- Research artifacts (screenshots, scraped pages): `Resume/research/`

## Application Execution Checklist

For each target role:

1. Save job details (URL + requirements) into `Resume/applications/<company>/jobs/`.
2. Generate:
   - A role-focused resume version (HTML + optional PDF).
   - A concise cover letter (Markdown or TXT).
3. Apply via `agent-browser`:
   - Fill fields using `Resume/applications/job_applications/application_answers.md` as the baseline.
   - Re-snapshot after each navigation.
   - Capture confirmation screenshot/PDF to `Resume/applications/<company>/submissions/`.
4. Update `Resume/applications/job_applications/application_tracker.csv`.

## Tooling

- Browser automation: `agent-browser` (preferred).
- Optional conversions: `pandoc` is available for HTML/PDF generation; only generate PDFs if the target portal requires them.

## Local RAG (Applications Index)

The application index lives in `Resume/rag/` and is refreshed via:

```bash
python3 Resume/rag/cli.py scan
python3 Resume/rag/cli.py build
```
