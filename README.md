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
cd rag && python3 -m pytest tests/ -v
```

## Ralph Loop (GitHub CI)

Continuous loop runs in GitHub Actions via `.github/workflows/ralph-loop.yml`:

- Schedule: every 30 minutes
- Actions:
  - discover new jobs from remote feeds
  - add new `Draft` rows to `applications/job_applications/application_tracker.csv`
  - generate per-company artifacts under `applications/<company>/`
  - rebuild RAG index
  - open/update a PR with changes

Manual run:

1. Open **Actions** -> **Ralph Loop**
2. Click **Run workflow**
3. Optionally set `max_new_jobs`

## Principles

- Evidence before assertion: never mark `Applied` without a confirmation screenshot
- No fabricated facts: credentials, metrics, and dates must be real
- No high-risk PII on disk: SSN/DOB are detected and blocked at index time
