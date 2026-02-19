# Applications RAG (LanceDB + Hybrid RLHF + JSONL)

This folder implements a lightweight "RAG" over the job-application artifacts in `Resume/`.

Goals:
- Keep a queryable index of applications with metadata and associated artifacts (resume/cover letter/evidence).
- Record actions and outcomes in append-only `.jsonl` logs.
- Maintain dual memory stores for retrieval-time alignment:
  - short-term episodic memory (`memory_short.jsonl`)
  - long-term semantic memory (`memory_long.jsonl`)
- Fuse retrieval scores with RLHF priors (Thompson Sampling arms) and memory boosts.
- Prevent sensitive PII (especially DOB/SSN) from being ingested or written into logs.

## Layout

- `cli.py`: build/query utilities.
- `data/applications.jsonl`: canonical normalized application records (generated from the tracker + artifacts).
- `data/memory_short.jsonl`: episodic memory (events + outcomes, recency-weighted).
- `data/memory_long.jsonl`: semantic memory distilled from records (stable targeting priors).
- `data/arms.json`: Thompson Sampling RLHF state (category + method arms).
- `logs/events.jsonl`: append-only action log (safe/redacted).
- `lancedb/`: local vector database (NOT committed).

## Usage

Build or refresh the dataset + index:

```bash
python Resume/rag/cli.py build
# Optional distributed mode (safe fallback in auto mode):
python Resume/rag/cli.py build --dist-mode auto --dist-backend auto
```

Query by text:

```bash
python Resume/rag/cli.py query "mercor trajectory submitted"
python Resume/rag/cli.py query "agent routing Tetrate"
```

Smart retrieval endpoint (single interface for agents):

```bash
python Resume/rag/cli.py retrieve "python backend remote" -k 5 --json
python Resume/rag/cli.py retrieve "sre ashby" --status Applied --method ashby
python Resume/rag/cli.py retrieve "infra remote" --json --envelope --provider local
```

`--json` keeps backward-compatible list output.
`--json --envelope` emits a strict contract envelope (`rag.retrieve.v1`) with
request metadata, provider id, timestamp, and validated result records.

Record explicit outcome feedback (updates RLHF model and short-term memory):

```bash
python Resume/rag/cli.py feedback --app-id "<app_id>" --outcome interview
```

Quick thumbs alias (maps `up -> response`, `down -> no_response`):

```bash
python Resume/rag/cli.py thumb --app-id "<app_id>" --vote up
python Resume/rag/cli.py thumb --app-id "<app_id>" --vote "👎"
```

Replay feedback from JSONL streams into RLHF arms (idempotent via ledger):

```bash
python Resume/rag/cli.py feedback-batch --source memory_short
python Resume/rag/cli.py feedback-batch --source events --dist-mode auto
```

Append a manual note (logged to JSONL, redacted):

```bash
python Resume/rag/cli.py log --app-id "<app_id>" --type "follow_up" --msg "Pinged recruiter on LinkedIn"
```

Scan for sensitive PII patterns (DOB/SSN) before indexing:

```bash
python Resume/rag/cli.py scan
```
