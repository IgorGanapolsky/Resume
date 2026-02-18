import re
from dataclasses import dataclass
from typing import Iterable, List, Tuple


@dataclass(frozen=True)
class Finding:
    kind: str
    start: int
    end: int
    excerpt: str


_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

# We only treat dates as DOB when there is nearby context. This avoids wiping out
# application dates and other normal timeline info.
_DOB_CONTEXT_RE = re.compile(r"(?i)\b(dob|date of birth)\b")
_DATE_RE = re.compile(r"\b(0[1-9]|1[0-2])/(0[1-9]|[12]\d|3[01])/(19\d{2}|20\d{2})\b")


def scan_pii(text: str) -> List[Finding]:
    findings: List[Finding] = []

    for m in _SSN_RE.finditer(text):
        findings.append(Finding("ssn", m.start(), m.end(), text[m.start() : m.end()]))

    # DOB: require context close to the date.
    for dm in _DATE_RE.finditer(text):
        window_start = max(0, dm.start() - 40)
        window_end = min(len(text), dm.end() + 40)
        window = text[window_start:window_end]
        if _DOB_CONTEXT_RE.search(window):
            findings.append(Finding("dob", dm.start(), dm.end(), text[dm.start() : dm.end()]))

    return findings


def redact(text: str) -> str:
    # Redact SSN always.
    text = _SSN_RE.sub("[REDACTED_SSN]", text)

    # Redact DOB only when context indicates it's a DOB.
    # Do this in a second pass to preserve indices of SSN replacements.
    out = []
    last = 0
    for dm in _DATE_RE.finditer(text):
        window_start = max(0, dm.start() - 40)
        window_end = min(len(text), dm.end() + 40)
        window = text[window_start:window_end]
        if _DOB_CONTEXT_RE.search(window):
            out.append(text[last : dm.start()])
            out.append("[REDACTED_DOB]")
            last = dm.end()
    out.append(text[last:])
    return "".join(out)


def assert_no_high_risk_pii(text: str, *, context: str = "") -> None:
    findings = scan_pii(text)
    if not findings:
        return
    kinds = ", ".join(sorted({f.kind for f in findings}))
    where = f" ({context})" if context else ""
    raise ValueError(f"High-risk PII detected: {kinds}{where}; refusing to ingest/log.")


