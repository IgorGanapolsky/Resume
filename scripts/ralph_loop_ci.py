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
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
TRACKER_CSV = ROOT / "applications" / "job_applications" / "application_tracker.csv"
APPLICATIONS_DIR = ROOT / "applications"
BASE_RESUME = ROOT / "resumes" / "Igor_Ganapolsky_AI_Systems_Engineer_2026-02-17.html"

ROLE_RE = re.compile(
    r"(software|ai|ml|machine learning|platform|infrastructure|infra|backend|full[- ]?stack|"
    r"devops|site reliability|sre|distributed systems|agent)",
    re.IGNORECASE,
)
TECH_TITLE_RE = re.compile(
    r"(engineer|developer|devops|sre|site reliability|architect|ml|ai|data engineer|"
    r"backend|frontend|full[- ]?stack|platform|infrastructure|ios|android|qa)",
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
    r"(account executive|sales|recruiter|attorney|counsel|office assistant|marketing|"
    r"content manager|revenue operations|client support|customer support specialist|"
    r"operations manager|community manager)",
    re.IGNORECASE,
)
LOCATION_RE = re.compile(
    r"(remote|hybrid|florida|south florida|miami|boca|fort lauderdale|west palm|united states|usa|us)",
    re.IGNORECASE,
)
REMOTE_POSITIVE_RE = re.compile(
    r"(remote|work from home|wfh|distributed|anywhere|home[- ]?based)",
    re.IGNORECASE,
)
REMOTE_HYBRID_RE = re.compile(r"\bhybrid\b", re.IGNORECASE)
REMOTE_NEGATIVE_RE = re.compile(
    r"(on[- ]?site|onsite|in[- ]?office|office[- ]?based|relocation required)",
    re.IGNORECASE,
)
TRACKER_EXTRA_FIELDS = (
    "Remote Policy",
    "Remote Likelihood Score",
    "Remote Evidence",
    "Submission Lane",
)
AUTO_SUBMIT_METHODS = {"ashby", "greenhouse", "lever"}


@dataclass(frozen=True)
class RoleProfile:
    track: str
    score: int
    signals: List[str]
    is_relevant: bool
    philosophy: str = ""
    distinctive_achievements: List[str] = field(default_factory=list)


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


# Regex to extract ATS apply links from HTML job descriptions.
_ATS_URL_RE = re.compile(
    r'href=["\']?(https?://[^"\'>\s]*'
    r"(?:ashbyhq\.com|greenhouse\.io|lever\.co|jobs\.lever\.co)"
    r'[^"\'>\s]*)',
    re.I,
)


def _extract_ats_url(description_html: str) -> str:
    """Extract a direct ATS URL (Ashby/Greenhouse/Lever) from HTML if present."""
    m = _ATS_URL_RE.search(description_html or "")
    return m.group(1) if m else ""


