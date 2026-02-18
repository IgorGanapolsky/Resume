## Summary
<!-- What does this PR do? 1-3 bullets max. -->

## Type
- [ ] New application / materials
- [ ] RAG / RLHF update
- [ ] Bug fix
- [ ] Infrastructure / CI
- [ ] Refactor

## Checklist
- [ ] No secrets committed (`python3 scripts/scrub_job_captures.py --dry-run` clean)
- [ ] Job files scrubbed if new captures added
- [ ] Tests pass (`cd rag && python3 -m pytest tests/ -v`)
- [ ] RAG index rebuilt if tracker CSV changed (`python3 rag/cli.py build`)
- [ ] Tracker CSV updated if application submitted
- [ ] Outcome recorded if response received (`python3 rag/cli.py feedback ...`)
