import hashlib
import re
from typing import Dict, List


_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

# ATS patterns ordered by specificity (most specific first)
_ATS_PATTERNS: List[tuple] = [
    ("mercor", re.compile(r"work\.mercor\.com")),
    ("ashby", re.compile(r"ashbyhq\.com")),
    ("greenhouse", re.compile(r"greenhouse\.io|job-boards\.greenhouse\.io")),
    ("lever", re.compile(r"jobs\.lever\.co")),
    ("wellfound", re.compile(r"wellfound\.com|angel\.co")),
    ("workday", re.compile(r"myworkdayjobs\.com|workday\.com")),
    ("linkedin", re.compile(r"linkedin\.com/jobs")),
]


def slug(s: str) -> str:
    s = (s or "").strip().lower()
    s = _NON_ALNUM_RE.sub("-", s).strip("-")
    return s or "unknown"


def stable_id(company: str, role: str, url: str) -> str:
    base = f"{slug(company)}__{slug(role)}__{url.strip()}"
    h = hashlib.sha256(base.encode("utf-8")).hexdigest()[:10]
    return f"{slug(company)}__{slug(role)}__{h}"


def parse_tags(tag_str: str) -> List[str]:
    if not tag_str:
        return []
    parts = [p.strip() for p in tag_str.split(";")]
    return [p for p in parts if p]


def normalize_status(status: str) -> str:
    s = (status or "").strip().lower()
    mapping = {
        "applied": "Applied",
        "draft": "Draft",
        "in progress": "Draft",
        "closed": "Closed",
        "blocked": "Blocked",
        "rejected": "Rejected",
        "offer": "Offer",
    }
    return mapping.get(s, status.strip() or "Draft")


def infer_application_method(url: str) -> str:
    """Infer ATS/application method from a job URL. Returns a stable lowercase key."""
    url = (url or "").lower()
    for name, pattern in _ATS_PATTERNS:
        if pattern.search(url):
            return name
    return "direct"


def normalize_row(row: Dict[str, str]) -> Dict[str, object]:
    company = row.get("Company", "").strip()
    role = row.get("Role", "").strip()
    url = (row.get("Career Page URL", "") or "").strip()

    out: Dict[str, object] = dict(row)
    out["Status"] = normalize_status(row.get("Status", ""))
    out["Tags"] = parse_tags(row.get("Tags", ""))
    out["app_id"] = stable_id(company, role, url)
    out["application_method"] = infer_application_method(url)
    return out
