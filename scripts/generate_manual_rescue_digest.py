#!/usr/bin/env python3
"""Generate a markdown digest of captcha/anti-bot-blocked ReadyToSubmit rows.

When Anchor Browser is out of credits or stealth fails, the submit pipeline
cannot auto-apply. This script emits a prioritized one-click-submit digest so
the human operator can clear the top N blocked rows in a few minutes.

Priority: AI-labs and Ashby cohort first (thesis-aligned), then remainder.
For each row we include: apply URL, tailored resume path, cover letter path.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
from pathlib import Path
from typing import Dict, List, Sequence


ROOT = Path(__file__).resolve().parents[1]
TRACKER = ROOT / "applications" / "job_applications" / "application_tracker.csv"
DEFAULT_OUT = (
    ROOT / "applications" / "job_applications" / "manual_rescue_digest.md"
)

THESIS_COMPANIES = (
    "OpenAI",
    "Anthropic",
    "Cohere",
    "Perplexity",
    "Cursor",
    "Replit",
    "Character.AI",
    "Writer",
    "Notion",
    "Ramp",
    "Plaid",
    "ElevenLabs",
    "Snowflake",
    "Databricks",
    "Stripe",
    "Scale AI",
)

THESIS_ROLE_NEEDLES = (
    "forward deployed",
    "deployment engineer",
    "codex",
    "solutions engineer",
    "solutions architect",
    "applied ai",
    "customer engineer",
    "technical account",
    "partner engineer",
)

NON_US_NEEDLES = (
    "tokyo",
    "apac",
    "london",
    "munich",
    "paris",
    "dublin",
    "singapore",
    "sydney",
    "seoul",
    "india",
    "bangalore",
    "amsterdam",
    "zurich",
    "berlin",
    "sao paulo",
    "são paulo",
    "mexico city",
    "mumbai",
)


def _is_thesis_role(role: str) -> bool:
    low = role.lower()
    return any(n in low for n in THESIS_ROLE_NEEDLES)


def _is_us_location(role: str, location: str) -> bool:
    blob = f"{role} {location}".lower()
    return not any(n in blob for n in NON_US_NEEDLES)


def _priority(row: Dict[str, str]) -> int:
    """Lower = higher priority."""
    company = (row.get("Company") or "").strip()
    role = (row.get("Role") or "").strip()
    location = (row.get("Location") or "").strip()
    thesis_co = company in THESIS_COMPANIES
    thesis_role = _is_thesis_role(role)
    us_loc = _is_us_location(role, location)
    if thesis_co and thesis_role and us_loc:
        return 0
    if thesis_co and thesis_role:
        return 1
    if thesis_co and us_loc:
        return 2
    if thesis_role and us_loc:
        return 3
    if thesis_co:
        return 4
    return 5


def _row_has_tailored_resume(row: Dict[str, str]) -> bool:
    path = (row.get("Submitted Resume Path") or "").strip()
    return bool(path) and (ROOT / path).exists()


def _find_tailored_resume(row: Dict[str, str]) -> str:
    """Best-effort match by slug under applications/<company>/tailored_resumes/."""
    company = (row.get("Company") or "").strip().lower().replace(" ", "-").replace(".", "")
    if not company:
        return ""
    base = ROOT / "applications" / company / "tailored_resumes"
    if not base.exists():
        return ""
    role_slug = (
        (row.get("Role") or "")
        .strip()
        .lower()
        .replace(" ", "-")
        .replace("(", "")
        .replace(")", "")
        .replace(",", "")
        .replace("—", "-")
        .replace("/", "-")
    )
    # Match any .docx whose name contains a large prefix of the role slug.
    for path in sorted(base.glob("*.docx")):
        name = path.stem.lower()
        if role_slug[:30] in name:
            return str(path.relative_to(ROOT))
    # Fallback: return the newest docx in the company dir.
    docs = sorted(base.glob("*.docx"), key=lambda p: p.stat().st_mtime, reverse=True)
    return str(docs[0].relative_to(ROOT)) if docs else ""


def _find_cover_letter(row: Dict[str, str]) -> str:
    used = (row.get("Cover Letter Used") or "").strip()
    if used:
        candidate = ROOT / "applications" / (used if used.endswith(".md") else used + ".md")
        # Try a few plausible paths
        plausible = [
            ROOT / f"applications/{(row.get('Company') or '').strip().lower().replace(' ','-')}/cover_letters/{used}.md",
            ROOT / f"applications/{(row.get('Company') or '').strip().lower().replace(' ','-')}/cover_letters/{used}",
            candidate,
        ]
        for p in plausible:
            if p.exists():
                return str(p.relative_to(ROOT))
    return ""


def _is_blocked(row: Dict[str, str]) -> bool:
    if (row.get("Status") or "").strip() != "ReadyToSubmit":
        return False
    notes = (row.get("Notes") or "").lower()
    # antibot markers or CI skip reasons
    return any(
        marker in notes
        for marker in (
            "anti-bot",
            "recaptcha",
            "antibot_blocked",
            "required_fields_unanswered",
            "resume_input_missing",
            "confirmation_text_not_detected",
        )
    ) or "insufficient credits" in notes


def _row_blob(row: Dict[str, str]) -> str:
    return f"{row.get('Company','')}|{row.get('Role','')}|{row.get('Career Page URL','')}"


def build_digest(
    rows: Sequence[Dict[str, str]],
    limit: int,
    only_thesis: bool,
) -> str:
    candidates: List[Dict[str, str]] = []
    for row in rows:
        if (row.get("Status") or "").strip() != "ReadyToSubmit":
            continue
        company = (row.get("Company") or "").strip()
        role = (row.get("Role") or "").strip()
        location = (row.get("Location") or "").strip()
        if not _is_us_location(role, location):
            continue
        if only_thesis and not (
            company in THESIS_COMPANIES or _is_thesis_role(role)
        ):
            continue
        candidates.append(row)

    candidates.sort(
        key=lambda r: (
            _priority(r),
            (r.get("Company") or "").lower(),
            (r.get("Role") or "").lower(),
        )
    )
    picks = candidates[:limit]

    lines: List[str] = []
    lines.append(f"# Manual Rescue Digest — {dt.date.today().isoformat()}")
    lines.append("")
    lines.append(
        "Top thesis-aligned ReadyToSubmit rows blocked by captcha / anti-bot."
    )
    lines.append(
        "For each: open Apply URL → paste tailored resume → paste cover letter → submit."
    )
    lines.append("")
    lines.append(f"- Total ReadyToSubmit rows in tracker: {sum(1 for r in rows if (r.get('Status') or '').strip()=='ReadyToSubmit')}")
    lines.append(f"- US-eligible candidates surfaced: {len(candidates)}")
    lines.append(f"- Showing top: {len(picks)}")
    lines.append("")
    by_priority: Dict[int, List[Dict[str, str]]] = {}
    for row in picks:
        by_priority.setdefault(_priority(row), []).append(row)

    bucket_titles = {
        0: "Tier 0 — Thesis company + thesis role + US",
        1: "Tier 1 — Thesis company + thesis role (any location)",
        2: "Tier 2 — Thesis company + US (role adjacent)",
        3: "Tier 3 — Thesis role + US (any company)",
        4: "Tier 4 — Thesis company only",
        5: "Tier 5 — Other (fallback)",
    }

    for tier in sorted(by_priority.keys()):
        lines.append(f"## {bucket_titles[tier]}")
        lines.append("")
        for row in by_priority[tier]:
            company = (row.get("Company") or "").strip()
            role = (row.get("Role") or "").strip()
            url = (row.get("Career Page URL") or "").strip()
            location = (row.get("Location") or "").strip()
            resume = (row.get("Submitted Resume Path") or "").strip() or _find_tailored_resume(row)
            cover = _find_cover_letter(row)
            lines.append(f"### {company} — {role}")
            lines.append(f"- Location: {location}")
            lines.append(f"- Apply: {url}")
            if resume:
                lines.append(f"- Resume: `{resume}`")
            else:
                lines.append("- Resume: _(none tailored yet)_")
            if cover:
                lines.append(f"- Cover letter: `{cover}`")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tracker", default=str(TRACKER))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument(
        "--only-thesis",
        action="store_true",
        default=True,
        help="Restrict to thesis companies or thesis role needles (default on).",
    )
    ap.add_argument(
        "--include-all",
        dest="only_thesis",
        action="store_false",
        help="Override and include non-thesis rows.",
    )
    args = ap.parse_args()

    tracker_path = Path(args.tracker)
    with tracker_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    digest = build_digest(rows, limit=args.limit, only_thesis=args.only_thesis)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(digest, encoding="utf-8")
    print(f"Wrote {out_path} ({len(digest)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
