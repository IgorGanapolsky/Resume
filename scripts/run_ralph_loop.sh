#!/bin/bash
# Ralph Loop - local autonomous supervisor runner
# Run from repo root. Called by OpenClaw cron every 30 min.
cd "$(dirname "$0")/.."

# Export required submit secrets from local JSON files
export CI_SUBMIT_PROFILE_JSON=$(python3 -c "import json; print(json.dumps(json.load(open('applications/job_applications/candidate_profile.json'))))" 2>/dev/null || echo "")
export CI_SUBMIT_ANSWERS_JSON=$(python3 -c "import json; print(json.dumps(json.load(open('applications/job_applications/submit_answers.json'))))" 2>/dev/null || echo "")

python3 scripts/autonomous_supervisor.py \
  --max-new-jobs 15 \
  --agent-runtime auto \
  --fit-threshold 70 \
  --remote-min-score 0 \
  --max-submit-jobs 5 \
  --target-applied 5 \
  --quarantine-blocked \
  --max-parallel 3 \
  --execute-submissions \
  --report applications/job_applications/autonomous_supervisor_report.json \
  || true

# Always rebuild RAG regardless of submit outcome
python3 rag/cli.py build

# Notify OpenClaw
openclaw system event --text "Ralph Loop local run complete. Check autonomous_supervisor_report.json for results." --mode now
