#!/usr/bin/env python3
"""Read a recent Greenhouse (or ATS) email verification code from Gmail via IMAP.

Designed for the Greenhouse email-verification gate: after attempting submit,
Greenhouse sends an 8-character alphanumeric code to the applicant's email.
This helper polls Gmail over IMAP, finds the latest verification email, and
returns the code so the submission pipeline can finish the form.

Env vars required:
    GMAIL_USER           - e.g. iganapolsky@gmail.com
    GMAIL_APP_PASSWORD   - Gmail app-password (not the account password)

Stdlib only — no extra deps. Intended to run on the self-hosted CI runner.
"""

from __future__ import annotations

import argparse
import email
import imaplib
import os
import re
import sys
import time
from email.header import decode_header, make_header
from typing import Iterable, Optional

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993

DEFAULT_FROM_FILTERS = (
    "no-reply@greenhouse.io",
    "donotreply@greenhouse.io",
    "notifications@greenhouse.io",
    "@greenhouse.io",
)
DEFAULT_SUBJECT_HINTS = (
    "verification code",
    "verify your email",
    "confirm your email",
    "your code",
)
CODE_RE = re.compile(r"\b([A-Z0-9]{8})\b")


def _decode(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _extract_text_parts(msg: email.message.Message) -> str:
    chunks = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype not in ("text/plain", "text/html"):
                continue
            try:
                payload = part.get_payload(decode=True) or b""
                chunks.append(
                    payload.decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
                )
            except Exception:  # nosec B112 - skip unreadable part; other parts may decode
                continue
    else:
        try:
            payload = msg.get_payload(decode=True) or b""
            chunks.append(
                payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
            )
        except Exception:  # nosec B110 - unreadable single-part message; return ""
            pass
    return "\n".join(chunks)


def _match_code(text: str) -> Optional[str]:
    candidates = CODE_RE.findall(text or "")
    filler = {"GREENHOUSE", "APPLICANT", "ABCDEFGH"}
    for cand in candidates:
        if cand.isdigit():
            return cand
        if cand in filler:
            continue
        return cand
    return None


def fetch_latest_code(
    user: str,
    app_password: str,
    from_filters: Iterable[str] = DEFAULT_FROM_FILTERS,
    subject_hints: Iterable[str] = DEFAULT_SUBJECT_HINTS,
    lookback_minutes: int = 15,
    poll_attempts: int = 6,
    poll_interval: float = 5.0,
) -> Optional[str]:
    """Poll Gmail for a verification code. Returns the code string or None."""
    last_error: Optional[BaseException] = None
    for _attempt in range(poll_attempts):
        try:
            with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT) as mail:
                mail.login(user, app_password)
                mail.select("INBOX")
                since = time.strftime(
                    "%d-%b-%Y",
                    time.gmtime(time.time() - lookback_minutes * 60),
                )
                search_criteria = f'(SINCE "{since}")'
                typ, data = mail.search(None, search_criteria)
                if typ != "OK" or not data or not data[0]:
                    mail.logout()
                    raise RuntimeError("no candidate messages")
                ids = data[0].split()
                for msg_id in reversed(ids):
                    typ, msg_data = mail.fetch(msg_id, "(RFC822)")
                    if typ != "OK" or not msg_data or not msg_data[0]:
                        continue
                    raw = msg_data[0][1]
                    msg = email.message_from_bytes(raw)
                    sender = _decode(msg.get("From", "")).lower()
                    subject = _decode(msg.get("Subject", "")).lower()
                    if not any(f.lower() in sender for f in from_filters):
                        continue
                    if subject_hints and not any(
                        h.lower() in subject for h in subject_hints
                    ):
                        continue
                    body = _extract_text_parts(msg)
                    code = _match_code(subject + "\n" + body)
                    if code:
                        return code
                mail.logout()
        except Exception as exc:
            last_error = exc
        time.sleep(poll_interval)
    if last_error is not None:
        print(f"gmail_verification_reader: {last_error}", file=sys.stderr)
    return None


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lookback-minutes", type=int, default=15)
    parser.add_argument("--poll-attempts", type=int, default=6)
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument(
        "--from",
        dest="from_filter",
        action="append",
        default=None,
        help="Repeatable. Override default FROM filters.",
    )
    parser.add_argument(
        "--subject-hint",
        action="append",
        default=None,
        help="Repeatable. Override default subject hints.",
    )
    args = parser.parse_args(argv)
    user = os.environ.get("GMAIL_USER", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    if not user or not password:
        print(
            "GMAIL_USER and GMAIL_APP_PASSWORD env vars are required",
            file=sys.stderr,
        )
        return 2
    code = fetch_latest_code(
        user=user,
        app_password=password,
        from_filters=tuple(args.from_filter) if args.from_filter else DEFAULT_FROM_FILTERS,
        subject_hints=tuple(args.subject_hint) if args.subject_hint else DEFAULT_SUBJECT_HINTS,
        lookback_minutes=args.lookback_minutes,
        poll_attempts=args.poll_attempts,
        poll_interval=args.poll_interval,
    )
    if not code:
        return 1
    print(code)
    return 0


if __name__ == "__main__":
    sys.exit(main())
