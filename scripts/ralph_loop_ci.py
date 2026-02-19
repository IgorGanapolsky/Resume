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
import html
import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
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
FDE_TITLE_RE = re.compile(
    r"(forward[- ]?deployed|solutions engineer|customer engineer|implementation engineer|"
    r"technical consultant|partner engineer)",
    re.IGNORECASE,
)
FDE_SIGNAL_RE = re.compile(
    r"(customer|client|stakeholder|api|integration|embedded|executive)",
    re.IGNORECASE,
)
PYTHON_SIGNAL_RE = re.compile(r"\bpython\b", re.IGNORECASE)
VOICE_SIGNAL_RE = re.compile(
    r"(voice|audio|speech|tts|asr|call center|ivr)", re.IGNORECASE
)
NON_TECH_RE = re.compile(
    r"(account executive|sales|recruiter|attorney|counsel|office assistant|marketing)",
    re.IGNORECASE,
)
LOCATION_RE = re.compile(
    r"(remote|hybrid|florida|south florida|miami|boca|fort lauderdale|west palm|united states|usa|us)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RoleProfile:
    track: str
    score: int
    signals: List[str]
    is_relevant: bool


def _fetch_json(url: str) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": "ResumeRalphLoop/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _slug(value: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return out or "company"


def _safe_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _strip_html(value: str) -> str:
    no_tags = re.sub(r"<[^>]+>", " ", value or "")
    return _safe_text(html.unescape(no_tags))


def _replace_once(text: str, old: str, new: str) -> str:
    if old not in text:
        return text
    return text.replace(old, new, 1)


def classify_role(job: Dict[str, str]) -> RoleProfile:
    title = job.get("title", "")
    hay = " ".join(
        [
            title,
            job.get("company", ""),
            job.get("location", ""),
            job.get("job_type", ""),
            job.get("tags", "").replace(";", " "),
            job.get("description", ""),
        ]
    )
    role_match = bool(ROLE_RE.search(hay))
    location_match = bool(LOCATION_RE.search(hay))
    non_tech = bool(NON_TECH_RE.search(title))

    signals: List[str] = []
    score = 0
    if FDE_TITLE_RE.search(hay):
        signals.append("fde-title")
        score += 3
    if FDE_SIGNAL_RE.search(hay):
        signals.append("customer-integration")
        score += 2
    if PYTHON_SIGNAL_RE.search(hay):
        signals.append("python")
        score += 1
    if VOICE_SIGNAL_RE.search(hay):
        signals.append("voice-audio")
        score += 1

    track = "fde" if score >= 3 else "general"
    is_relevant = role_match and location_match and not non_tech
    return RoleProfile(
        track=track,
        score=score,
        signals=sorted(set(signals)),
        is_relevant=is_relevant,
    )


def _profile_tags(profile: RoleProfile) -> List[str]:
    tags: List[str] = []
    if profile.track == "fde":
        tags.extend(["forward-deployed", "customer-facing", "api-integration"])
    if "python" in profile.signals:
        tags.append("python-requested")
    if "voice-audio" in profile.signals:
        tags.append("voice-ai")
    return tags


def _merge_tags(existing: str, additions: List[str]) -> str:
    tokens = [t.strip() for t in (existing or "").split(";") if t.strip()]
    token_set = set(tokens)
    for token in additions:
        if token not in token_set:
            tokens.append(token)
            token_set.add(token)
    return ";".join(tokens)


def extract_key_requirements(job: Dict[str, str], profile: RoleProfile) -> List[str]:
    requirements: List[str] = []
    if profile.track == "fde":
        requirements.extend(
            [
                "End-to-end ownership: architecture through deployment and iteration.",
                "Daily collaboration with customer engineering and stakeholder teams.",
                "Strong API integration and reliable production delivery.",
                "Clear communication and problem-solving in ambiguous environments.",
            ]
        )
    if "python" in profile.signals:
        requirements.append("Python proficiency for integration-heavy services.")
    if "voice-audio" in profile.signals:
        requirements.append("Experience with voice/audio AI workflows is a plus.")
    if not requirements:
        requirements.append(
            "Production software engineering with cloud systems, APIs, and reliability focus."
        )
    return requirements


def build_cover_letter(job: Dict[str, str], profile: RoleProfile) -> str:
    company = job["company"]
    role = job["title"]
    intro = (
        f"I am interested in the {role} opportunity. "
        "My background is in production AI/software systems and platform engineering."
    )
    if profile.track == "fde":
        intro = (
            f"I am interested in the {role} opportunity because I enjoy "
            "customer-facing, integration-heavy delivery work."
        )

    highlights = [
        "- Led end-to-end delivery of AI features from prototype to production at Subway.",
        "- Built secure APIs and cloud workflows on GCP integrating LLM features with existing services.",
        "- Partnered with product/data stakeholders to define prompts, tools, and escalation rules.",
    ]
    if profile.track != "fde":
        highlights = [
            "- Built production AI/software systems with strong reliability, observability, and CI/CD.",
            "- Delivered cloud-native services on GCP/AWS and integrated LLM features into existing stacks.",
        ]

    lines = [
        f"Subject: Interest in {role}",
        "",
        f"Hello {company} team,",
        "",
        intro,
        "",
        "Why I may be a good fit:",
        *highlights,
        "",
        "Links:",
        "- GitHub: https://github.com/IgorGanapolsky",
        "- LinkedIn: https://www.linkedin.com/in/igor-ganapolsky/",
        "",
        "Thank you for your consideration.",
        "",
        "Igor Ganapolsky",
    ]
    return "\n".join(lines) + "\n"


def tailor_resume_html(base_html: str, profile: RoleProfile) -> str:
    if profile.track != "fde":
        return base_html

    out = base_html
    out = _replace_once(
        out,
        "<p>Senior AI Systems Engineer (LLM Infrastructure, Cloud, Distributed Systems)</p>",
        "<p>Forward-Deployed AI/Software Engineer (LLM Integrations, API Delivery, Customer-Facing AI Systems)</p>",
    )
    out = _replace_once(
        out,
        (
            "<p>Senior AI and Full-Stack Engineer with 15+ years of professional "
            "software development experience and 6+ years of significant full-stack "
            "development responsibility. Focused on building production AI systems: "
            "LLM gateways, agent/tool execution, retrieval-augmented workflows, and "
            "cloud-native services on Google Cloud Platform and AWS. Provides technical "
            "leadership across architecture, code review, and AI adoption; mentors teams "
            "on reliability, cost/latency tradeoffs, and observability. Proven track "
            "record building scalable, secure, performant systems aligned with business "
            "objectives.</p>"
        ),
        (
            "<p>Senior AI and Full-Stack Engineer with 15+ years of professional "
            "software development experience and 6+ years of significant full-stack "
            "development responsibility. Builds and ships production AI systems end-to-end, "
            "from architecture and implementation through rollout and iteration, including "
            "LLM gateways, tool-using agents, retrieval-augmented workflows, and cloud-native "
            "services on Google Cloud Platform and AWS. Partners closely with product, data, "
            "and engineering stakeholders to translate ambiguous requirements into reliable "
            "integrations, emphasizing observability, cost/latency tradeoffs, and measurable "
            "business impact.</p>"
        ),
    )
    out = _replace_once(
        out,
        "<p><strong>CORE COMPETENCIES</strong></p>",
        "<p><strong>FORWARD-DEPLOYED COMPETENCIES</strong></p>",
    )
    out = _replace_once(
        out,
        (
            "<p>\n• AI Systems / Infra: distributed debugging, reliability, observability "
            "(logs/metrics/traces), cost/latency tuning, incident-minded engineering<br />\n"
            "• L7 traffic &amp; platform: API gateways, HTTP/2 concepts, service mesh "
            "fundamentals (Istio/Envoy), production hardening and rollout safety<br />\n"
            "• Full-Stack Development: React Native (New Architecture, Fabric), Node.js, REST "
            "APIs, microservices, cloud-based systems<br />\n"
            "• Cloud Platforms: GCP (Vertex AI, Dialogflow, BigQuery, Cloud Functions, Cloud "
            "Build), AWS (Lambda, Bedrock, S3)<br />\n"
            "• DevOps &amp; CI/CD: GitHub Actions, GCP Cloud Build, CircleCI, Azure DevOps, "
            "Gradle, containerization<br />\n"
            "• Leadership: architecture reviews, code review, mentoring engineers, clear "
            "communication with engineers and stakeholders\n</p>"
        ),
        (
            "<p>\n• Customer-facing delivery: end-to-end ownership from technical discovery and "
            "architecture to implementation, rollout, and iteration<br />\n"
            "• Integration engineering: API gateways, REST APIs, service integrations, auth/rate "
            "limiting, feature flags, production rollout safety<br />\n"
            "• AI application development: LLM-enabled product features, React Native (New "
            "Architecture, Fabric), Node.js, microservices<br />\n"
            "• Reliability and performance: observability (logs/metrics/traces), fallback "
            "behavior, distributed debugging, latency/cost tuning<br />\n"
            "• Cloud platforms: GCP (Vertex AI, Dialogflow, BigQuery, Cloud Functions, Cloud "
            "Build), AWS (Lambda, Bedrock, S3)<br />\n"
            "• Communication and leadership: architecture reviews, code review, mentoring "
            "engineers, clear technical communication with stakeholders\n</p>"
        ),
    )
    out = _replace_once(
        out,
        (
            "<li><p>Led design and delivery of end-to-end AI features from prototype to "
            "production, including LLM-backed search, personalized recommendations, and "
            "conversational AI assistant serving millions of users monthly</p></li>"
        ),
        (
            "<li><p>Owned end-to-end delivery of AI features from prototype to production, "
            "including LLM-backed search, personalized recommendations, and conversational AI "
            "assistant experiences serving millions of users monthly</p></li>"
        ),
    )
    out = _replace_once(
        out,
        "reducing customer service load by 35%",
        "reducing customer service load by <strong>35%</strong>",
    )
    out = _replace_once(
        out,
        (
            "• Autonomous AI trading system (github.com/IgorGanapolsky/trading): "
            "multi-model LLM gateway with Tetrate Agent Router Service (TARS), cost-aware "
            "routing and provider fallbacks, and self-healing CI for continuous reliability"
        ),
        (
            "• Autonomous AI trading system (github.com/IgorGanapolsky/trading): "
            "multi-model LLM gateway with API-first integration design, Tetrate Agent Router "
            "Service (TARS), cost-aware routing/provider fallbacks, and self-healing CI for "
            "continuous reliability"
        ),
    )
    out = _replace_once(
        out, "reduced support volume 40%", "reduced support volume <strong>40%</strong>"
    )
    return out


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
                "description": _strip_html(str(job.get("description", ""))),
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
                "description": _strip_html(str(job.get("description", ""))),
                "tags": ";".join([_slug(str(t)) for t in tags if str(t).strip()]),
            }
        )
    return out


