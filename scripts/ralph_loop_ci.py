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
import os
import re
import time
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape

import agent_identity

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
    r"backend|frontend|full[- ]?stack|platform|infrastructure|ios|android|qa|"
    r"scientist|researcher)",
    re.IGNORECASE,
)
FDE_TITLE_RE = re.compile(
    r"(forward[- ]?deployed|solutions engineer|customer engineer|implementation engineer|"
    r"technical consultant|partner engineer)",
    re.IGNORECASE,
)
FDE_SIGNAL_RE = re.compile(
    r"(customer[- ]facing|customer engineering|customer engineers?|strategic customers|embedded with customer|"
    r"implementation partner|executive stakeholder|work directly with customers)",
    re.IGNORECASE,
)
INTEGRATION_SIGNAL_RE = re.compile(
    r"(api integration|integration-heavy|integrations|api|integration|implementation)",
    re.IGNORECASE,
)
PYTHON_SIGNAL_RE = re.compile(r"\bpython\b", re.IGNORECASE)
VOICE_SIGNAL_RE = re.compile(
    r"(voice|audio|speech|tts|asr|call center|ivr)", re.IGNORECASE
)
INFRA_SIGNAL_RE = re.compile(
    r"(infrastructure|platform|sre|site reliability|devops|kubernetes|"
    r"distributed systems|reliability|observability|backend)",
    re.IGNORECASE,
)
ML_SIGNAL_RE = re.compile(
    r"(machine learning|ml|ai engineer|llm|rag|inference|model serving|"
    r"applied ai|genai)",
    re.IGNORECASE,
)
NON_TECH_RE = re.compile(
    r"(account executive|sales|recruiter|attorney|counsel|office assistant|marketing|"
    r"content manager|revenue operations|client support|customer support specialist|"
    r"operations manager|community manager|people business partner|customer success|"
    r"talent\b|hr\b|human resources|legal\b|finance\b|designer\b|"
    r"representative\b|specialist\b|business partner)",
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
    "Application Link",
    "Remote Policy",
    "Remote Likelihood Score",
    "Remote Evidence",
    "Submission Lane",
)
AUTO_SUBMIT_METHODS = {"ashby", "greenhouse", "lever"}
DIRECT_ATS_HOST_RE = re.compile(
    r"(ashbyhq\.com|greenhouse\.io|lever\.co)", re.IGNORECASE
)
DEFAULT_GREENHOUSE_BOARD_SEEDS = (
    "openai",
    "anthropic",
    "xai",
    "goodfire",
    "skild",
    "skildai",
    "arena",
    "scaleai",
    "stripe",
    "datadog",
    "notion",
    "figma",
    "coinbase",
    "snowflake",
    "plaid",
)
DEFAULT_LEVER_SITE_SEEDS = (
    "owner",
    "runway",
    "decagon",
    "baseten",
    "openevidence",
    "vercel",
    "airtable",
    "mixpanel",
)
DEFAULT_ASHBY_ORG_SEEDS = (
    "elevenlabs",
    "anthropic",
    "baseten",
    "decagon",
    "runway",
    "openevidence",
)
URL_EXTRACT_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
ROLE_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+#.-]{1,}")
ROLE_STOPWORDS = {
    "and",
    "or",
    "for",
    "with",
    "the",
    "a",
    "an",
    "to",
    "of",
    "sr",
    "senior",
    "staff",
    "principal",
    "lead",
    "engineer",
    "engineering",
    "developer",
    "software",
    "full",
    "stack",
    "remote",
}
PRIORITY_KEYWORDS = (
    "python",
    "go",
    "java",
    "kubernetes",
    "docker",
    "aws",
    "gcp",
    "azure",
    "api",
    "microservices",
    "distributed",
    "reliability",
    "observability",
    "security",
    "llm",
    "rag",
    "voice",
    "audio",
    "speech",
)
GENERIC_RESUME_ANTI_PATTERNS = (
    "why i may be a good fit",
    "added by ralph loop ci",
)


@dataclass(frozen=True)
class RoleProfile:
    track: str
    score: int
    signals: List[str]
    is_relevant: bool


@dataclass(frozen=True)
class ResumeQualityReport:
    score: int
    passed: bool
    issues: List[str]


