# Applications RAG (LanceDB + JSONL)

This folder implements a lightweight "RAG" over the job-application artifacts in `Resume/`.

Goals:
- Keep a queryable index of applications with metadata and associated artifacts (resume/cover letter/evidence).
- Record actions and outcomes in append-only `.jsonl` logs.
- Prevent sensitive PII (especially DOB/SSN) from being ingested or written into logs.

## Layout

- `cli.py`: build/query utilities.
- `data/applications.jsonl`: canonical normalized application records (generated from the tracker + artifacts).
- `logs/events.jsonl`: append-only action log (safe/redacted).
- `lancedb/`: local vector database (NOT committed).

## Usage

Build or refresh the dataset + index:

```bash
python Resume/rag/cli.py build
```

Query by text:

```bash
python Resume/rag/cli.py query "mercor trajectory submitted"
python Resume/rag/cli.py query "agent routing Tetrate"
```

Append a manual note (logged to JSONL, redacted):

```bash
python Resume/rag/cli.py log --app-id "<app_id>" --type "follow_up" --msg "Pinged recruiter on LinkedIn"
```

Scan for sensitive PII patterns (DOB/SSN) before indexing:

```bash
python Resume/rag/cli.py scan
```

