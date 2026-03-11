#!/usr/bin/env python3
"""Capture optional Playwright auth state for CI job submissions."""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, MutableMapping, Optional, Sequence


SUPPORTED_ADAPTERS = ("ashby", "greenhouse", "lever")


@dataclass(frozen=True)
class CaptureTarget:
    adapter: str
    url: str


def parse_capture_target(value: str) -> CaptureTarget:
    adapter_raw, separator, url_raw = value.partition("=")
    adapter = adapter_raw.strip().lower()
    url = url_raw.strip()
    if not separator or not adapter or not url:
        raise argparse.ArgumentTypeError(
            "Expected --capture in the form adapter=https://example.com/job"
        )
    if adapter not in SUPPORTED_ADAPTERS:
        raise argparse.ArgumentTypeError(
            "Unsupported adapter. Use one of: " + ", ".join(SUPPORTED_ADAPTERS)
        )
    if not (url.startswith("http://") or url.startswith("https://")):
        raise argparse.ArgumentTypeError(
            "Capture URL must start with http:// or https://"
        )
    return CaptureTarget(adapter=adapter, url=url)


def load_auth_payload(path: Path) -> Dict[str, Dict[str, object]]:
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Existing auth payload must be a JSON object.")
    return normalize_auth_payload(payload)


def normalize_auth_payload(
    payload: Mapping[str, object],
) -> Dict[str, Dict[str, object]]:
    normalized: Dict[str, Dict[str, object]] = {}
    for adapter, item in payload.items():
        if not isinstance(adapter, str) or adapter not in SUPPORTED_ADAPTERS:
            continue
        if not isinstance(item, Mapping):
            continue
        storage_state = item.get("storage_state")
        if isinstance(storage_state, Mapping):
            normalized[adapter] = {"storage_state": dict(storage_state)}
    return normalized


def merge_auth_payload(
    base_payload: Mapping[str, object],
    updates: Mapping[str, Mapping[str, object]],
) -> Dict[str, Dict[str, object]]:
    merged = normalize_auth_payload(base_payload)
    for adapter, storage_state in updates.items():
        merged[adapter] = {"storage_state": dict(storage_state)}
    return merged


def resolve_repo(explicit_repo: str) -> str:
    if explicit_repo.strip():
        return explicit_repo.strip()
    proc = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
        check=True,
        capture_output=True,
        text=True,
    )
    repo = proc.stdout.strip()
    if not repo:
        raise RuntimeError(
            "Could not resolve GitHub repository. Pass --repo owner/name explicitly."
        )
    return repo


def set_secret(repo: str, secret_name: str, value: str) -> None:
    subprocess.run(
        ["gh", "secret", "set", secret_name, "--repo", repo],
        check=True,
        input=value,
        text=True,
    )


def capture_storage_state(
    target: CaptureTarget, *, headless: bool
) -> Dict[str, object]:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except (
        Exception
    ) as exc:  # pragma: no cover - exercised only when Playwright missing
        raise RuntimeError(
            "Playwright is required. Install it with `pip install playwright` and "
            "`python -m playwright install chromium`."
        ) from exc

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()
        print(f"Opening {target.url} for adapter={target.adapter}")
        page.goto(target.url, wait_until="domcontentloaded", timeout=60000)
        print(
            "Complete any login, anti-bot challenge, or form preflight in the opened "
            "browser window, then press Enter here."
        )
        input()
        storage_state = context.storage_state()
        browser.close()
    if not isinstance(storage_state, dict):
        raise RuntimeError("Playwright returned a non-dict storage state payload.")
    return storage_state


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--capture",
        action="append",
        required=True,
        type=parse_capture_target,
        help=(
            "Repeatable adapter=url entry. Example: "
            "--capture ashby=https://jobs.ashbyhq.com/company/role"
        ),
    )
    parser.add_argument(
        "--merge-from",
        default="",
        help="Optional existing auth JSON file to merge into the new payload.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional path to write the merged auth JSON. Keep it outside the repo.",
    )
    parser.add_argument(
        "--sync-secret",
        action="store_true",
        help="Sync the merged payload into a GitHub Actions secret using `gh`.",
    )
    parser.add_argument(
        "--repo",
        default="",
        help="GitHub repository in owner/name form. Defaults to `gh repo view`.",
    )
    parser.add_argument(
        "--secret-name",
        default="CI_SUBMIT_AUTH_JSON",
        help="GitHub Actions secret name to update when --sync-secret is used.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run Chromium headless. Headed mode is recommended for manual challenges.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.output and not args.sync_secret:
        parser.error("Use at least one of --output or --sync-secret.")

    base_payload: Mapping[str, object] = {}
    if args.merge_from:
        base_payload = load_auth_payload(Path(args.merge_from))

    updates: MutableMapping[str, Mapping[str, object]] = {}
    for target in args.capture:
        updates[target.adapter] = capture_storage_state(target, headless=args.headless)

    merged = merge_auth_payload(base_payload, updates)
    payload_json = json.dumps(merged, ensure_ascii=True, indent=2)

    if args.output:
        output_path = Path(args.output).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload_json + "\n", encoding="utf-8")
        print(f"Wrote auth payload to {output_path}")

    if args.sync_secret:
        repo = resolve_repo(args.repo)
        set_secret(repo, args.secret_name, payload_json)
        print(f"Updated GitHub Actions secret in {repo}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