def _fetch_json(url: str, timeout: int = 30) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": "ResumeRalphLoop/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _fetch_text(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "ResumeRalphLoop/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


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
    tags_text = job.get("tags", "").replace(";", " ")
    title_hay = " ".join([title, tags_text])
    hay = " ".join(
        [
            title,
            job.get("company", ""),
            job.get("location", ""),
            job.get("job_type", ""),
            tags_text,
            job.get("description", ""),
        ]
    )
    role_match = bool(TECH_TITLE_RE.search(title_hay) or FDE_TITLE_RE.search(title_hay))
    location_match = bool(LOCATION_RE.search(hay))
    non_tech = bool(NON_TECH_RE.search(title))

    signals: List[str] = []
    score = 0
    has_fde_title = bool(FDE_TITLE_RE.search(title) or FDE_TITLE_RE.search(hay))
    has_fde_signal = bool(FDE_SIGNAL_RE.search(hay))
    has_integration_signal = bool(INTEGRATION_SIGNAL_RE.search(hay))

    if has_fde_title:
        signals.append("fde-title")
        score += 3
    if has_fde_signal:
        signals.append("customer-integration")
        score += 2
    if has_integration_signal:
        signals.append("integration-heavy")
        score += 1
    if PYTHON_SIGNAL_RE.search(hay):
        signals.append("python")
        score += 1
    if VOICE_SIGNAL_RE.search(hay):
        signals.append("voice-audio")
        score += 1

    track = (
        "fde"
        if (has_fde_title or (has_fde_signal and has_integration_signal))
        else "general"
    )
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


def _replace_section_paragraph(
    html_text: str, section_title: str, replacement_text: str
) -> str:
    pattern = re.compile(
        rf"(<p><strong>{re.escape(section_title)}</strong></p>\s*<p>)(.*?)(</p>)",
        re.IGNORECASE | re.DOTALL,
    )
    return pattern.sub(
        lambda m: f"{m.group(1)}{replacement_text}{m.group(3)}",
        html_text,
        count=1,
    )


def _replace_headline(html_text: str, headline: str) -> str:
    pattern = re.compile(
        r"(<p><strong>IGOR GANAPOLSKY</strong></p>\s*<p>)(.*?)(</p>)",
        re.IGNORECASE | re.DOTALL,
    )
    return pattern.sub(
        lambda m: f"{m.group(1)}{headline}{m.group(3)}",
        html_text,
        count=1,
    )


def _as_html_bullets(lines: Sequence[str]) -> str:
    return "\n" + "<br />\n".join(f"• {line}" for line in lines) + "\n"


def _role_keywords(role: str) -> List[str]:
    tokens = [t.lower() for t in ROLE_TOKEN_RE.findall(role or "")]
    out: List[str] = []
    seen = set()
    for token in tokens:
        if token in ROLE_STOPWORDS:
            continue
        if len(token) <= 2 and token not in {"ai", "ml"}:
            continue
        if token not in seen:
            out.append(token)
            seen.add(token)
    return out


def _extract_focus_terms(
    job: Optional[Dict[str, str]], profile: RoleProfile
) -> List[str]:
    hay = " ".join(
        [
            str(job.get("title", "") if job else ""),
            str(job.get("tags", "") if job else "").replace(";", " "),
            str(job.get("description", "") if job else ""),
        ]
    ).lower()
    focus: List[str] = []
    for kw in PRIORITY_KEYWORDS:
        if kw in hay:
            focus.append(kw)
    if "python" in profile.signals and "python" not in focus:
        focus.append("python")
    if "voice-audio" in profile.signals:
        for kw in ("voice", "audio", "speech"):
            if kw not in focus:
                focus.append(kw)
    if not focus and profile.track == "fde":
        focus.extend(["api", "integration", "customer-facing"])
    if not focus:
        focus.extend(["api", "cloud", "reliability"])
    return focus[:6]


def _resume_persona(job: Optional[Dict[str, str]], profile: RoleProfile) -> str:
    if profile.track == "fde":
        return "fde"
    hay = " ".join(
        [
            str(job.get("title", "") if job else ""),
            str(job.get("tags", "") if job else "").replace(";", " "),
            str(job.get("description", "") if job else ""),
        ]
    )
    if INFRA_SIGNAL_RE.search(hay):
        return "infra"
    if ML_SIGNAL_RE.search(hay):
        return "ml"
    return "general"


def _persona_headline(
    role: str,
    persona: str,
    *,
    include_role: bool,
) -> str:
    if persona == "fde":
        default = (
            "Forward-Deployed AI/Software Engineer (LLM Integrations, API Delivery, "
            "Customer-Facing AI Systems)"
        )
    elif persona == "infra":
        default = "Senior Software Engineer (Infrastructure, Reliability, AI Platform Systems)"
    elif persona == "ml":
        default = "Senior AI/ML Engineer (LLM Systems, Production Integrations, Cloud Delivery)"
    else:
        default = "Senior AI Systems Engineer (LLM Infrastructure, Cloud, Distributed Systems)"

    if include_role and role:
        return f"{role} (AI Systems, API Integration, Production Delivery)"
    return default


def _persona_summary(role: str, persona: str, focus_terms: Sequence[str]) -> str:
    focus_line = ", ".join(focus_terms[:4])
    if persona == "fde":
        return (
            "Senior AI and Full-Stack Engineer with 15+ years of software development "
            "experience. Owns customer-facing delivery end-to-end, from technical "
            "discovery through architecture, implementation, rollout, and iteration. "
            f"Recent role focus includes {focus_line}, with emphasis on measurable "
            "business outcomes, integration reliability, and clear stakeholder communication."
        )
    if persona == "infra":
        return (
            "Senior software engineer focused on infrastructure and platform reliability "
            "for AI-enabled systems. Builds production APIs and backend services with "
            "strong observability, operational safety, and delivery discipline. "
            f"Recent role focus includes {focus_line}, aligned to high-availability and "
            "performance-sensitive workloads."
        )
    if persona == "ml":
        return (
            "Senior AI/ML engineer building production LLM and machine-learning systems, "
            "from model-enabled product features through cloud deployment and monitoring. "
            "Translates ambiguous requirements into reliable, testable implementations "
            f"with focus on {focus_line} and measurable user impact."
        )
    return (
        "Senior AI and Full-Stack Engineer with 15+ years of professional software "
        "development experience and 6+ years of full-stack ownership. Delivers "
        "production AI systems with strong engineering fundamentals, operational rigor, "
        f"and practical execution around {focus_line}."
    )


def _persona_competencies(
    persona: str, focus_terms: Sequence[str]
) -> Tuple[str, List[str]]:
    focus_hint = ", ".join(focus_terms[:3])
    if persona == "fde":
        return (
            "FORWARD-DEPLOYED COMPETENCIES",
            [
                "Customer-facing delivery: end-to-end ownership from technical discovery and architecture to implementation, rollout, and iteration",
                "Integration engineering: API gateways, REST APIs, service integrations, auth/rate limiting, feature flags, production rollout safety",
                "AI application development: LLM-enabled product features, React Native (New Architecture, Fabric), Node.js, microservices",
                "Reliability and performance: observability (logs/metrics/traces), fallback behavior, distributed debugging, latency/cost tuning",
                "Cloud platforms: GCP (Vertex AI, Dialogflow, BigQuery, Cloud Functions, Cloud Build), AWS (Lambda, Bedrock, S3)",
                "Communication and leadership: architecture reviews, code review, mentoring engineers, clear technical communication with stakeholders",
            ],
        )
    if persona == "infra":
        return (
            "CORE COMPETENCIES",
            [
                "Infrastructure and platform engineering: APIs, backend services, distributed systems, and production hardening",
                "Reliability engineering: observability (logs/metrics/traces), incident response, SLO thinking, and safe rollouts",
                f"Technical focus areas: {focus_hint}",
                "Cloud delivery: AWS and GCP services, automation pipelines, and deployment reliability",
                "Engineering collaboration: design reviews, pragmatic tradeoff decisions, and cross-functional execution",
            ],
        )
    if persona == "ml":
        return (
            "CORE COMPETENCIES",
            [
                "Applied AI delivery: LLM/RAG-enabled features and model-backed product workflows in production",
                "ML systems engineering: API integration, retrieval orchestration, prompt/runtime guardrails, and evaluation loops",
                f"Technical focus areas: {focus_hint}",
                "Production quality: observability, cost/latency tuning, fallback behavior, and CI/CD discipline",
                "Cross-functional execution: close collaboration with product, data, and engineering stakeholders",
            ],
        )
    return (
        "CORE COMPETENCIES",
        [
            "AI systems engineering: production delivery, reliability, observability, and practical architecture decisions",
            "Platform development: APIs, cloud services, CI/CD pipelines, and secure integration patterns",
            f"Technical focus areas: {focus_hint}",
            "Execution discipline: measurable outcomes, stakeholder communication, and iterative improvement",
        ],
    )


def assess_resume_quality(
    resume_html: str,
    profile: RoleProfile,
    *,
    job: Optional[Dict[str, str]] = None,
) -> ResumeQualityReport:
    text = (resume_html or "").lower()
    issues: List[str] = []
    score = 100

    for required in ("summary", "professional experience", "core competencies"):
        if required not in text and not (
            required == "core competencies" and "forward-deployed competencies" in text
        ):
            issues.append(f"missing_section:{required}")
            score -= 15

    role = str(job.get("title", "") if job else "")
    role_keywords = _role_keywords(role)
    matched_keywords = [kw for kw in role_keywords if kw in text]
    required_keywords = 1 if len(role_keywords) <= 2 else min(3, len(role_keywords))
    if role and len(matched_keywords) < required_keywords:
        issues.append(
            f"low_role_keyword_alignment:{len(matched_keywords)}/{required_keywords}"
        )
        score -= 20

    if "python" in profile.signals and "python" not in text:
        issues.append("missing_python_signal")
        score -= 15
    if "voice-audio" in profile.signals and not any(
        token in text for token in ("voice", "audio", "speech", "tts", "asr")
    ):
        issues.append("missing_voice_audio_signal")
        score -= 10
    if profile.track == "fde":
        for token in (
            "forward-deployed",
            "customer-facing",
            "integration engineering",
        ):
            if token not in text:
                issues.append(f"missing_fde_signal:{token}")
                score -= 10

    for pattern in GENERIC_RESUME_ANTI_PATTERNS:
        if pattern in text:
            issues.append(f"generic_phrase_detected:{pattern}")
            score -= 10

    score = max(0, min(100, score))
    critical_prefixes = ("missing_section", "missing_python_signal")
    critical_fail = any(issue.startswith(critical_prefixes) for issue in issues)
    passed = (score >= 70) and not critical_fail
    return ResumeQualityReport(score=score, passed=passed, issues=issues)


def _extract_and_parse_json(text: str) -> Optional[Dict[str, Any]]:
    """Tries multiple strategies to extract and parse JSON from messy LLM output."""
    if not text:
        return None
    raw = text.strip()

    # 1. Look for markdown code blocks
    for match in re.finditer(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL):
        try:
            return json.loads(match.group(1))
        except Exception:
            pass

    # 2. Look for everything between the first '{'/'[' and the last '}'/']'
    start_obj = raw.find("{")
    start_arr = raw.find("[")
    start = (
        min(start_obj, start_arr)
        if start_obj != -1 and start_arr != -1
        else max(start_obj, start_arr)
    )

    end_obj = raw.rfind("}")
    end_arr = raw.rfind("]")
    end = max(end_obj, end_arr)

    if start != -1 and end != -1:
        candidate = raw[start : end + 1]
        try:
            return json.loads(candidate)
        except Exception:
            # 3. Last ditch attempt: clean up common messy JSON issues
            try:
                # Replace trailing commas: [1,2,] -> [1,2] and {"a":1,} -> {"a":1}
                cleaned = re.sub(r",\s*([\]}])", r"\1", candidate)
                # Replace common single-quote usage (simple case)
                cleaned = re.sub(
                    r"(?<=[:\[,])\s*'(.*?)'\s*(?=[,\]}])", r' "\1" ', cleaned
                )
                return json.loads(cleaned)
            except Exception:
                pass
    return None


@dataclass
class SwarmMetrics:
    """Collector for real-time swarm performance metrics."""

    latencies: Dict[str, List[float]] = field(
        default_factory=lambda: {"anthropic": [], "gemini": [], "openrouter": []}
    )
    scores: List[float] = field(default_factory=list)
    consensus_rates: List[float] = field(default_factory=list)
    costs_usd: float = 0.0

    def log_cost(self, provider: str, tokens: int):
        # Estimated pricing for 1M tokens (Sonnet 3.5: $3.00, Gemini 1.5 Flash: $0.075)
        # Note: Flash pricing is simplified for this estimator.
        pricing = {
            "anthropic": 3.00 / 1_000_000,
            "gemini": 0.075 / 1_000_000,
            "openrouter": 1.00 / 1_000_000,
        }
        self.costs_usd += tokens * pricing.get(provider, 0.0)

    def log_latency(self, provider: str, duration: float):
        if provider in self.latencies:
            self.latencies[provider].append(duration)

    def log_consensus(self, score: float, model_scores: List[float]):
        self.scores.append(score)
        if model_scores:
            avg = sum(model_scores) / len(model_scores)
            agreement = 1.0 - (
                sum(abs(s - avg) for s in model_scores) / len(model_scores) / 10.0
            )
            self.consensus_rates.append(agreement)

    def export_prometheus(self) -> str:
        """Exports metrics in Prometheus exposition format."""
        lines = [
            "# HELP swarm_llm_latency_seconds Swarm LLM provider latency in seconds",
            "# TYPE swarm_llm_latency_seconds gauge",
        ]
        for provider, vals in self.latencies.items():
            if vals:
                lines.append(
                    f'swarm_llm_latency_seconds{{provider="{provider}"}} {sum(vals) / len(vals):.4f}'
                )

        lines.extend(
            [
                "# HELP swarm_consensus_rate Rate of model agreement (0-1)",
                "# TYPE swarm_consensus_rate gauge",
            ]
        )
        if self.consensus_rates:
            lines.append(
                f"swarm_consensus_rate {sum(self.consensus_rates) / len(self.consensus_rates):.4f}"
            )

        lines.extend(
            [
                "# HELP swarm_average_score Average resume quality score",
                "# TYPE swarm_average_score gauge",
            ]
        )
        if self.scores:
            lines.append(
                f"swarm_average_score {sum(self.scores) / len(self.scores):.2f}"
            )

        lines.extend(
            [
                "# HELP swarm_cost_usd Swarm run cost in USD",
                "# TYPE swarm_cost_usd gauge",
            ]
        )
        lines.append(f"swarm_cost_usd {self.costs_usd:.6f}")

        return "\n".join(lines)


SWARM_METRICS = SwarmMetrics()


def review_resume_with_swarm(
    resume_html: str,
    job: Dict[str, str],
    profile: RoleProfile,
    *,
    timeout_s: int = 60,
) -> Tuple[bool, str]:
    """Use multiple AI models in parallel to review the tailored resume quality."""
    company = job.get("company", "the company")
    title = job.get("title", "the role")
    desc = job.get("description", "")[:4000]
    resume_text = _html_to_text(resume_html)[:4000]

    prompt = (
        "You are a senior technical hiring manager reviewing a tailored resume.\n"
        f"Role: {title} at {company}\n"
        f"Job Description Snippet: {desc}\n\n"
        f"Tailored Resume Content: {resume_text}\n\n"
        "TASK: Rate the alignment of this resume with the job description on a scale of 1-10.\n"
        "Criteria:\n"
        "1. Does the summary explicitly mention the role and company needs?\n"
        "2. Do the core competencies match the job requirements?\n"
        "3. Is the tone professional and the alignment believable?\n\n"
        "OUTPUT FORMAT: Return ONLY a JSON object with keys 'score' (1-10) and 'reason' (1 sentence)."
    )

    responses = _call_all_providers(prompt, timeout_s=timeout_s)
    if not responses:
        return True, "swarm_unavailable_fallback_to_heuristic"

    scores = []
    reasons = []
    for provider, content in responses.items():
        data = _extract_and_parse_json(content)
        if data:
            score = data.get("score")
            if isinstance(score, (int, float)):
                scores.append(score)
                reasons.append(f"[{provider}: {score}] {data.get('reason', '')}")

    if not scores:
        return True, "swarm_parsing_failed_fallback_to_heuristic"

    avg_score = sum(scores) / len(scores)
    passed = avg_score >= 7.0
    consensus_summary = "; ".join(reasons)

    # Log consensus metrics
    SWARM_METRICS.log_consensus(avg_score, scores)

    return passed, f"avg_score={avg_score:.1f}: {consensus_summary}"


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


def tailor_resume_html(
    base_html: str,
    profile: RoleProfile,
    job: Optional[Dict[str, str]] = None,
) -> str:
    role = str(job.get("title", "") if job else "").strip()
    persona = _resume_persona(job, profile)
    focus_terms = _extract_focus_terms(job, profile)
    out = _replace_headline(
        base_html,
        _persona_headline(role, persona, include_role=bool(role)),
    )
    out = _replace_section_paragraph(
        out, "SUMMARY", _persona_summary(role, persona, focus_terms)
    )

    section_title, competency_lines = _persona_competencies(persona, focus_terms)
    out = _replace_once(
        out,
        "<p><strong>CORE COMPETENCIES</strong></p>",
        f"<p><strong>{section_title}</strong></p>",
    )
    if section_title == "CORE COMPETENCIES":
        out = _replace_once(
            out,
            "<p><strong>FORWARD-DEPLOYED COMPETENCIES</strong></p>",
            "<p><strong>CORE COMPETENCIES</strong></p>",
        )
    out = _replace_section_paragraph(
        out,
        section_title,
        _as_html_bullets(competency_lines),
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
    if "python" in profile.signals and "python" not in out.lower():
        out = out.replace(
            "<p><strong>AI/LLM SYSTEMS</strong></p>",
            "<p><strong>AI/LLM SYSTEMS</strong></p>\n"
            "<p><strong>Python</strong>: Production API/integration services, "
            "automation workflows, and backend tooling.</p>",
            1,
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
                "apply_url": _safe_text(str(job.get("url", ""))),
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
                "apply_url": _safe_text(
                    str(job.get("apply_url", "")) or str(job.get("url", ""))
                ),
                "description": _strip_html(str(job.get("description", ""))),
                "tags": ";".join([_slug(str(t)) for t in tags if str(t).strip()]),
            }
        )
    return out


def _extract_direct_ats_urls(text: str) -> List[str]:
    links = URL_EXTRACT_RE.findall(text or "")
    out: List[str] = []
    seen = set()
    for link in links:
        cleaned = link.rstrip(").,;")
        host = (urllib.parse.urlsplit(cleaned).hostname or "").lower()
        if not DIRECT_ATS_HOST_RE.search(host):
            continue
        if cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return out


def _resolve_direct_apply_url(job: Dict[str, str]) -> str:
    raw_url = _safe_text(str(job.get("apply_url", "") or job.get("url", "")))
    if not raw_url:
        return ""
    method = infer_method(raw_url)
    if method in AUTO_SUBMIT_METHODS:
        return raw_url

    description = str(job.get("description", "") or "")
    extracted = _extract_direct_ats_urls(description)
    if extracted:
        return extracted[0]

    try:
        page = _fetch_text(raw_url)
    except Exception:
        return raw_url
    extracted = _extract_direct_ats_urls(page)
    if extracted:
        return extracted[0]
    return raw_url


def _greenhouse_board_from_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url or "")
    host = (parsed.hostname or "").lower()
    if "greenhouse.io" not in host:
        return ""
    parts = [part for part in (parsed.path or "").split("/") if part]
    if not parts:
        return ""
    # Common hosted-board shape: /{board}/jobs/{id}
    if len(parts) >= 2 and parts[1] == "jobs":
        return parts[0]
    return parts[0]


