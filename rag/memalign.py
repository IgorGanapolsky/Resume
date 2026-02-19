import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


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


def _parse_iso_utc(ts: str) -> Optional[datetime]:
    raw = (ts or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=True) + "\n")


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            if isinstance(item, dict):
                rows.append(item)
    return rows


def build_short_memory_entry(
    *,
    app_id: Optional[str],
    event_type: str,
    msg: str,
    ts: str,
    outcome: Optional[str] = None,
) -> Dict[str, Any]:
    outcome_weights = {
        "blocked": 0.2,
        "no_response": 0.3,
        "rejected": 0.4,
        "response": 0.7,
        "interview": 0.9,
        "offer": 1.0,
    }
    score_hint = outcome_weights.get((outcome or "").strip(), 0.35)
    return {
        "kind": "episodic",
        "ts": ts,
        "app_id": app_id,
        "event_type": event_type,
        "outcome": outcome,
        "score_hint": score_hint,
        "text": msg,
    }


def build_long_memory_entry(rec: Dict[str, Any], *, ts: str) -> Dict[str, Any]:
    status = str(rec.get("status", "") or "")
    status_priority = {
        "Offer": 1.0,
        "Applied": 0.8,
        "Rejected": 0.5,
        "Blocked": 0.3,
        "Draft": 0.2,
        "Closed": 0.1,
    }
    tags = rec.get("tags", [])
    tags_list = tags if isinstance(tags, list) else []
    summary = " ".join(
        [
            str(rec.get("company", "")),
            str(rec.get("role", "")),
            " ".join(str(t) for t in tags_list),
            str(rec.get("application_method", "")),
            str(rec.get("notes", ""))[:240],
        ]
    ).strip()
    return {
        "kind": "semantic",
        "ts": ts,
        "app_id": rec.get("app_id"),
        "company": rec.get("company"),
        "role": rec.get("role"),
        "status": status,
        "application_method": rec.get("application_method"),
        "tags": tags_list,
        "priority": status_priority.get(status, 0.4),
        "summary": summary,
    }


def recency_scores(
    rows: List[Dict[str, Any]], *, now_ts: str, half_life_days: float = 14.0
) -> Dict[str, float]:
    now = _parse_iso_utc(now_ts) or datetime.now(timezone.utc)
    by_app: Dict[str, float] = {}

    for row in rows:
        app_id = str(row.get("app_id", "") or "")
        if not app_id:
            continue
        ts = _parse_iso_utc(str(row.get("ts", "") or ""))
        if ts is None:
            continue
        age_days = max(0.0, (now - ts).total_seconds() / 86400.0)
        decay = math.exp(-math.log(2.0) * age_days / max(0.1, half_life_days))
        weight = float(row.get("score_hint", 0.35) or 0.35)
        score = max(0.0, min(1.0, decay * weight))
        by_app[app_id] = max(by_app.get(app_id, 0.0), score)

    return by_app


def long_memory_scores(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    by_app: Dict[str, float] = {}
    for row in rows:
        app_id = str(row.get("app_id", "") or "")
        if not app_id:
            continue
        priority = float(row.get("priority", 0.4) or 0.4)
        priority = max(0.0, min(1.0, priority))
        by_app[app_id] = max(by_app.get(app_id, 0.0), priority)
    return by_app
