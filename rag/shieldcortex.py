import re
from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class Finding:
    kind: str
    start: int
    end: int
    excerpt: str


@dataclass(frozen=True)
class GateResult:
    action: str
    text: str
    findings: List[Finding]


_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b")

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
            findings.append(
                Finding("dob", dm.start(), dm.end(), text[dm.start() : dm.end()])
            )

    return findings


def redact(text: str) -> str:
    # Redact SSN always.
    text = _SSN_RE.sub("[REDACTED_SSN]", text)
    # Medium-risk PII is redacted and allowed through the gate.
    text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    text = _PHONE_RE.sub("[REDACTED_PHONE]", text)

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


def gate_text(text: str, *, context: str = "") -> GateResult:
    """ShieldCortex-style gate API used by ingestion/logging paths.

    Actions:
    - allow:      no high-risk findings
    - quarantine: redaction changed text (currently informational)
    - block:      high-risk PII detected (ssn/dob)
    """
    findings = scan_pii(text)
    if findings:
        kinds = ", ".join(sorted({f.kind for f in findings}))
        where = f" ({context})" if context else ""
        raise ValueError(
            f"High-risk PII detected: {kinds}{where}; refusing to ingest/log."
        )

    redacted = redact(text)
    action = "allow"
    if redacted != text:
        action = "quarantine"
    return GateResult(action=action, text=redacted, findings=[])