def _resolve_redirect_url(url: str, timeout: int = 8) -> str:
    """Follow redirects on a feed URL to discover the final ATS host.

    Returns the resolved URL on success, or the original on failure.
    This is best-effort and designed to be fast (HEAD only).
    """
    try:
        req = urllib.request.Request(
            url, method="HEAD", headers={"User-Agent": "ResumeRalphLoop/1.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            final = resp.url
            if final and final != url:
                return final
    except Exception:
        pass
    return url


def _replace_once(text: str, old: str, new: str) -> str:
    if old not in text:
        return text
    return text.replace(old, new, 1)


def _html_to_text(value: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\\1>", " ", value or "")
    text = re.sub(r"(?i)<br\\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\\s*>", "\n\n", text)
    text = re.sub(r"(?i)</li\\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    lines = [re.sub(r"\\s+", " ", line).strip() for line in text.splitlines()]
    compact = [line for line in lines if line]
    return "\n".join(compact) + ("\n" if compact else "")


def _write_simple_docx(text: str, out_path: Path) -> None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        lines = ["Resume"]

    body = "".join(
        f'<w:p><w:r><w:t xml:space="preserve">{escape(line)}</w:t></w:r></w:p>'
        for line in lines
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}"
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" '
        'w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>'
        "</w:body></w:document>"
    )
    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml)
        zf.writestr("_rels/.rels", rels_xml)
        zf.writestr("word/document.xml", document_xml)


def _ensure_docx_from_html(html_path: Path, docx_path: Path) -> None:
    if docx_path.exists() or not html_path.exists():
        return
    html_text = html_path.read_text(encoding="utf-8", errors="replace")
    _write_simple_docx(_html_to_text(html_text), docx_path)


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
    role_match = bool(TECH_TITLE_RE.search(title) or ROLE_RE.search(hay))
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

    # Vanessa POV logic: Add distinctive philosophy and achievements
    philosophy = ""
    distinctive = []
    if track == "fde":
        philosophy = "Integration is a social problem, not just a technical one; I build 'API-first' relationships, not just endpoints."
        distinctive = [
            "Architected a self-healing CI pipeline for multi-model LLM consensus that reduced manual debug time by 80%.",
            "Pioneered a 'shipping small experiments weekly' approach for LLM features at Subway, beating the standard quarterly release cycle.",
        ]
    else:
        philosophy = "Production AI is about reliability and cost-predictability, not just prompt engineering."
        distinctive = [
            "Built a semantic memory system using LanceDB that reduced context window 'forgetting' across 200+ autonomous agent turns.",
            "Optimized LLM inference pipelines to maintain <200ms latency while reducing token spend by 40%.",
        ]

    return RoleProfile(
        track=track,
        score=score,
        signals=sorted(set(signals)),
        is_relevant=is_relevant,
        philosophy=philosophy,
        distinctive_achievements=distinctive,
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


def _ensure_tracker_fields(
    fieldnames: List[str], rows: List[Dict[str, str]]
) -> List[str]:
    out = list(fieldnames)
    missing = [name for name in TRACKER_EXTRA_FIELDS if name not in out]
    if not missing:
        return out
    out.extend(missing)
    for row in rows:
        for name in missing:
            row.setdefault(name, "")
    return out


def _host_matches_domain(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


def infer_remote_profile(job: Dict[str, str]) -> tuple[str, int, List[str]]:
    hay = " ".join(
        [
            str(job.get("location", "") or ""),
            str(job.get("job_type", "") or ""),
            str(job.get("tags", "") or "").replace(";", " "),
            str(job.get("description", "") or ""),
        ]
    ).lower()
    host = (urllib.parse.urlsplit(str(job.get("url", "") or "")).hostname or "").lower()
    evidence: List[str] = []
    policy = "unknown"
    score = 45

    if REMOTE_NEGATIVE_RE.search(hay):
        policy = "onsite"
        score = 10
        evidence.append("onsite_keyword")
    elif REMOTE_HYBRID_RE.search(hay):
        policy = "hybrid"
        score = 65
        evidence.append("hybrid_keyword")
    elif REMOTE_POSITIVE_RE.search(hay):
        policy = "remote"
        score = 85
        evidence.append("remote_keyword")

    if _host_matches_domain(host, "remoteok.com") or _host_matches_domain(
        host, "remotive.com"
    ):
        evidence.append("remote_feed_source")
        score = min(95, score + 5)

    if policy == "onsite":
        score = min(score, 25)

    return policy, max(0, min(100, score)), sorted(set(evidence))


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

    # Vanessa-style POV intro
    intro = (
        f"I am interested in the {role} opportunity. "
        f"My philosophy is that {profile.philosophy.lower().strip('.')}. "
        "I build production AI/software systems where reliability is a non-negotiable."
    )

    highlights = [f"- {ach}" for ach in profile.distinctive_achievements]
    # Add one core competence bullet
    if profile.track == "fde":
        highlights.append(
            "- Delivered customer-facing, integration-heavy delivery work with product/data stakeholders to define prompts, tools, and escalation rules."
        )
    else:
        highlights.append(
            "- Delivered cloud-native services on GCP/AWS and integrated LLM features into existing stacks."
        )

    lines = [
        f"Subject: Interest in {role}",
        "",
        f"Hello {company} team,",
        "",
        intro,
        "",
        "How I've lived this philosophy recently:",
        *highlights,
        "",
        "My work is grounded in proof, not just prompts. You can find the code for my autonomous agent architectures at:",
        "- GitHub: https://github.com/IgorGanapolsky",
        "- Technical POV: https://www.linkedin.com/in/igor-ganapolsky/",
        "",
        "Thank you for your consideration.",
        "",
        "Igor Ganapolsky",
    ]
    return "\n".join(lines) + "\n"


def tailor_resume_html(base_html: str, profile: RoleProfile) -> str:
    out = base_html

    # Vanessa POV Injection into Summary
    pov_summary = (
        f"<p>Senior AI and Full-Stack Engineer with 15+ years of experience. "
        f"<strong>Philosophy: {profile.philosophy}</strong> Builds and ships production AI systems "
        "end-to-end, from architecture and implementation through rollout and iteration.</p>"
    )

    # Generic summary replacement (assuming standard base resume structure)
    out = re.sub(
        r"<p>Senior AI and Full-Stack Engineer with 15\+ years.*?</p>",
        pov_summary,
        out,
        flags=re.DOTALL,
    )

    # Inject Distinctive Achievements as "Featured Impact" bullets
    featured_bullets = "".join(
        [
            f"<li><p><strong>Featured Impact:</strong> {ach}</p></li>"
            for ach in profile.distinctive_achievements
        ]
    )
    out = _replace_once(out, "<ul>", f"<ul>\n{featured_bullets}")

    if profile.track == "fde":
        out = _replace_once(
            out,
            "<p>Senior AI Systems Engineer (LLM Infrastructure, Cloud, Distributed Systems)</p>",
            "<p>Forward-Deployed AI/Software Engineer (LLM Integrations, API Delivery, Customer-Facing AI Systems)</p>",
        )
        out = _replace_once(
            out,
            "<p><strong>CORE COMPETENCIES</strong></p>",
            "<p><strong>FORWARD-DEPLOYED COMPETENCIES</strong></p>",
        )
        out = out.replace(
            "customer service load by 35%",
            "customer service load by <strong>35%</strong>",
        )
        out = out.replace(
            "reduced support volume 40%",
            "reduced support volume <strong>40%</strong>",
        )

    # ... rest of specific replacements ...
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
        desc_html = str(job.get("description", ""))
        listing_url = _safe_text(str(job.get("url", "")))
        # Prefer a direct ATS link found in the description over the feed listing URL.
        ats_url = _extract_ats_url(desc_html)
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
                "url": ats_url or listing_url,
                "listing_url": listing_url,
                "description": _strip_html(desc_html),
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
        listing_url = _safe_text(str(job.get("url", "")))
        if not title or not company or not listing_url:
            continue
        tags = job.get("tags") if isinstance(job.get("tags"), list) else []
        desc_html = str(job.get("description", ""))
        # Prefer a direct ATS link found in the description over the feed listing URL.
        ats_url = _extract_ats_url(desc_html)
        out.append(
            {
                "source": "remoteok",
                "company": company,
                "title": title,
                "location": _safe_text(str(job.get("location", "Remote"))),
                "salary": _safe_text(str(job.get("salary", ""))),
                "job_type": _safe_text(str(job.get("employment_type", ""))),
                "url": ats_url or listing_url,
                "listing_url": listing_url,
                "description": _strip_html(desc_html),
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


def _fetch_html(url: str) -> str:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ResumeRalphLoop/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


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
    resume_docx = resumes_dir / f"{today}_{company_slug}_{role_slug}.docx"

    if not job_md.exists():
        html_content = _fetch_html(job["url"])
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
                    "## Full Description (HTML Snippet)",
                    f"```html\n{html_content[:5000]}\n```",
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
    _ensure_docx_from_html(resume_html, resume_docx)

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
    if host == "talentprise.com" or host.endswith(".talentprise.com"):
        return "talentprise"
    return "direct"


def infer_submission_lane(method: str) -> str:
    return "ci_auto" if method in AUTO_SUBMIT_METHODS else "manual"


def _planned_cover_stem(company: str, role: str, today: str) -> str:
    return f"{today}_{_slug(company)}_{_slug(role)[:64]}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-new-jobs", type=int, default=10)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    today = dt.date.today().isoformat()
    fieldnames, rows = read_tracker()
    fieldnames = _ensure_tracker_fields(fieldnames, rows)
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
        method = infer_method(job["url"])
        submission_lane = infer_submission_lane(method)
        remote_policy, remote_score, remote_evidence = infer_remote_profile(job)
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
            "Remote Policy": remote_policy,
            "Remote Likelihood Score": str(remote_score),
            "Remote Evidence": ";".join(remote_evidence),
            "Submission Lane": submission_lane,
            "Notes": (
                f"Discovered by Ralph Loop CI on {today}; pending review and submission. "
                f"Role track={profile.track}; signals={signals}; method={method}; lane={submission_lane}. "
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
