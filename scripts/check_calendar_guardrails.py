#!/usr/bin/env python3
"""Fail-closed check that AGENTS.md contains strict calendar guardrails."""

from __future__ import annotations

import re
import sys
from pathlib import Path


HEADER = "## Calendar Guardrails (Non-Negotiable)"

REQUIRED_SNIPPETS = [
    "runtime environment only",
    "`today`, `yesterday`, `tomorrow`, `latest`, `current`, deadlines, age",
    "ISO format (`YYYY-MM-DD`)",
    "weekday and timezone",
    "Relative dates must always be converted to absolute dates",
    "malformed or ambiguous (example: `2026--02-19`)",
    "`UNVERIFIED_DATE_CLAIM`",
    "submitted today",
]


def _extract_section(text: str, header: str) -> str:
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == header:
            start = i + 1
            break
    if start is None:
        return ""

    section_lines = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        section_lines.append(line)
    return "\n".join(section_lines)


def _missing_requirements(section: str) -> list[str]:
    missing = []
    for snippet in REQUIRED_SNIPPETS:
        if snippet not in section:
            missing.append(snippet)
    for n in range(1, 7):
        if re.search(rf"^\s*{n}\.\s", section, flags=re.MULTILINE) is None:
            missing.append(f"numbered rule {n}.")
    return missing


def main() -> int:
    target = Path("AGENTS.md")
    if not target.exists():
        print("ERROR: AGENTS.md not found at repository root.")
        return 1

    text = target.read_text(encoding="utf-8")
    section = _extract_section(text, HEADER)
    if not section:
        print(f"ERROR: Missing required header: {HEADER}")
        return 1

    missing = _missing_requirements(section)
    if missing:
        print("ERROR: Calendar guardrails section is incomplete.")
        for item in missing:
            print(f"- missing: {item}")
        return 1

    print("OK: Calendar guardrails are present and complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
