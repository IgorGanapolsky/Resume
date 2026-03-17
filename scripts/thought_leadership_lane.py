#!/usr/bin/env python3
"""Content-Led Authority / Thought Leadership generator.

Reads recent swarm metrics and CI outcomes to generate highly technical,
empirical LinkedIn posts to establish authority.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
QUEUE_JSON = ROOT / "linkedin" / "linkedin_post_queue.json"
METRICS_PROM = ROOT / "applications" / "job_applications" / "swarm_metrics.prom"
TRACKER_CSV = ROOT / "applications" / "job_applications" / "application_tracker.csv"
FALLBACK_POST = (
    "System update: Swarm architecture scaling successfully. "
    "Metrics logging to Prometheus is live. "
    "Zero manual intervention required today. "
    "#AI #Automation #Observability"
)


def call_llm(prompt: str) -> str:
    """Calls Anthropic Claude to generate content."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return FALLBACK_POST

    data = {
        "model": "claude-3-5-sonnet-20240620",
        "max_tokens": 512,
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(data).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))["content"][0]["text"]
    except Exception as e:
        print(f"WARN: LLM unavailable, using fallback post. error={e}")
        return FALLBACK_POST


def main():
    if not QUEUE_JSON.exists():
        print(f"Skipping: {QUEUE_JSON} not found.")
        return 0

    try:
        queue_data = json.loads(QUEUE_JSON.read_text(encoding="utf-8"))
    except Exception:
        queue_data = {"queue": []}

    metrics = (
        METRICS_PROM.read_text(encoding="utf-8")
        if METRICS_PROM.exists()
        else "No recent metrics available."
    )

    # Simple count from tracker (last 24 hours)
    applied_count = 0
    if TRACKER_CSV.exists():
        import csv

        with TRACKER_CSV.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            today_iso = datetime.now(timezone.utc).date().isoformat()
            for row in reader:
                if row.get("Status") == "Applied" and today_iso in row.get(
                    "Submission Verified At", ""
                ):
                    applied_count += 1

    prompt = f"""You are a CTO building an autonomous AI Swarm to manage your own engineering workflows.
Instead of just sharing metrics, write a "Teachable" LinkedIn post (max 150 words) that shares a distinct PHILOSOPHY or FRAMEWORK.

Topic ideas based on recent work:
- Why "consensus-based review" beats single-model agents.
- The "Integration is a social problem" POV for Forward-Deployed roles.
- Why prompt engineering is secondary to cost-predictability and reliability.

Context for inspiration:
Applied today: {applied_count}
{metrics[:500]}

Vanessa's Advice: Stop gaming filters. Create a distinct, generous POV that forces a human review.
Format: Return ONLY the raw post content. No preambles. Use 2-3 hashtags like #AISwarm #EngineeringPhilosophy #TeachableContent."""

    print("Generating thought leadership post...")
    content = call_llm(prompt)

    new_id = max([p.get("id", 0) for p in queue_data.get("queue", [])] + [0]) + 1
    post = {
        "id": new_id,
        "title": f"Swarm Architect Metrics {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        "status": "pending",
        "content": content.strip(),
        "source": "autonomous_thought_leadership",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tags": ["AI", "AgentSwarm", "Automation", "Observability"],
    }

    queue_data.setdefault("queue", []).append(post)
    QUEUE_JSON.write_text(json.dumps(queue_data, indent=2), encoding="utf-8")

    print(f"Successfully appended post to {QUEUE_JSON}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
