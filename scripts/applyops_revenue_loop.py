#!/usr/bin/env python3
"""Guarded ApplyOps revenue loop.

Publishes the ApplyOps offer through Zernio on a cadence without storing
secrets or promising outcomes. Designed for scheduled Ralph-style operation.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import textwrap
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports" / "gtm" / "2026-05-07-applyops"
LOG_PATH = REPORT_DIR / "applyops_revenue_loop.jsonl"
ZERNIO_BASE = "https://zernio.com/api/v1"
CHECKOUT_URL = "https://buy.stripe.com/4gM28rcqM1eSdWhayn3sI1U"
CAMPAIGN = "applyops_truth_audit_2026_05_07"

TEXT_POST = f"""AI resumes can quietly add risky claims.

I opened a $499 ApplyOps diagnostic for job seekers getting rejected or ghosted:

- resume truth audit
- safer ATS targeting
- job-fit queue
- referral drafts
- 7-day sprint plan

No fake claims. No blind mass applying.

{CHECKOUT_URL}"""

BLUESKY_POST = (
    "AI resumes can quietly add risky claims. $499 ApplyOps diagnostic: "
    "resume truth audit, safer ATS targeting, job-fit queue, referral drafts, "
    f"and a 7-day sprint plan. {CHECKOUT_URL}"
)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def _append_log(row: dict[str, Any]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def _recent_publish_within(hours: int) -> bool:
    if not LOG_PATH.exists():
        return False
    cutoff = _now() - dt.timedelta(hours=hours)
    for line in LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("status") != "published":
            continue
        try:
            ts = dt.datetime.fromisoformat(str(row.get("timestamp", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts >= cutoff:
            return True
    return False


def _request(
    method: str,
    path: str,
    api_key: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not path.startswith("/"):
        raise ValueError("Zernio API path must be relative")
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        f"{ZERNIO_BASE}{path}",
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as response:  # nosec B310
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Zernio HTTP {exc.code}: {detail[:500]}") from exc


def _accounts(api_key: str) -> list[dict[str, Any]]:
    data = _request("GET", "/accounts", api_key)
    raw = data.get("accounts") or data.get("data") or data
    if not isinstance(raw, list):
        return []
    accounts: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        platform = str(item.get("platform") or "").strip()
        account_id = str(
            item.get("accountId") or item.get("_id") or item.get("id") or ""
        ).strip()
        if platform and account_id:
            accounts.append({"platform": platform, "accountId": account_id})
    return accounts


def _publish(api_key: str, accounts: list[dict[str, str]], text: str) -> dict[str, Any]:
    return _request(
        "POST",
        "/posts",
        api_key,
        {
            "content": text,
            "platforms": accounts,
            "publishNow": True,
        },
    )


def run(mode: str, confirm: str, min_hours_between_posts: int) -> int:
    timestamp = _now().isoformat()
    api_key = os.environ.get("ZERNIO_API_KEY", "").strip()
    if not api_key:
        _append_log({"timestamp": timestamp, "status": "skipped", "reason": "missing_zernio_api_key"})
        print("SKIPPED missing_zernio_api_key")
        return 0
    if mode == "publish" and confirm != "APPLYOPS_REVENUE":
        raise SystemExit("Publishing requires --confirm APPLYOPS_REVENUE")
    if _recent_publish_within(min_hours_between_posts):
        _append_log({"timestamp": timestamp, "status": "skipped", "reason": "recent_publish"})
        print("SKIPPED recent_publish")
        return 0

    accounts = _accounts(api_key)
    platform_counts: dict[str, int] = {}
    for account in accounts:
        platform_counts[account["platform"]] = platform_counts.get(account["platform"], 0) + 1

    text_platforms = [
        account
        for account in accounts
        if account["platform"] in {"threads", "twitter"}
    ]
    bluesky_accounts = [account for account in accounts if account["platform"] == "bluesky"]

    if mode == "dry-run":
        _append_log(
            {
                "timestamp": timestamp,
                "status": "dry_run",
                "platform_counts": platform_counts,
                "text_platforms": [a["platform"] for a in text_platforms],
                "bluesky": bool(bluesky_accounts),
            }
        )
        print(json.dumps({"status": "dry_run", "platform_counts": platform_counts}, indent=2))
        return 0

    published: list[dict[str, Any]] = []
    errors: list[str] = []
    if text_platforms:
        try:
            published.append(
                {
                    "platforms": [a["platform"] for a in text_platforms],
                    "result": _publish(api_key, text_platforms, TEXT_POST),
                }
            )
        except Exception as exc:
            errors.append(f"text_platforms:{exc}")
    if bluesky_accounts:
        try:
            published.append({"platforms": ["bluesky"], "result": _publish(api_key, bluesky_accounts, BLUESKY_POST)})
        except Exception as exc:
            errors.append(f"bluesky:{exc}")

    status = "published" if published and not errors else "partial" if published else "failed"
    _append_log(
        {
            "timestamp": timestamp,
            "status": status,
            "published_count": len(published),
            "errors": errors,
            "checkout_url": CHECKOUT_URL,
        }
    )
    print(
        json.dumps(
            {"status": status, "published_count": len(published), "errors": errors},
            indent=2,
        )
    )
    return 1 if status == "failed" else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """\
            Examples:
              python3 scripts/applyops_revenue_loop.py --mode dry-run
              python3 scripts/applyops_revenue_loop.py --mode publish --confirm APPLYOPS_REVENUE
            """
        ),
    )
    parser.add_argument("--mode", choices=["dry-run", "publish"], default="dry-run")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--min-hours-between-posts", type=int, default=12)
    args = parser.parse_args()
    return run(args.mode, args.confirm, args.min_hours_between_posts)


if __name__ == "__main__":
    raise SystemExit(main())
