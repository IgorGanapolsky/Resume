"""Shared RLHF learning helpers for Ralph Loop.

This module turns the persisted Thompson-sampling arms into:
- machine-readable learning reports
- deterministic priority scores for ReadyToSubmit / Draft rows
"""

from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_ATS_PATTERNS: Sequence[Tuple[str, re.Pattern[str]]] = (
    ("mercor", re.compile(r"work\.mercor\.com", re.I)),
    ("ashby", re.compile(r"ashbyhq\.com", re.I)),
    ("greenhouse", re.compile(r"greenhouse\.io|job-boards\.greenhouse\.io", re.I)),
    ("lever", re.compile(r"jobs\.lever\.co", re.I)),
    ("wellfound", re.compile(r"wellfound\.com|angel\.co", re.I)),
    ("workday", re.compile(r"myworkdayjobs\.com|workday\.com", re.I)),
    ("linkedin", re.compile(r"linkedin\.com/jobs", re.I)),
)
_READY_STATUS_KEYS = {"readytosubmit", "ready_to_submit", "ready to submit"}
_DRAFT_STATUS_KEYS = {"draft"}
_ANTIBOT_MARKERS = (
    "recaptcha_score_below_threshold",
    "possible spam",
    "anti-bot",
)
_MANUAL_BLOCK_MARKERS = (
    "manual browser submit required",
    "manual rescue evidence",
    "needs manual completion",
)


def _norm_key(text: str) -> str:
    return re.sub(r"[\s_]+", "", (text or "").strip().lower())


def slug(text: str) -> str:
    return _NON_ALNUM_RE.sub("-", (text or "").strip().lower()).strip("-") or "unknown"


def parse_tags(raw: str) -> List[str]:
    if not raw:
        return []
    return [part.strip() for part in str(raw).split(";") if part.strip()]


def infer_application_method(url: str) -> str:
    hay = str(url or "").strip().lower()
    for name, pattern in _ATS_PATTERNS:
        if pattern.search(hay):
            return name
    return "direct"


def is_ready_status(status: str) -> bool:
    return _norm_key(status) in _READY_STATUS_KEYS


def is_draft_status(status: str) -> bool:
    return _norm_key(status) in _DRAFT_STATUS_KEYS