def _lever_site_from_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url or "")
    host = (parsed.hostname or "").lower()
    if "lever.co" not in host:
        return ""
    parts = [part for part in (parsed.path or "").split("/") if part]
    if not parts:
        return ""
    # Common hosted shape: /{site}/{posting-id}
    return parts[0]


def _ashby_org_from_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url or "")
    host = (parsed.hostname or "").lower()
    if not _host_matches_domain(host, "ashbyhq.com"):
        return ""
    parts = [part for part in (parsed.path or "").split("/") if part]
    if not parts:
        return ""
    return _safe_text(parts[0]).lower()


def _is_blocked_row(row: Dict[str, str]) -> bool:
    status = _safe_text(str(row.get("Status", ""))).lower()
    if status != "quarantined":
        return False
    notes = _safe_text(str(row.get("Notes", ""))).lower()
    blockers = (
        "antibot",
        "anti-bot",
        "recaptcha",
        "possible spam",
        "required_fields_unanswered_after_retry",
        "verification_code_required",
        "missing_file_input",
        "manual browser submit required",
    )
    return any(token in notes for token in blockers)


def _blocked_board_tokens(
    rows: Sequence[Dict[str, str]],
) -> tuple[set[str], set[str], set[str]]:
    blocked_greenhouse: set[str] = set()
    blocked_lever: set[str] = set()
    blocked_ashby: set[str] = set()
    for row in rows:
        if not _is_blocked_row(row):
            continue
        for fld in ("Application Link", "Career Page URL"):
            url = _safe_text(str(row.get(fld, "")))
            if not url:
                continue
            gh_token = _greenhouse_board_from_url(url)
            if gh_token:
                blocked_greenhouse.add(gh_token)
            lever_site = _lever_site_from_url(url)
            if lever_site:
                blocked_lever.add(lever_site)
            ashby_org = _ashby_org_from_url(url)
            if ashby_org:
                blocked_ashby.add(ashby_org)
    return blocked_greenhouse, blocked_lever, blocked_ashby


