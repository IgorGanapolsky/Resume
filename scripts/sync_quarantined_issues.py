#!/usr/bin/env python3
"""Sync quarantined tracker rows into GitHub issues."""

from __future__ import annotations

import argparse
import csv
import importlib
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
normalize_row = importlib.import_module("rag.memalign").normalize_row


TRACKER_CSV = ROOT / "applications" / "job_applications" / "application_tracker.csv"
DEFAULT_REPORT = (
    ROOT / "applications" / "job_applications" / "quarantine_issue_sync_report.json"
)
QUARANTINED_STATUS = "Quarantined"
APP_ID_MARKER = "resume-quarantine-app-id"


@dataclass(frozen=True)
class QuarantinedApplication:
    app_id: str
    company: str
    role: str
    url: str
    notes: str
    response: str
    interview_stage: str
    submission_lane: str
    remote_policy: str
    remote_score: str
    evidence_paths: List[str]


@dataclass(frozen=True)
class ExistingIssue:
    number: int
    title: str
    body: str
    state: str
    url: str
    app_id: str


@dataclass(frozen=True)
class SyncAction:
    action: str
    app_id: str
    title: str
    body: str
    issue_number: Optional[int] = None
    issue_url: str = ""


def _norm_status(status: str) -> str:
    return (status or "").strip().lower()


def _collect_evidence_paths(row: Dict[str, str]) -> List[str]:
    candidates: List[str] = []
    explicit = str(row.get("Submission Evidence Path", "")).strip()
    if explicit:
        candidates.append(explicit)
    for token in str(row.get("Notes", "")).replace('"', " ").split():
        if token.startswith("applications/") and (
            token.endswith(".png")
            or token.endswith(".pdf")
            or token.endswith(".md")
            or token.endswith(".html")
        ):
            candidates.append(token.rstrip(".,;:"))
    deduped: List[str] = []
    seen = set()
    for item in candidates:
        if item and item not in seen:
            deduped.append(item)
            seen.add(item)
    return deduped


def load_quarantined_applications(tracker_csv: Path) -> List[QuarantinedApplication]:
    with tracker_csv.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    out: List[QuarantinedApplication] = []
    for row in rows:
        if _norm_status(str(row.get("Status", ""))) != _norm_status(QUARANTINED_STATUS):
            continue
        normalized = normalize_row(row)
        out.append(
            QuarantinedApplication(
                app_id=str(normalized.get("app_id", "")),
                company=str(row.get("Company", "")).strip(),
                role=str(row.get("Role", "")).strip(),
                url=str(row.get("Career Page URL", "")).strip(),
                notes=str(row.get("Notes", "")).strip(),
                response=str(row.get("Response", "")).strip(),
                interview_stage=str(row.get("Interview Stage", "")).strip(),
                submission_lane=str(row.get("Submission Lane", "")).strip(),
                remote_policy=str(row.get("Remote Policy", "")).strip(),
                remote_score=str(row.get("Remote Likelihood Score", "")).strip(),
                evidence_paths=_collect_evidence_paths(row),
            )
        )
    return out


def build_issue_title(app: QuarantinedApplication) -> str:
    return f"Quarantine triage: {app.company} - {app.role}"


def build_issue_body(app: QuarantinedApplication) -> str:
    evidence_lines = (
        "\n".join(f"- {path}" for path in app.evidence_paths)
        if app.evidence_paths
        else "- No evidence path recorded in tracker"
    )
    notes = app.notes or "No additional notes recorded."
    return (
        f"<!-- {APP_ID_MARKER}: {app.app_id} -->\n"
        "## Summary\n"
        f"- Company: {app.company}\n"
        f"- Role: {app.role}\n"
        f"- Tracker status: {QUARANTINED_STATUS}\n"
        f"- Submission lane: {app.submission_lane or 'manual'}\n"
        f"- Interview stage: {app.interview_stage or 'Initial'}\n"
        f"- Response: {app.response or 'Submit blocked'}\n"
        f"- Remote profile: {app.remote_policy or 'unknown'}"
        f" ({app.remote_score or 'n/a'})\n"
        f"- Career page: {app.url or 'missing'}\n\n"
        "## Evidence\n"
        f"{evidence_lines}\n\n"
        "## Tracker Notes\n"
        f"{notes}\n\n"
        "## Done When\n"
        "- [ ] A human or adapter retry path is chosen\n"
        "- [ ] Fresh submission evidence is captured or the application is explicitly closed\n"
        "- [ ] `applications/job_applications/application_tracker.csv` is updated with the outcome\n"
    )


def _extract_app_id(body: str) -> str:
    prefix = f"<!-- {APP_ID_MARKER}:"
    for line in (body or "").splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix) and stripped.endswith("-->"):
            return stripped[len(prefix) : -3].strip()
    return ""


def _run_gh_json(args: Sequence[str]) -> List[Dict[str, Any]]:
    proc = subprocess.run(
        list(args),
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            proc.stderr.strip() or proc.stdout.strip() or "gh command failed"
        )
    payload = json.loads(proc.stdout or "[]")
    return payload if isinstance(payload, list) else []


def load_existing_issues(repo: str) -> Dict[str, ExistingIssue]:
    payload = _run_gh_json(
        [
            "gh",
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "all",
            "--limit",
            "200",
            "--json",
            "number,title,body,state,url",
        ]
    )
    out: Dict[str, ExistingIssue] = {}
    for item in payload:
        body = str(item.get("body", "") or "")
        app_id = _extract_app_id(body)
        if not app_id:
            continue
        out[app_id] = ExistingIssue(
            number=int(item.get("number", 0) or 0),
            title=str(item.get("title", "") or ""),
            body=body,
            state=str(item.get("state", "") or ""),
            url=str(item.get("url", "") or ""),
            app_id=app_id,
        )
    return out


