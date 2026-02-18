# Copilot Instructions — Resume

## Core Rules

- Never invent facts: no fabricated employers, titles, dates, metrics, or skills.
- Never commit secrets or API keys. Run `python3 scripts/scrub_job_captures.py` before staging job files.
- Never claim an application is submitted without a confirmation screenshot in `applications/<company>/submissions/`.

## Workflow

1. Check for duplicates before starting: `python3 rag/cli.py query "<company> <role>"`
2. Check RLHF recommendations: `python3 rag/cli.py recommend`
3. Scrub job files before commit: `python3 scripts/scrub_job_captures.py`
4. Rebuild index after tracker changes: `python3 rag/cli.py build`
5. Record outcomes immediately: `python3 rag/cli.py feedback --app-id <id> --outcome <outcome>`

## Testing

```bash
cd rag && python3 -m pytest tests/ -v
```

## Change Scope

- Surgical diffs only. Don't touch unrelated files.
- No new Markdown files outside allowed paths (applications/, rag/, research/, scripts/).
- Keep RAG/RLHF pipeline functional at all times.