def load_tracker_rows(path: Path) -> List[Dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [row for row in csv.DictReader(handle) if any(str(v).strip() for v in row.values())]


def load_arms(path: Path) -> Dict[str, Dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def arm_mean(arm: Dict[str, Any] | None) -> float:
    if not arm:
        return 0.5
    alpha = float(arm.get("alpha", 1.0) or 1.0)
    beta = float(arm.get("beta", 1.0) or 1.0)
    denom = alpha + beta
    if denom <= 0:
        return 0.5
    return alpha / denom


def arm_confidence(arm: Dict[str, Any] | None) -> float:
    if not arm:
        return 1.0
    pulls = int(arm.get("pulls", 0) or 0)
    if pulls <= 0:
        return 1.0
    return math.sqrt(2.0 * math.log(max(1, pulls + 1)) / (pulls + 1))


def learning_features_for_row(
    row: Dict[str, Any], arms: Dict[str, Dict[str, Any]]
) -> Dict[str, Any]:
    company = str(row.get("Company", "") or row.get("company", "")).strip()
    role = str(row.get("Role", "") or row.get("role", "")).strip()
    status = str(row.get("Status", "") or row.get("status", "")).strip()
    url = str(row.get("Career Page URL", "") or row.get("career_page_url", "")).strip()
    lane = str(row.get("Submission Lane", "") or row.get("submission_lane", "")).strip()
    tags = row.get("Tags")
    if isinstance(tags, list):
        tag_list = [str(tag).strip() for tag in tags if str(tag).strip()]
    else:
        tag_list = parse_tags(str(tags or row.get("tags", "")))

    method = infer_application_method(url)
    method_arm = arms.get(f"method:{method}")
    method_mean = arm_mean(method_arm)
    method_pulls = int((method_arm or {}).get("pulls", 0) or 0)

    tag_metrics: List[Dict[str, Any]] = []
    for tag in tag_list:
        arm = arms.get(f"cat:{tag}")
        if arm is None:
            continue
        tag_metrics.append(
            {
                "tag": tag,
                "mean_reward": round(arm_mean(arm), 4),
                "pulls": int(arm.get("pulls", 0) or 0),
            }
        )
    tag_metrics.sort(key=lambda item: (item["mean_reward"], item["pulls"], item["tag"]), reverse=True)
    tag_mean = (
        sum(float(item["mean_reward"]) for item in tag_metrics) / len(tag_metrics)
        if tag_metrics
        else 0.5
    )

    evidence_count = int(method_arm is not None) + len(tag_metrics)
    evidence_bonus = min(0.08, 0.01 * evidence_count)
    learned_score = min(1.0, 0.52 * method_mean + 0.40 * tag_mean + evidence_bonus)

    notes = str(row.get("Notes", "") or row.get("notes", "")).lower()
    antibot_flag = any(marker in notes for marker in _ANTIBOT_MARKERS)
    manual_flag = any(marker in notes for marker in _MANUAL_BLOCK_MARKERS)
    lane_priority = 0 if lane.startswith("ci_auto") else 1
    adjusted_score = learned_score - (0.04 if antibot_flag else 0.0) - (0.03 if manual_flag else 0.0)

    return {
        "company": company,
        "role": role,
        "status": status,
        "career_page_url": url,
        "submission_lane": lane,
        "method": method,
        "tags": tag_list,
        "method_mean_reward": round(method_mean, 4),
        "method_pulls": method_pulls,
        "tag_mean_reward": round(tag_mean, 4),
        "matched_positive_tags": [
            item["tag"] for item in tag_metrics if item["mean_reward"] >= 0.55
        ][:6],
        "matched_negative_tags": [
            item["tag"] for item in tag_metrics if item["pulls"] >= 2 and item["mean_reward"] <= 0.45
        ][:6],
        "learned_score": round(learned_score, 4),
        "adjusted_score": round(max(0.0, adjusted_score), 4),
        "lane_priority": lane_priority,
        "antibot_history": antibot_flag,
        "manual_history": manual_flag,
    }


def rank_rows_by_learning(
    rows: Sequence[Dict[str, Any]],
    arms: Dict[str, Dict[str, Any]],
    *,
    status_filter: str = "ready",
    max_rows: int = 0,
) -> List[Dict[str, Any]]:
    ranked: List[Dict[str, Any]] = []
    for idx, row in enumerate(rows):
        status = str(row.get("Status", "") or row.get("status", "")).strip()
        if status_filter == "ready" and not is_ready_status(status):
            continue
        if status_filter == "draft" and not is_draft_status(status):
            continue
        features = learning_features_for_row(row, arms)
        features["row_index"] = idx
        ranked.append(features)

    ranked.sort(
        key=lambda item: (
            item["lane_priority"],
            -float(item["adjusted_score"]),
            item["company"].lower(),
            item["role"].lower(),
        )
    )
    if max_rows > 0:
        return ranked[: max_rows]
    return ranked


def build_learning_report(
    tracker_rows: Sequence[Dict[str, Any]],
    arms: Dict[str, Dict[str, Any]],
    *,
    top_k: int = 10,
    tracker_csv: str = "",
    arms_json: str = "",
) -> Dict[str, Any]:
    status_counts = Counter(str(row.get("Status", "")).strip() or "Unknown" for row in tracker_rows)
    method_friction: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {
            "rows": 0,
            "ready_rows": 0,
            "applied_rows": 0,
            "antibot_count": 0,
            "manual_block_count": 0,
        }
    )

    for row in tracker_rows:
        method = infer_application_method(str(row.get("Career Page URL", "")).strip())
        status = str(row.get("Status", "")).strip()
        notes = str(row.get("Notes", "")).lower()
        bucket = method_friction[method]
        bucket["rows"] += 1
        if is_ready_status(status):
            bucket["ready_rows"] += 1
        if status == "Applied":
            bucket["applied_rows"] += 1
        if any(marker in notes for marker in _ANTIBOT_MARKERS):
            bucket["antibot_count"] += 1
        if any(marker in notes for marker in _MANUAL_BLOCK_MARKERS) or status == "Quarantined":
            bucket["manual_block_count"] += 1

    method_summary: List[Dict[str, Any]] = []
    category_summary: List[Dict[str, Any]] = []
    for arm_name, arm in arms.items():
        entry = {
            "arm": arm_name,
            "mean_reward": round(arm_mean(arm), 4),
            "confidence": round(arm_confidence(arm), 4),
            "pulls": int(arm.get("pulls", 0) or 0),
            "total_reward": round(float(arm.get("total_reward", 0.0) or 0.0), 4),
        }
        if arm_name.startswith("method:"):
            method = arm_name.split(":", 1)[1]
            entry.update(method_friction.get(method, {}))
            method_summary.append(entry)
        elif arm_name.startswith("cat:"):
            category_summary.append(entry)

    method_summary.sort(
        key=lambda item: (-float(item["mean_reward"]), -int(item["pulls"]), item["arm"])
    )
    category_summary.sort(
        key=lambda item: (-float(item["mean_reward"]), -int(item["pulls"]), item["arm"])
    )

    ready_ranked = rank_rows_by_learning(tracker_rows, arms, status_filter="ready", max_rows=top_k)
    draft_ranked = rank_rows_by_learning(tracker_rows, arms, status_filter="draft", max_rows=top_k)
    manual_rescue_queue = [
        item
        for item in ready_ranked
        if item["antibot_history"] or item["manual_history"]
    ][:top_k]

    action_summary: List[str] = []
    if ready_ranked:
        top_ready = ready_ranked[0]
        action_summary.append(
            f"Prioritize {top_ready['company']} / {top_ready['role']} "
            f"(lane={top_ready['submission_lane'] or 'manual'}, adjusted_score={top_ready['adjusted_score']})."
        )
    if method_summary:
        top_method = method_summary[0]
        action_summary.append(
            f"Best historical method arm is {top_method['arm']} "
            f"(mean_reward={top_method['mean_reward']}, pulls={top_method['pulls']})."
        )
    noisy_methods = [
        item for item in method_summary if int(item.get("antibot_count", 0) or 0) > 0
    ]
    if noisy_methods:
        worst = max(noisy_methods, key=lambda item: int(item.get("antibot_count", 0) or 0))
        action_summary.append(
            f"{worst['arm']} shows anti-bot friction on {worst['antibot_count']} tracked rows; "
            "keep headed-browser/manual-rescue behavior for that lane."
        )
    if draft_ranked:
        top_draft = draft_ranked[0]
        action_summary.append(
            f"Best draft prep target is {top_draft['company']} / {top_draft['role']} "
            f"(adjusted_score={top_draft['adjusted_score']})."
        )

    return {
        "generated_at_iso": datetime.now(timezone.utc).isoformat(),
        "tracker_csv": tracker_csv,
        "arms_json": arms_json,
        "total_tracker_rows": len(tracker_rows),
        "total_arms": len(arms),
        "status_counts": dict(status_counts),
        "method_summary": method_summary[:top_k],
        "category_summary": category_summary[:top_k],
        "ready_ranked": ready_ranked,
        "draft_ranked": draft_ranked,
        "manual_rescue_queue": manual_rescue_queue,
        "action_summary": action_summary,
    }