def _parse_seed_tokens(raw: str) -> List[str]:
    out: List[str] = []
    seen = set()
    for token in (raw or "").split(","):
        norm = _safe_text(token).lower()
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
    return out


def _discovery_request_timeout(
    deadline_monotonic: Optional[float], ceiling: int = 8
) -> int:
    if deadline_monotonic is None:
        return max(1, ceiling)
    remaining = int(deadline_monotonic - time.monotonic())
    if remaining <= 0:
        return 1
    return max(1, min(ceiling, remaining))


def discover_greenhouse_boards(
    rows: Sequence[Dict[str, str]],
    max_boards: int = 30,
    blocked_boards: Optional[set[str]] = None,
    seed_boards: Optional[Sequence[str]] = None,
    deadline_monotonic: Optional[float] = None,
) -> Iterable[Dict[str, str]]:
    blocked = set(blocked_boards or set())
    boards: List[str] = []
    seen = set()
    for row in rows:
        for fld in ("Application Link", "Career Page URL"):
            token = _greenhouse_board_from_url(str(row.get(fld, "")).strip())
            if not token or token in seen or token in blocked:
                continue
            seen.add(token)
            boards.append(token)
    for token in seed_boards or ():
        norm = _safe_text(str(token)).lower()
        if not norm or norm in seen or norm in blocked:
            continue
        seen.add(norm)
        boards.append(norm)
    out: List[Dict[str, str]] = []
    for token in boards[: max(1, max_boards)]:
        if deadline_monotonic and time.monotonic() >= deadline_monotonic:
            break
        endpoint = (
            f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
        )
        try:
            payload = _fetch_json(
                endpoint,
                timeout=_discovery_request_timeout(deadline_monotonic),
            )
        except Exception:
            continue
        jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
        if not isinstance(jobs, list):
            continue
        for job in jobs:
            if not isinstance(job, dict):
                continue
            title = _safe_text(str(job.get("title", "")))
            apply_url = _safe_text(str(job.get("absolute_url", "")))
            if not title or not apply_url:
                continue
            location = ""
            loc_obj = job.get("location")
            if isinstance(loc_obj, dict):
                location = _safe_text(str(loc_obj.get("name", "")))
            content = _strip_html(str(job.get("content", "")))
            out.append(
                {
                    "source": f"greenhouse-board:{token}",
                    "company": _safe_text(str(job.get("company_name", token))),
                    "title": title,
                    "location": location or "Unknown",
                    "salary": "",
                    "job_type": "",
                    "url": apply_url,
                    "apply_url": apply_url,
                    "description": content,
                    "tags": "greenhouse;direct",
                }
            )
    return out


