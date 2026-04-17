#!/usr/bin/env python3
"""Discovery and artifact generation helpers for Ralph workflows.

This module discovers new roles from remote job feeds, creates draft artifacts,
updates the application tracker, and keeps the RAG index fresh. Submit
execution happens elsewhere in the Ralph automation stack.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

try:
    from candidate_data import load_candidate_profile
except Exception:  # pragma: no cover - fallback keeps CI discovery resilient
    load_candidate_profile = None

ROOT = Path(__file__).resolve().parents[1]
TRACKER_CSV = ROOT / "applications" / "job_applications" / "application_tracker.csv"
APPLICATIONS_DIR = ROOT / "applications"
BASE_RESUME = ROOT / "resumes" / "Igor_Ganapolsky_AI_Systems_Engineer_2026-02-17.html"
COMPANY_BOARDS_CONFIG = (
    ROOT / "applications" / "job_applications" / "company_boards.json"
)

ROLE_RE = re.compile(
    r"(software|ai|ml|machine learning|platform|infrastructure|infra|backend|full[- ]?stack|"
    r"devops|site reliability|sre|distributed systems|agent|technical staff|member of technical staff)",
    re.IGNORECASE,
)
TECH_TITLE_RE = re.compile(
    r"(engineer|developer|devops|sre|site reliability|architect|ml|ai|data engineer|"
    r"backend|frontend|full[- ]?stack|platform|infrastructure|ios|android|qa|"
    r"technical staff|member of technical staff)",
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
ALLOWED_FETCH_SCHEMES = {"http", "https"}


def _candidate_contact() -> Dict[str, str]:
    fallback = {
        "full_name": "Igor Ganapolsky",
        "github": "https://github.com/IgorGanapolsky",
        "linkedin": "https://www.linkedin.com/in/igor-ganapolsky/",
    }
    if load_candidate_profile is None:
        return fallback
    try:
        profile = load_candidate_profile()
    except Exception:
        return fallback
    return {
        "full_name": profile.get("full_name", fallback["full_name"]),
        "github": profile.get("github", fallback["github"]),
        "linkedin": profile.get("linkedin", fallback["linkedin"]),
    }


CANDIDATE_CONTACT = _candidate_contact()


@dataclass(frozen=True)
class RoleProfile:
    track: str
    score: int
    signals: List[str]
    is_relevant: bool
    philosophy: str = ""
    distinctive_achievements: List[str] = field(default_factory=list)


def _validate_fetch_url(url: str, *, allowed_hosts: Sequence[str] | None = None) -> None:
    parsed = urllib.parse.urlsplit(url)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()
    if scheme not in ALLOWED_FETCH_SCHEMES:
        raise ValueError(f"Unsupported fetch scheme: {scheme or '<none>'}")
    if not host:
        raise ValueError("Fetch URL must include a host")
    if allowed_hosts and host not in {item.lower() for item in allowed_hosts}:
        raise ValueError(f"Unexpected host for fetch: {host}")


def _open_url(request_or_url: urllib.request.Request | str, *, timeout: int):
    url = (
        request_or_url.full_url
        if isinstance(request_or_url, urllib.request.Request)
        else str(request_or_url)
    )
    _validate_fetch_url(url)
    return urllib.request.urlopen(request_or_url, timeout=timeout)  # nosec B310


def _fetch_json(url: str) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": "ResumeRalphLoop/1.0"})
    with _open_url(req, timeout=30) as resp:
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
        with _open_url(req, timeout=timeout) as resp:
            final = resp.url
            if final and final != url:
                return final
    except (ValueError, OSError, urllib.error.HTTPError, urllib.error.URLError):
        return url
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
        f'<w:p><w:r><w:t xml:space="preserve">{html.escape(line, quote=False)}</w:t></w:r></w:p>'
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


def _ashby_auto_submit_url_ok(url: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    if not (host == "ashbyhq.com" or host.endswith(".ashbyhq.com")):
        return False
    if "/form/" in path:
        return False
    segments = [segment for segment in path.split("/") if segment]
    return len(segments) >= 2


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
            "Built customer-facing AI delivery systems that tie API integrations, rollout discipline, and stakeholder alignment together.",
            "Shipped iterative LLM features in production without waiting for long release trains or handoff-heavy planning cycles.",
        ]
    else:
        philosophy = "Production AI is about reliability and cost-predictability, not just prompt engineering."
        distinctive = [
            "Built a semantic memory system with LanceDB to keep long-running agent sessions grounded in prior context.",
            "Optimized LLM inference and orchestration paths around latency, cost, and operational predictability.",
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
        f"The through-line in my recent work is straightforward: {profile.philosophy.lower().strip('.')}. "
        "I build production AI/software systems where reliability is non-negotiable."
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
        "Recent examples:",
        *highlights,
        "",
        "My work is grounded in proof, not just prompts. You can find the code for my autonomous agent architectures at:",
        f"- GitHub: {CANDIDATE_CONTACT['github']}",
        f"- Technical POV: {CANDIDATE_CONTACT['linkedin']}",
        "",
        "Thank you for your consideration.",
        "",
        CANDIDATE_CONTACT["full_name"],
    ]
    return "\n".join(lines) + "\n"


def tailor_resume_html(base_html: str, profile: RoleProfile) -> str:
    out = base_html

    # Keep the injected summary direct and factual instead of templated headers.
    pov_summary = (
        "<p>Senior AI and Full-Stack Engineer with 15+ years of experience. "
        f"Focused on {profile.philosophy.lower().strip('.')} and on shipping production AI systems "
        "end-to-end, from architecture and implementation through rollout and iteration.</p>"
    )

    # Generic summary replacement (assuming standard base resume structure)
    out = re.sub(
        r"<p>Senior AI and Full-Stack Engineer with 15\+ years.*?</p>",
        pov_summary,
        out,
        flags=re.DOTALL,
    )

    # Inject role-relevant evidence without static template labels.
    featured_bullets = "".join(
        [f"<li><p>{ach}</p></li>" for ach in profile.distinctive_achievements]
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


def _load_company_boards() -> List[Dict[str, str]]:
    if not COMPANY_BOARDS_CONFIG.exists():
        return []
    try:
        data = json.loads(COMPANY_BOARDS_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return []
    boards = data.get("boards") if isinstance(data, dict) else None
    if not isinstance(boards, list):
        return []
    out: List[Dict[str, str]] = []
    for entry in boards:
        if not isinstance(entry, dict):
            continue
        ats = _safe_text(str(entry.get("ats", "")).lower())
        slug = _safe_text(str(entry.get("slug", "")))
        company = _safe_text(str(entry.get("company", "")))
        if ats in {"greenhouse", "ashby"} and slug and company:
            out.append({"ats": ats, "slug": slug, "company": company})
    return out


def discover_greenhouse_board(slug: str, company: str) -> Iterable[Dict[str, str]]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    try:
        data = _fetch_json(url)
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
        title = _safe_text(str(job.get("title", "")))
        job_id = job.get("id")
        # Canonical Greenhouse URL. Many companies (Databricks, Stripe, ...)
        # return `absolute_url` pointing to their own careers page, which
        # breaks the Greenhouse adapter. Always route through job-boards.
        if job_id:
            apply_url = f"https://job-boards.greenhouse.io/{slug}/jobs/{job_id}"
        else:
            apply_url = _safe_text(str(job.get("absolute_url", "")))
        if not title or not apply_url:
            continue
        loc = job.get("location") or {}
        location = _safe_text(str(loc.get("name", "")) if isinstance(loc, dict) else "")
        # Greenhouse double-encodes HTML: unescape entities, then strip tags.
        raw_content = str(job.get("content", ""))
        description = _strip_html(html.unescape(raw_content))
        departments = job.get("departments") or []
        tags: List[str] = []
        if isinstance(departments, list):
            for d in departments:
                if isinstance(d, dict) and d.get("name"):
                    tags.append(_slug(str(d["name"])))
        out.append(
            {
                "source": f"greenhouse:{slug}",
                "company": company,
                "title": title,
                "location": location or "Remote",
                "salary": "",
                "job_type": "",
                "url": apply_url,
                "listing_url": apply_url,
                "description": description,
                "tags": ";".join(t for t in tags if t),
            }
        )
    return out


def discover_ashby_board(slug: str, company: str) -> Iterable[Dict[str, str]]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    try:
        data = _fetch_json(url)
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
        if job.get("isListed") is False:
            continue
        title = _safe_text(str(job.get("title", "")))
        apply_url = _safe_text(
            str(job.get("jobUrl") or job.get("applyUrl") or "")
        )
        if not title or not apply_url:
            continue
        location = _safe_text(str(job.get("location", "")))
        description = _safe_text(
            str(job.get("descriptionPlain") or "")
        ) or _strip_html(str(job.get("descriptionHtml", "")))
        department = _safe_text(str(job.get("department", "")))
        team = _safe_text(str(job.get("team", "")))
        tags = ";".join(_slug(t) for t in [department, team] if t)
        out.append(
            {
                "source": f"ashby:{slug}",
                "company": company,
                "title": title,
                "location": location or "Remote",
                "salary": "",
                "job_type": _safe_text(str(job.get("employmentType", ""))),
                "url": apply_url,
                "listing_url": apply_url,
                "description": description,
                "tags": tags,
            }
        )
    return out


def discover_company_boards() -> Iterable[Dict[str, str]]:
    """Fan out to per-company Greenhouse/Ashby boards defined in config.

    Each board call is isolated: a failure for one company does not stop others.
    """
    boards = _load_company_boards()
    aggregated: List[Dict[str, str]] = []
    for entry in boards:
        ats = entry["ats"]
        slug = entry["slug"]
        company = entry["company"]
        try:
            if ats == "greenhouse":
                aggregated.extend(discover_greenhouse_board(slug, company))
            elif ats == "ashby":
                aggregated.extend(discover_ashby_board(slug, company))
        except Exception:  # nosec B112 - best-effort per board; one broken ATS must not block others
            continue
    return aggregated


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
        with _open_url(req, timeout=10) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (ValueError, OSError, urllib.error.HTTPError, urllib.error.URLError):
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
        if not _ashby_auto_submit_url_ok(url):
            return "direct"
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


def _company_application_counts(rows: Iterable[Dict[str, str]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in rows:
        key = _safe_text(row.get("Company", "")).lower()
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
    return counts


def _discovery_priority(
    job: Dict[str, str],
    profile: RoleProfile,
    method: str,
    prior_counts: Dict[str, int] | None = None,
) -> tuple[int, int, int, str, str]:
    auto_penalty = 0 if method in AUTO_SUBMIT_METHODS else 1
    company_lc = _safe_text(job.get("company", "")).lower()
    prior = (prior_counts or {}).get(company_lc, 0)
    return (
        auto_penalty,
        -int(profile.score),
        prior,
        company_lc,
        _safe_text(job.get("title", "")).lower(),
    )


def _planned_cover_stem(company: str, role: str, today: str) -> str:
    return f"{today}_{_slug(company)}_{_slug(role)[:64]}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-new-jobs", type=int, default=10)
    ap.add_argument(
        "--max-manual-jobs",
        type=int,
        default=0,
        help=(
            "Optional quota for non-adapter/manual feed listings. "
            "Defaults to 0 so Ralph Loop prioritizes CI-submittable ATS roles."
        ),
    )
    ap.add_argument(
        "--max-per-company",
        type=int,
        default=2,
        help=(
            "Maximum new rows per company per run. Keeps discovery diverse "
            "instead of letting one big ATS board dominate the queue."
        ),
    )
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

    discovered = (
        list(discover_company_boards())
        + list(discover_remotive())
        + list(discover_remoteok())
    )
    prior_counts = _company_application_counts(rows)
    relevant: List[tuple[Dict[str, str], RoleProfile, str]] = []
    for job in discovered:
        if not job.get("url"):
            continue
        profile = classify_role(job)
        if profile.is_relevant:
            method = infer_method(job["url"])
            relevant.append((job, profile, method))
    relevant.sort(
        key=lambda item: _discovery_priority(item[0], item[1], item[2], prior_counts)
    )

    added = 0
    added_auto = 0
    added_manual = 0
    manual_quota = max(0, int(args.max_manual_jobs))
    per_company_cap = max(1, int(args.max_per_company))
    added_by_company: Dict[str, int] = {}
    for job, profile, method in relevant:
        if added >= args.max_new_jobs:
            break
        if method not in AUTO_SUBMIT_METHODS and added_manual >= manual_quota:
            continue
        company_lc = _safe_text(job["company"]).lower()
        if added_by_company.get(company_lc, 0) >= per_company_cap:
            continue
        url = _safe_text(job["url"]).lower()
        pair = (company_lc, _safe_text(job["title"]).lower())
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
        added_by_company[company_lc] = added_by_company.get(company_lc, 0) + 1
        added += 1
        if method in AUTO_SUBMIT_METHODS:
            added_auto += 1
        else:
            added_manual += 1

    print(f"Discovered: {len(discovered)}")
    print(f"Relevant: {len(relevant)}")
    print(f"Added: {added}")
    print(f"Added auto-submit candidates: {added_auto}")
    print(f"Added manual candidates: {added_manual}")
    if not args.dry_run and added:
        write_tracker(fieldnames, rows)
        print(f"Tracker updated: {TRACKER_CSV}")


if __name__ == "__main__":
    main()
