#!/usr/bin/env python3
"""Strip embedded JavaScript/app-data blobs from job capture Markdown files.

Job pages scraped from Greenhouse/Ashby/Lever embed large <script> blocks
containing platform API keys and tokens in window.__appData / window.ENV.
This script removes those blocks in-place before files are committed.

Usage:
    python3 scripts/scrub_job_captures.py            # scrub all jobs/ files
    python3 scripts/scrub_job_captures.py --dry-run  # preview only
    python3 scripts/scrub_job_captures.py path/to/file.md
"""

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Matches window.ENV / window.__appData anywhere on a line (including mid-line minified JS)
_WINDOW_ASSIGN_RE = re.compile(
    r"window\.(?:ENV|__\w+)\s*=\s*\{[^;]*?\}(?=\s*[;,\n]|$)",
    re.IGNORECASE,
)
# Redact public recaptcha site keys that trigger gitleaks generic-api-key rule.
_RECAPTCHA_KEY_RE = re.compile(
    r'("recaptchaPublicSiteKey"\s*:\s*")[^"]+(")',
    re.IGNORECASE,
)
# Line-start variants (multi-line formatted)
_INLINE_DATA_RE = re.compile(
    r"^\s*window\.__\w+\s*=\s*\{.*",
    re.IGNORECASE,
)
_INLINE_ENV_RE = re.compile(
    r"^\s*window\.\w+\s*=\s*[\{\[].*",
    re.IGNORECASE,
)

_REDACT_LABEL = "<!-- [script/app-data removed by scrub_job_captures.py] -->"


def _strip_script_blocks(text: str) -> tuple[str, int]:
    """Strip <script>...</script> blocks using a simple scanner (no regex parser)."""
    lower = text.lower()
    out: list[str] = []
    i = 0
    removed = 0
    while True:
        start = lower.find("<script", i)
        if start == -1:
            out.append(text[i:])
            break
        out.append(text[i:start])
        end = lower.find("</script>", start)
        removed += 1
        out.append(_REDACT_LABEL)
        if end == -1:
            i = len(text)
            break
        i = end + len("</script>")
    return "".join(out), removed


def scrub(text: str) -> tuple[str, int]:
    """Return (scrubbed_text, change_count)."""
    changes = 0

    # Remove <script>...</script> blocks.
    scrubbed, n = _strip_script_blocks(text)
    changes += n
    text = scrubbed

    # Remove inline window.ENV / window.__appData JSON blobs (mid-line minified JS)
    scrubbed, n = _WINDOW_ASSIGN_RE.subn(_REDACT_LABEL, text)
    changes += n
    text = scrubbed

    # Redact recaptcha public site keys in JSON blobs.
    scrubbed, n = _RECAPTCHA_KEY_RE.subn(r"\1[REDACTED_PUBLIC_KEY]\2", text)
    changes += n
    text = scrubbed

    # Remove lines with window.__appData / window.ENV assignments
    lines = text.splitlines(keepends=True)
    clean_lines = []
    skip_until_close = False
    for line in lines:
        if skip_until_close:
            if line.strip().startswith("};") or line.strip() == "}":
                skip_until_close = False
            continue
        if _INLINE_DATA_RE.match(line) or _INLINE_ENV_RE.match(line):
            clean_lines.append(_REDACT_LABEL + "\n")
            changes += 1
            if not line.rstrip().endswith(";"):
                skip_until_close = True
            continue
        clean_lines.append(line)

    text = "".join(clean_lines)

    # Collapse multiple consecutive redaction labels into one
    text = re.sub(
        r"(" + re.escape(_REDACT_LABEL) + r"\n?){2,}",
        _REDACT_LABEL + "\n",
        text,
    )
    # If a line begins with the redaction label but still has trailing minified data,
    # keep only the label to avoid leaked keys in the remainder.
    text, n = re.subn(
        r"^\s*" + re.escape(_REDACT_LABEL) + r"[ \t]+\S.*$",
        _REDACT_LABEL,
        text,
        flags=re.MULTILINE,
    )
    changes += n

    return text, changes


def scrub_file(path: Path, *, dry_run: bool = False) -> bool:
    """Scrub one file. Returns True if changes were made."""
    original = path.read_text(encoding="utf-8", errors="replace")
    cleaned, changes = scrub(original)
    if changes == 0:
        return False
    if dry_run:
        try:
            label = path.relative_to(ROOT)
        except ValueError:
            label = path
        print(f"  [dry-run] {label}  ({changes} block(s) would be removed)")
        return True
    path.write_text(cleaned, encoding="utf-8")
    try:
        label = path.relative_to(ROOT)
    except ValueError:
        label = path
    print(f"  ✅ {label}  ({changes} block(s) removed)")
    return True


def find_job_files() -> list[Path]:
    jobs_dirs = (ROOT / "applications").glob("*/jobs/")
    files: list[Path] = []
    for d in jobs_dirs:
        files.extend(d.glob("*.md"))
        files.extend(d.glob("*.txt"))
    return sorted(files)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "files", nargs="*", help="Specific files to scrub (default: all jobs/)"
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="Preview changes without writing"
    )
    args = ap.parse_args()

    targets = [Path(f) for f in args.files] if args.files else find_job_files()
    if not targets:
        print("No job capture files found.")
        return

    changed = 0
    for path in targets:
        if scrub_file(path, dry_run=args.dry_run):
            changed += 1

    if changed == 0:
        print("✅ All job files are already clean.")
    elif not args.dry_run:
        print(
            f"\n✅ Scrubbed {changed} file(s). Run `python3 rag/cli.py build` to rebuild index."
        )


if __name__ == "__main__":
    main()