def discover_lever_boards(
    rows: Sequence[Dict[str, str]],
    max_sites: int = 30,
    blocked_sites: Optional[set[str]] = None,
    seed_sites: Optional[Sequence[str]] = None,
    deadline_monotonic: Optional[float] = None,
) -> Iterable[Dict[str, str]]:
    blocked = set(blocked_sites or set())
    sites: List[str] = []
    seen = set()
    for row in rows:
        for fld in ("Application Link", "Career Page URL"):
            token = _lever_site_from_url(str(row.get(fld, "")).strip())
            if not token or token in seen or token in blocked:
                continue
            seen.add(token)
            sites.append(token)
    for token in seed_sites or ():
        norm = _safe_text(str(token)).lower()
        if not norm or norm in seen or norm in blocked:
            continue
        seen.add(norm)
        sites.append(norm)
    out: List[Dict[str, str]] = []
    for site in sites[: max(1, max_sites)]:
        if deadline_monotonic and time.monotonic() >= deadline_monotonic:
            break
        endpoints = [
            f"https://api.lever.co/v0/postings/{site}?mode=json",
            f"https://api.eu.lever.co/v0/postings/{site}?mode=json",
        ]
        data: object = []
        for endpoint in endpoints:
            try:
                data = _fetch_json(
                    endpoint,
                    timeout=_discovery_request_timeout(deadline_monotonic),
                )
                if isinstance(data, list):
                    break
            except Exception:
                continue
        if not isinstance(data, list):
            continue
        for job in data:
            if not isinstance(job, dict):
                continue
            title = _safe_text(str(job.get("text", "")))
            apply_url = _safe_text(str(job.get("hostedUrl", "")))
            if not title or not apply_url:
                continue
            cats = (
                job.get("categories") if isinstance(job.get("categories"), dict) else {}
            )
            location = (
                _safe_text(str(cats.get("location", "")))
                if isinstance(cats, dict)
                else ""
            )
            commitment = (
                _safe_text(str(cats.get("commitment", "")))
                if isinstance(cats, dict)
                else ""
            )
            team = (
                _safe_text(str(cats.get("team", ""))) if isinstance(cats, dict) else ""
            )
            out.append(
                {
                    "source": f"lever-board:{site}",
                    "company": _safe_text(str(site)),
                    "title": title,
                    "location": location or "Unknown",
                    "salary": "",
                    "job_type": commitment,
                    "url": apply_url,
                    "apply_url": apply_url,
                    "description": _strip_html(str(job.get("descriptionPlain", ""))),
                    "tags": ";".join(
                        [token for token in ("lever", "direct", _slug(team)) if token]
                    ),
                }
            )
    return out