def is_relevant(job: Dict[str, str]) -> bool:
    return classify_role(job).is_relevant


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


def create_artifacts(
    job: Dict[str, str], today: str, profile: RoleProfile
) -> Dict[str, str]:
    company = job["company"]
    role = job["title"]
    company_slug = _slug(company)
    role_slug = _slug(role)[:64]
    job_id = hashlib.sha256(job["url"].encode("utf-8")).hexdigest()[:8]

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
        requirements = extract_key_requirements(job, profile)
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
                    "## Key Requirements",
                    *[f"- {line}" for line in requirements],
                    "",
                    "## Notes",
                    "- Added by Ralph Loop CI for review and application planning.",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    if not cover_md.exists():
        cover_md.write_text(build_cover_letter(job, profile), encoding="utf-8")

    if BASE_RESUME.exists() and not resume_html.exists():
        resume_html.write_text(
            tailor_resume_html(BASE_RESUME.read_text(encoding="utf-8"), profile),
            encoding="utf-8",
        )

    return {
        "job_md": str(job_md.relative_to(ROOT)),
        "cover_stem": cover_md.stem,
    }


def infer_method(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()

    if host == "ashbyhq.com" or host.endswith(".ashbyhq.com"):
        return "ashby"
    if host == "greenhouse.io" or host.endswith(".greenhouse.io"):
        return "greenhouse"
    if host == "lever.co" or host.endswith(".lever.co"):
        return "lever"
    if "workday" in host:
        return "workday"
    if (host == "linkedin.com" or host.endswith(".linkedin.com")) and path.startswith(
        "/jobs"
    ):
        return "linkedin"
    return "direct"


def _planned_cover_stem(company: str, role: str, today: str) -> str:
    return f"{today}_{_slug(company)}_{_slug(role)[:64]}"


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
    relevant: List[tuple[Dict[str, str], RoleProfile]] = []
    for job in discovered:
        if not job.get("url"):
            continue
        profile = classify_role(job)
        if profile.is_relevant:
            relevant.append((job, profile))
    relevant.sort(key=lambda item: item[1].score, reverse=True)

    added = 0
    for job, profile in relevant:
        if added >= args.max_new_jobs:
            break
        url = _safe_text(job["url"]).lower()
        pair = (_safe_text(job["company"]).lower(), _safe_text(job["title"]).lower())
        if url in existing_urls or pair in existing_pairs:
            continue
        if args.dry_run:
            artifacts = {
                "job_md": "(dry-run)",
                "cover_stem": _planned_cover_stem(job["company"], job["title"], today),
            }
        else:
            artifacts = create_artifacts(job, today, profile)
        merged_tags = _merge_tags(
            job.get("tags", "") or "ai;software", _profile_tags(profile)
        )
        signals = ",".join(profile.signals) if profile.signals else "none"
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
            "Tags": merged_tags,
            "Notes": (
                f"Discovered by Ralph Loop CI on {today}; pending review and submission. "
                f"Role track={profile.track}; signals={signals}. "
                f"Job capture: {artifacts['job_md']}"
            ),
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
