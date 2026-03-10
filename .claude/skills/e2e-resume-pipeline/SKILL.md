# E2E Resume Pipeline Validation

## Description

Comprehensive end-to-end validation of the Resume application pipeline. Validates artifact completeness, tracker consistency, and resume quality across all application directories.

## Trigger

- User invokes `/e2e-resume-pipeline`
- After batch resume generation
- Before submission runs

## Prerequisites

- Python 3.10+
- Working directory: `Resume/` (the Resume subproject root)

## Execution Steps

### Phase 1: Artifact Audit

Run `python3 scripts/e2e/audit_artifacts.py` to verify every application directory has the expected files:

- `jobs/*.md` (at least 1 job description)
- `tailored_resumes/*.html` (at least 1 HTML resume)
- `tailored_resumes/*.docx` (at least 1 DOCX resume)
- `cover_letters/*.md` (at least 1 cover letter)

Output: `applications/job_applications/e2e_artifact_audit.json`

### Phase 2: Tracker Consistency

Run `python3 scripts/e2e/check_tracker.py` to cross-reference:

- Every `resume_ready` or `Applied` row in `application_tracker.csv` has matching files on disk
- Every application directory with resumes has a tracker row
- No orphan entries in either direction

Output: `applications/job_applications/e2e_tracker_consistency.json`

### Phase 3: Resume Quality

Run `python3 scripts/e2e/validate_resumes.py` to check each HTML resume:

- HTML parses without errors
- Contains required contact info (email, phone, GitHub, LinkedIn)
- Contains candidate name "IGOR GANAPOLSKY"
- Checks presence of high-value keywords from `resume_keyword_audit.json` (if it exists)
- File size is reasonable (> 1KB, < 100KB)

Output: `applications/job_applications/e2e_resume_quality.json`

### Phase 4: Final Report

Run `python3 scripts/e2e/generate_report.py` which:

- Reads all 3 JSON outputs
- Produces a structured markdown report at `applications/job_applications/e2e_report.md`
- Prints a summary to stdout with pass/fail counts

## Success Criteria

- All 3 phases produce valid JSON
- Final report is generated
- Zero critical failures (missing resumes for `resume_ready` rows)
- Warnings are acceptable (missing DOCX, missing cover letters for older applications)

## Quick Run

```bash
cd Resume && python3 scripts/e2e/run_all.py
```
