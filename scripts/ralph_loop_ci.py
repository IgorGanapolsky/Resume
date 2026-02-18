#!/usr/bin/env python3
"""Continuous Ralph loop for GitHub Actions.

This job discovers new roles from remote job feeds, creates draft artifacts,
updates the application tracker, and keeps the RAG index fresh.

It intentionally does not perform irreversible portal submissions from CI.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import re
import urllib.request
from pathlib import Path
from typing import Dict, Iterable, List

ROOT = Path(__file__).resolve().parents[1]
TRACKER_CSV = ROOT / "applications" / "job_applications" / "application_tracker.csv"
APPLICATIONS_DIR = ROOT / "applications"
BASE_RESUME = ROOT / "resumes" / "Igor_Ganapolsky_AI_Systems_Engineer_2026-02-17.html"

ROLE_RE = re.compile(
    r"(software|ai|ml|machine learning|platform|infrastructure|infra|backend|full[- ]?stack|"
    r"devops|site reliability|sre|distributed systems|agent)",
    re.IGNORECASE,
)
LOCATION_RE = re.compile(
    r"(remote|hybrid|florida|south florida|miami|boca|fort lauderdale|west palm|united states|usa|us)",
    re.IGNORECASE,
)


def _fetch_json(url: str) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": "ResumeRalphLoop/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _slug(value: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return out or "company"


def _safe_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def discover_remotive() -> Iterable[Dict[str, str]]:
    try:
        data = _fetch_json("https://remotive.com/api/remote-jobs")
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    jobs = data.get("jobs", [])
    if not isinstance(jobs, list):
        return []
    out: List[Dict[str, str]] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        out.append(
            {
                "source": "remotive",
                "company": _safe_text(str(job.get("company_name", ""))),
                "title": _safe_text(str(job.get("title", ""))),
                "location": _safe_text(
                    str(job.get("candidate_required_location", "Remote"))
                ),
                "salary": _safe_text(str(job.get("salary", ""))),
                "job_type": _safe_text(str(job.get("job_type", ""))),
                "url": _safe_text(str(job.get("url", ""))),
                "tags": ";".join(
                    [_slug(str(t)) for t in job.get("tags", []) if str(t).strip()]
                ),
            }
        )
    return out


def discover_remoteok() -> Iterable[Dict[str, str]]:
    try:
        data = _fetch_json("https://remoteok.com/api")
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out: List[Dict[str, str]] = []
    for job in data:
        if not isinstance(job, dict):
            continue
        title = _safe_text(str(job.get("position", "")))
        company = _safe_text(str(job.get("company", "")))
        url = _safe_text(str(job.get("url", "")))
        if not title or not company or not url:
            continue
        tags = job.get("tags") if isinstance(job.get("tags"), list) else []
        out.append(
            {
                "source": "remoteok",
                "company": company,
                "title": title,
                "location": _safe_text(str(job.get("location", "Remote"))),
                "salary": _safe_text(str(job.get("salary", ""))),
                "job_type": _safe_text(str(job.get("employment_type", ""))),
                "url": url,
                "tags": ";".join([_slug(str(t)) for t in tags if str(t).strip()]),
            }
        )
    return out


def is_relevant(job: Dict[str, str]) -> bool:
    hay = " ".join(
        [
            job.get("title", ""),
            job.get("company", ""),
            job.get("location", ""),
            job.get("job_type", ""),
            job.get("tags", "").replace(";", " "),
        ]
    )
    return bool(ROLE_RE.search(hay) and LOCATION_RE.search(hay))


def read_tracker() -> tuple[List[str], List[Dict[str, str]]]:
    with TRACKER_CSV.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    return fieldnames, rows


def write_tracker(fieldnames: List[str], rows: List[Dict[str, str]]) -> None:
    with TRACKER_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def create_artifacts(job: Dict[str, str], today: str) -> Dict[str, str]:
    company = job["company"]
    role = job["title"]
    company_slug = _slug(company)
    role_slug = _slug(role)[:64]
    job_id = hashlib.sha1(job["url"].encode("utf-8")).hexdigest()[:8]

    base_dir = APPLICATIONS_DIR / company_slug
    jobs_dir = base_dir / "jobs"
    covers_dir = base_dir / "cover_letters"
    resumes_dir = base_dir / "tailored_resumes"
    subs_dir = base_dir / "submissions"
    for d in [jobs_dir, covers_dir, resumes_dir, subs_dir]:
        d.mkdir(parents=True, exist_ok=True)

    job_md = jobs_dir / f"{today}_{company_slug}_{role_slug}_{job_id}.md"
    cover_md = covers_dir / f"{today}_{company_slug}_{role_slug}.md"
    resume_html = resumes_dir / f"{today}_{company_slug}_{role_slug}.html"

    if not job_md.exists():
        job_md.write_text(
            "\n".join(
                [
                    f"# {company} - {role}",
                    "",
                    f"- Captured: {today}",
                    f"- URL: {job['url']}",
                    f"- Source: {job.get('source', 'unknown')}",
                    f"- Location: {job.get('location', '') or 'Unknown'}",
                    f"- Job Type: {job.get('job_type', '') or 'Unknown'}",
                    f"- Salary: {job.get('salary', '') or 'Not listed'}",
                    "",
                    "## Notes",
                    "- Added by Ralph Loop CI for review and application planning.",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    if not cover_md.exists():
        cover_md.write_text(
            "\n".join(
                [
                    f"Subject: Interest in {role}",
                    "",
                    f"Hello {company} team,",
                    "",
                    f"I am interested in the {role} opportunity. "
                    "My background is in production AI/software systems and platform engineering.",
                    "",
                    "Links:",
                    "- GitHub: https://github.com/IgorGanapolsky",
                    "- LinkedIn: https://www.linkedin.com/in/igor-ganapolsky/",
                    "",
                    "Thank you for your consideration.",
                    "",
                    "Igor Ganapolsky",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    if BASE_RESUME.exists() and not resume_html.exists():
        resume_html.write_text(
            BASE_RESUME.read_text(encoding="utf-8"), encoding="utf-8"
        )

    return {
        "job_md": str(job_md.relative_to(ROOT)),
        "cover_stem": cover_md.stem,
    }


def infer_method(url: str) -> str:
    u = url.lower()
    if "ashbyhq.com" in u:
        return "ashby"
    if "greenhouse.io" in u:
        return "greenhouse"
    if "lever.co" in u:
        return "lever"
    if "workday" in u:
        return "workday"
    if "linkedin.com/jobs" in u:
        return "linkedin"
    return "direct"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-new-jobs", type=int, default=10)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    today = dt.date.today().isoformat()
    fieldnames, rows = read_tracker()
    existing_urls = {(_safe_text(r.get("Career Page URL", "")).lower()) for r in rows}
    existing_pairs = {
        (
            _safe_text(r.get("Company", "")).lower(),
            _safe_text(r.get("Role", "")).lower(),
        )
        for r in rows
    }

    discovered = list(discover_remotive()) + list(discover_remoteok())
    relevant = [j for j in discovered if is_relevant(j) and j.get("url")]

    added = 0
    for job in relevant:
        if added >= args.max_new_jobs:
            break
        url = _safe_text(job["url"]).lower()
        pair = (_safe_text(job["company"]).lower(), _safe_text(job["title"]).lower())
        if url in existing_urls or pair in existing_pairs:
            continue
        artifacts = create_artifacts(job, today)
        row = {
            "Company": job["company"],
            "Role": job["title"],
            "Location": job.get("location", "Remote"),
            "Salary Range": job.get("salary", ""),
            "Status": "Draft",
            "Date Applied": "",
            "Follow Up Date": "",
            "Response": "",
            "Interview Stage": "Initial",
            "Days To Response": "",
            "Response Type": "",
            "Cover Letter Used": artifacts["cover_stem"],
            "What Worked": "",
            "Tags": job.get("tags", "") or "ai;software",
            "Notes": f"Discovered by Ralph Loop CI on {today}; pending review and submission. Job capture: {artifacts['job_md']}",
            "Career Page URL": job["url"],
        }
        # Preserve column order from tracker.
        rows.append({k: row.get(k, "") for k in fieldnames})
        existing_urls.add(url)
        existing_pairs.add(pair)
        added += 1

    print(f"Discovered: {len(discovered)}")
    print(f"Relevant: {len(relevant)}")
    print(f"Added: {added}")
    if not args.dry_run and added:
        write_tracker(fieldnames, rows)
        print(f"Tracker updated: {TRACKER_CSV}")


if __name__ == "__main__":
    main()