def build_sync_plan(
    apps: Sequence[QuarantinedApplication],
    existing: Dict[str, ExistingIssue],
    *,
    close_resolved: bool,
) -> List[SyncAction]:
    actions: List[SyncAction] = []
    current_ids = {app.app_id for app in apps}

    for app in apps:
        title = build_issue_title(app)
        body = build_issue_body(app)
        issue = existing.get(app.app_id)
        if issue is None:
            actions.append(SyncAction("create", app.app_id, title, body))
            continue
        if issue.state.lower() != "open":
            actions.append(
                SyncAction(
                    "reopen",
                    app.app_id,
                    title,
                    body,
                    issue_number=issue.number,
                    issue_url=issue.url,
                )
            )
            continue
        if issue.title != title or issue.body != body:
            actions.append(
                SyncAction(
                    "update",
                    app.app_id,
                    title,
                    body,
                    issue_number=issue.number,
                    issue_url=issue.url,
                )
            )
            continue
        actions.append(
            SyncAction(
                "noop",
                app.app_id,
                title,
                body,
                issue_number=issue.number,
                issue_url=issue.url,
            )
        )

    if close_resolved:
        for app_id, issue in existing.items():
            if issue.state.lower() != "open":
                continue
            if app_id in current_ids:
                continue
            actions.append(
                SyncAction(
                    "close",
                    app_id,
                    issue.title,
                    issue.body,
                    issue_number=issue.number,
                    issue_url=issue.url,
                )
            )

    return actions


def _run_gh_write(args: Sequence[str]) -> None:
    proc = subprocess.run(
        list(args),
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            proc.stderr.strip() or proc.stdout.strip() or "gh command failed"
        )


def apply_sync_plan(plan: Sequence[SyncAction], repo: str) -> None:
    for action in plan:
        if action.action in {"noop"}:
            continue
        if action.action == "create":
            _run_gh_write(
                [
                    "gh",
                    "issue",
                    "create",
                    "--repo",
                    repo,
                    "--title",
                    action.title,
                    "--body",
                    action.body,
                ]
            )
            continue
        if action.issue_number is None:
            raise RuntimeError(f"Missing issue number for action {action.action}")
        if action.action == "update":
            _run_gh_write(
                [
                    "gh",
                    "issue",
                    "edit",
                    str(action.issue_number),
                    "--repo",
                    repo,
                    "--title",
                    action.title,
                    "--body",
                    action.body,
                ]
            )
            continue
        if action.action == "reopen":
            _run_gh_write(
                [
                    "gh",
                    "issue",
                    "reopen",
                    str(action.issue_number),
                    "--repo",
                    repo,
                ]
            )
            _run_gh_write(
                [
                    "gh",
                    "issue",
                    "edit",
                    str(action.issue_number),
                    "--repo",
                    repo,
                    "--title",
                    action.title,
                    "--body",
                    action.body,
                ]
            )
            continue
        if action.action == "close":
            _run_gh_write(
                [
                    "gh",
                    "issue",
                    "close",
                    str(action.issue_number),
                    "--repo",
                    repo,
                    "--comment",
                    "Closing automatically because this application is no longer quarantined in the tracker.",
                ]
            )
            continue
        raise RuntimeError(f"Unsupported action {action.action}")


def run_sync(
    *,
    tracker_csv: Path,
    report_path: Path,
    repo: str,
    apply: bool,
    close_resolved: bool,
    existing_issues: Optional[Dict[str, ExistingIssue]] = None,
) -> int:
    apps = load_quarantined_applications(tracker_csv)
    existing = (
        existing_issues if existing_issues is not None else load_existing_issues(repo)
    )
    plan = build_sync_plan(apps, existing, close_resolved=close_resolved)

    if apply:
        apply_sync_plan(plan, repo)

    report = {
        "tracker_csv": str(tracker_csv),
        "repo": repo,
        "apply": apply,
        "close_resolved": close_resolved,
        "quarantined_count": len(apps),
        "actions": [asdict(item) for item in plan],
        "quarantined_apps": [asdict(app) for app in apps],
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )

    summary_counts: Dict[str, int] = {}
    for item in plan:
        summary_counts[item.action] = summary_counts.get(item.action, 0) + 1
    print(
        "Quarantine issue sync: "
        f"quarantined={len(apps)} actions={json.dumps(summary_counts, ensure_ascii=True, sort_keys=True)} "
        f"report={report_path}"
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tracker", default=str(TRACKER_CSV), help="Tracker CSV path")
    ap.add_argument(
        "--report",
        default=str(DEFAULT_REPORT),
        help="Write a JSON report describing the sync plan.",
    )
    ap.add_argument("--repo", required=True, help="GitHub repo in owner/name format")
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Apply creates/updates/reopens/closes through the gh CLI.",
    )
    ap.add_argument(
        "--close-resolved",
        action="store_true",
        help="Close open quarantine issues whose app_id is no longer quarantined.",
    )
    args = ap.parse_args()
    return run_sync(
        tracker_csv=Path(args.tracker),
        report_path=Path(args.report),
        repo=args.repo,
        apply=args.apply,
        close_resolved=args.close_resolved,
    )


if __name__ == "__main__":
    raise SystemExit(main())