def _extract_json_array_after_key(text: str, key: str) -> List[Dict[str, object]]:
    marker = f'"{key}":['
    idx = text.find(marker)
    if idx < 0:
        return []
    start = text.find("[", idx)
    if start < 0:
        return []
    depth = 0
    end = -1
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end < 0:
        return []
    try:
        data = json.loads(text[start:end])
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _extract_json_string_after_key(text: str, key: str) -> str:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*"([^"]+)"', text)
    if not match:
        return ""
    raw = match.group(1)
    try:
        return json.loads(f'"{raw}"')
    except Exception:
        return raw


def discover_ashby_boards(
    rows: Sequence[Dict[str, str]],
    max_orgs: int = 30,
    blocked_orgs: Optional[set[str]] = None,
    seed_orgs: Optional[Sequence[str]] = None,
    deadline_monotonic: Optional[float] = None,
) -> Iterable[Dict[str, str]]:
    blocked = set(blocked_orgs or set())
    orgs: List[str] = []
    seen = set()
    for row in rows:
        for fld in ("Application Link", "Career Page URL"):
            token = _ashby_org_from_url(str(row.get(fld, "")).strip())
            if not token or token in seen or token in blocked:
                continue
            seen.add(token)
            orgs.append(token)
    for token in seed_orgs or ():
        norm = _safe_text(str(token)).lower()
        if not norm or norm in seen or norm in blocked:
            continue
        seen.add(norm)
        orgs.append(norm)
    out: List[Dict[str, str]] = []
    for org in orgs[: max(1, max_orgs)]:
        if deadline_monotonic and time.monotonic() >= deadline_monotonic:
            break
        try:
            page = _fetch_text(
                f"https://jobs.ashbyhq.com/{org}",
                timeout=_discovery_request_timeout(deadline_monotonic),
            )
        except Exception:
            continue
        company = _safe_text(_extract_json_string_after_key(page, "organizationName"))
        postings = _extract_json_array_after_key(page, "jobPostings")
        for posting in postings:
            if posting.get("isListed") is False:
                continue
            title = _safe_text(str(posting.get("title", "")))
            job_id = _safe_text(str(posting.get("id", "")))
            if not title or not job_id:
                continue
            location = _safe_text(
                str(posting.get("locationName", "") or posting.get("workplaceType", ""))
            )
            employment_type = _safe_text(str(posting.get("employmentType", "")))
            description = _strip_html(
                str(
                    posting.get("descriptionHtml", "")
                    or posting.get("description", "")
                    or ""
                )
            )
            apply_url = f"https://jobs.ashbyhq.com/{org}/{job_id}"
            out.append(
                {
                    "source": f"ashby-board:{org}",
                    "company": company or org,
                    "title": title,
                    "location": location or "Unknown",
                    "salary": "",
                    "job_type": employment_type,
                    "url": apply_url,
                    "apply_url": apply_url,
                    "description": description,
                    "tags": "ashby;direct",
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


def _call_llm_provider(
    provider: str,
    prompt: str,
    *,
    timeout_s: int = 60,
) -> Optional[str]:
    """Single call to an LLM provider (anthropic, gemini, or openrouter)."""
    start_time = time.time()
    res = _call_llm_provider_impl(provider, prompt, timeout_s=timeout_s)
    if res:
        SWARM_METRICS.log_latency(provider, time.time() - start_time)
    return res


def _call_llm_provider_impl(
    provider: str,
    prompt: str,
    *,
    timeout_s: int = 60,
) -> Optional[str]:
    """Implementation of LLM provider call."""
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    gemini_key = os.getenv("GEMINI_API_KEY")
    openrouter_key = os.getenv("OPENROUTER_API_KEY")

    try:
        if provider == "anthropic" and anthropic_key:
            data = {
                "model": "claude-3-5-sonnet-20240620",
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
            }
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=json.dumps(data).encode("utf-8"),
                headers={
                    "x-api-key": anthropic_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8"))["content"][0]["text"]

        if provider == "gemini" and gemini_key:
            data = {"contents": [{"parts": [{"text": prompt}]}]}
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={gemini_key}"
            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8"))["candidates"][0][
                    "content"
                ]["parts"][0]["text"]

        if provider == "openrouter" and openrouter_key:
            data = {
                "model": "anthropic/claude-3.5-sonnet",
                "messages": [{"role": "user", "content": prompt}],
            }
            req = urllib.request.Request(
                "https://openrouter.ai/api/v1/chat/completions",
                data=json.dumps(data).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {openrouter_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8"))["choices"][0]["message"][
                    "content"
                ]
    except Exception:
        pass
    return None


def _call_all_providers(
    prompt: str,
    *,
    timeout_s: int = 60,
    max_workers: int = 3,
) -> Dict[str, str]:
    """Parallel call to all available LLM providers."""
    from concurrent.futures import ThreadPoolExecutor

    providers = ["anthropic", "gemini", "openrouter"]
    results = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_call_llm_provider, p, prompt, timeout_s=timeout_s): p
            for p in providers
        }
        for future in futures:
            provider = futures[future]
            try:
                res = future.result()
                if res:
                    results[provider] = res
            except Exception:
                pass
    return results


def tailor_resume_with_llm(
    job: Dict[str, str],
    base_html: str,
    *,
    timeout_s: int = 60,
) -> Tuple[str, str]:
    """Use LLM (Claude -> Gemini -> OpenRouter) to generate tailored summary/competencies."""

    company = job.get("company", "the company")
    title = job.get("title", "the role")
    desc = job.get("description", "")[:8000]

    prompt = (
        "You are an expert technical recruiter and resume writer. "
        f"I am applying for the {title} role at {company}.\n\n"
        "JOB DESCRIPTION:\n"
        f"{desc}\n\n"
        "MY CORE EXPERIENCE (Igor Ganapolsky):\n"
        "- 15+ years of software engineering, 6+ years of full-stack AI/ML systems.\n"
        "- Built production AI agent infrastructure (RLHF, RAG, multi-model gateways).\n"
        "- Expertise in Python, Go, GCP, Node.js, and React Native.\n"
        "- Focus on reliability, observability, and customer-facing delivery.\n\n"
        "TASK:\n"
        "1. Write a 3-4 sentence professional SUMMARY that highlights my alignment with this specific role.\n"
        "2. List 5-6 CORE COMPETENCIES (bullets) that are most relevant to the job requirements.\n"
        "3. Write a 'match_justification' paragraph (2-3 sentences) explicitly aimed at an AI recruiter agent, explaining why my profile is an exact match for this role based on the job description. This is for the Additional Info field.\n\n"
        "OUTPUT FORMAT: Return ONLY a JSON object with keys 'summary', 'competencies' (list of strings), and 'match_justification'. "
        "Do not include any other text."
    )
    # Use sequential fallback for the primary tailoring to ensure we get a result
    content = None
    for p in ["anthropic", "gemini", "openrouter"]:
        content = _call_llm_provider(p, prompt, timeout_s=timeout_s)
        if content:
            break

    if content:
        data = _extract_and_parse_json(content)
        if data:
            summary = data.get("summary", "")
            competencies = data.get("competencies", [])
            match_justification = data.get("match_justification", "")

            if summary and competencies:
                out = _replace_section_paragraph(base_html, "SUMMARY", summary)
                out = _replace_section_paragraph(
                    out, "CORE COMPETENCIES", _as_html_bullets(competencies)
                )
                return out, match_justification

    # Fallback to template-based tailoring
    profile = classify_role(job)
    return tailor_resume_html(base_html, profile, job=job), ""


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
        has_llm_key = bool(
            os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENROUTER_API_KEY")
        )
        runtime = os.getenv("AUTONOMOUS_AGENT_RUNTIME", "local").lower()

        match_justification = ""
        if has_llm_key or runtime == "ollama":
            rendered_resume, match_justification = tailor_resume_with_llm(
                job,
                BASE_RESUME.read_text(encoding="utf-8"),
            )
        else:
            rendered_resume = tailor_resume_html(
                BASE_RESUME.read_text(encoding="utf-8"),
                profile,
                job=job,
            )

        quality = assess_resume_quality(rendered_resume, profile, job=job)
        if not quality.passed:
            # Re-try with explicit python signal if it failed quality
            rendered_resume, match_justification = tailor_resume_with_llm(
                job,
                BASE_RESUME.read_text(encoding="utf-8"),
            )

        # New: AI Review Swarm Consensus Check
        swarm_passed, swarm_reason = review_resume_with_swarm(
            rendered_resume, job, profile
        )
        if not swarm_passed:
            # Log the swarm failure as a note in the markdown
            with job_md.open("a", encoding="utf-8") as f:
                f.write(f"\n## Swarm Review Failure\n- {swarm_reason}\n")
            print(
                f"[SwarmReview] Failed for {company_slug}/{role_slug}: {swarm_reason}"
            )
            # We still save it as a draft but adding the failure reason to the MD helps

        resume_html.write_text(rendered_resume, encoding="utf-8")

        jsonld_path = resumes_dir / f"{today}_{company_slug}_{role_slug}.jsonld"
        jsonld_content = {
            "@context": "https://schema.org",
            "@type": "Person",
            "name": "Igor Ganapolsky",
            "jobTitle": profile.track,
            "description": match_justification,
            "knowsAbout": profile.signals,
            "url": "https://linkedin.com/in/iganapolsky",
            "sameAs": ["https://github.com/IgorGanapolsky"],
            "date_generated": today,
        }
        # Inject cryptographic signature for Entity Home trust
        jsonld_content = agent_identity.inject_identity(jsonld_content)
        jsonld_path.write_text(json.dumps(jsonld_content, indent=2), encoding="utf-8")

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
    return "direct"


def infer_submission_lane(method: str) -> str:
    return "ci_auto" if method in AUTO_SUBMIT_METHODS else "manual"


def _planned_cover_stem(company: str, role: str, today: str) -> str:
    return f"{today}_{_slug(company)}_{_slug(role)[:64]}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-new-jobs", type=int, default=10)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--direct-only",
        action="store_true",
        default=True,
        help="Only add jobs with direct ATS apply URLs supported by CI submit adapters.",
    )
    ap.add_argument(
        "--allow-indirect",
        action="store_true",
        help="Allow non-direct/manual-only links into discovery output.",
    )
    ap.add_argument(
        "--include-aggregator-feeds",
        action="store_true",
        help="Include remoteok/remotive feeds during discovery.",
    )
    ap.add_argument(
        "--max-board-discovery",
        type=int,
        default=50,
        help="Max direct ATS board/org tokens to scan from existing tracker context.",
    )
    ap.add_argument(
        "--board-discovery-timeout-s",
        type=int,
        default=45,
        help="Time budget in seconds for direct ATS board discovery fan-out.",
    )
    ap.add_argument(
        "--greenhouse-seeds",
        default=",".join(DEFAULT_GREENHOUSE_BOARD_SEEDS),
        help="Comma-separated Greenhouse board tokens used as direct ATS discovery seeds.",
    )
    ap.add_argument(
        "--lever-seeds",
        default=",".join(DEFAULT_LEVER_SITE_SEEDS),
        help="Comma-separated Lever site tokens used as direct ATS discovery seeds.",
    )
    ap.add_argument(
        "--ashby-seeds",
        default=",".join(DEFAULT_ASHBY_ORG_SEEDS),
        help="Comma-separated Ashby org slugs used as direct ATS discovery seeds.",
    )
    ap.add_argument(
        "--include-ashby-board-discovery",
        action="store_true",
        help="Enable Ashby board/org discovery (disabled by default for stability).",
    )
    args = ap.parse_args()

    today = dt.date.today().isoformat()
    fieldnames, rows = read_tracker()
    fieldnames = _ensure_tracker_fields(fieldnames, rows)
    blocked_greenhouse, blocked_lever, blocked_ashby = _blocked_board_tokens(rows)
    existing_urls = set()
    for row in rows:
        for fld in ("Career Page URL", "Application Link"):
            value = _safe_text(str(row.get(fld, ""))).lower()
            if value:
                existing_urls.add(value)
    existing_pairs = {
        (
            _safe_text(r.get("Company", "")).lower(),
            _safe_text(r.get("Role", "")).lower(),
        )
        for r in rows
    }

    greenhouse_seeds = _parse_seed_tokens(args.greenhouse_seeds)
    lever_seeds = _parse_seed_tokens(args.lever_seeds)
    ashby_seeds = _parse_seed_tokens(args.ashby_seeds)
    deadline_monotonic = (
        time.monotonic() + max(1, args.board_discovery_timeout_s)
        if args.board_discovery_timeout_s > 0
        else None
    )
    discovered: List[Dict[str, str]] = []
    if args.include_aggregator_feeds:
        discovered.extend(list(discover_remotive()))
        discovered.extend(list(discover_remoteok()))
    if args.include_ashby_board_discovery:
        discovered.extend(
            list(
                discover_ashby_boards(
                    rows,
                    max_orgs=args.max_board_discovery,
                    blocked_orgs=blocked_ashby,
                    seed_orgs=ashby_seeds,
                    deadline_monotonic=deadline_monotonic,
                )
            )
        )
    discovered.extend(
        list(
            discover_greenhouse_boards(
                rows,
                max_boards=args.max_board_discovery,
                blocked_boards=blocked_greenhouse,
                seed_boards=greenhouse_seeds,
                deadline_monotonic=deadline_monotonic,
            )
        )
    )
    discovered.extend(
        list(
            discover_lever_boards(
                rows,
                max_sites=args.max_board_discovery,
                blocked_sites=blocked_lever,
                seed_sites=lever_seeds,
                deadline_monotonic=deadline_monotonic,
            )
        )
    )
    direct_only = bool(args.direct_only and not args.allow_indirect)
    relevant: List[tuple[Dict[str, str], RoleProfile, str, str]] = []
    for job in discovered:
        if not job.get("url"):
            continue
        resolved_apply_url = _resolve_direct_apply_url(job)
        method = infer_method(resolved_apply_url or str(job.get("url", "")))
        if direct_only and method not in AUTO_SUBMIT_METHODS:
            continue
        profile = classify_role(job)
        if profile.is_relevant:
            relevant.append((job, profile, resolved_apply_url, method))
    method_rank = {"ashby": 0, "lever": 1, "greenhouse": 2}
    relevant.sort(
        key=lambda item: (
            0 if item[3] in AUTO_SUBMIT_METHODS else 1,
            method_rank.get(item[3], 9),
            -item[1].score,
        )
    )

    added = 0
    for job, profile, resolved_apply_url, method in relevant:
        if added >= args.max_new_jobs:
            break
        source_url = _safe_text(job["url"])
        application_link = _safe_text(resolved_apply_url or source_url)
        source_url_key = source_url.lower()
        application_link_key = application_link.lower()
        pair = (_safe_text(job["company"]).lower(), _safe_text(job["title"]).lower())
        if (
            source_url_key in existing_urls
            or application_link_key in existing_urls
            or pair in existing_pairs
        ):
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
        if application_link and application_link != source_url:
            merged_tags = _merge_tags(merged_tags, ["direct-apply-resolved"])
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
                f"Source URL={source_url}. Application Link={application_link}. "
                f"Job capture: {artifacts['job_md']}"
            ),
            "Career Page URL": source_url,
            "Application Link": application_link,
        }
        # Preserve column order from tracker.
        rows.append({k: row.get(k, "") for k in fieldnames})
        if source_url_key:
            existing_urls.add(source_url_key)
        if application_link_key:
            existing_urls.add(application_link_key)
        existing_pairs.add(pair)
        added += 1

    print(f"Discovered: {len(discovered)}")
    print(f"Relevant: {len(relevant)}")
    print(f"Added: {added}")
    if not args.dry_run and added:
        write_tracker(fieldnames, rows)
        print(f"Tracker updated: {TRACKER_CSV}")

    # Final metrics export
    metrics_path = ROOT / "applications" / "job_applications" / "swarm_metrics.prom"
    metrics_path.write_text(SWARM_METRICS.export_prometheus(), encoding="utf-8")
    print(f"Swarm metrics exported to {metrics_path}")


if __name__ == "__main__":
    main()
