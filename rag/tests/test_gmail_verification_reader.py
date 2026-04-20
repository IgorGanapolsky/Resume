"""Unit tests for scripts/gmail_verification_reader.py."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    script_dir = Path(__file__).resolve().parents[2] / "scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    script_path = script_dir / "gmail_verification_reader.py"
    spec = importlib.util.spec_from_file_location(
        "gmail_verification_reader_test_mod", script_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_match_code_extracts_mixed_case_greenhouse_code():
    mod = _load_module()
    body = (
        "Hi Igor, Copy and paste this code into the security code field on "
        "your application: ICoKfMuK After you enter the code, resubmit your "
        "application. 2026 Greenhouse 18 West 18th Street, 11th Floor, New "
        "York NY 10011"
    )
    assert mod._match_code(body) == "ICoKfMuK"


def test_match_code_rejects_all_lowercase_english_words():
    mod = _load_module()
    body = "greeting security resubmit sincerely"
    assert mod._match_code(body) is None


def test_match_code_accepts_digit_only_codes():
    mod = _load_module()
    assert mod._match_code("Your code is 12345678 thanks") == "12345678"


def test_match_code_skips_known_fillers_case_insensitive():
    mod = _load_module()
    body = "Logo GREENHOUSE branded email. Actual code: AbCd3fGh trailing text."
    assert mod._match_code(body) == "AbCd3fGh"


def test_default_from_filters_include_greenhouse_mail_io():
    mod = _load_module()
    filters = {f.lower() for f in mod.DEFAULT_FROM_FILTERS}
    assert "@us.greenhouse-mail.io" in filters
    assert "@greenhouse-mail.io" in filters
    assert "@greenhouse.io" in filters


def test_default_subject_hints_include_security_code():
    mod = _load_module()
    hints = {h.lower() for h in mod.DEFAULT_SUBJECT_HINTS}
    assert "security code" in hints
    assert "verification code" in hints


def test_message_epoch_parses_rfc2822_date():
    mod = _load_module()
    import email
    import email.utils

    msg = email.message.Message()
    msg["Date"] = "Mon, 20 Apr 2026 16:10:59 +0000"
    epoch = mod._message_epoch(msg)
    expected = email.utils.parsedate_to_datetime(msg["Date"]).timestamp()
    assert epoch is not None
    assert abs(epoch - expected) < 1.0


def test_message_epoch_returns_none_for_missing_date():
    mod = _load_module()
    import email

    msg = email.message.Message()
    assert mod._message_epoch(msg) is None


def test_message_epoch_returns_none_for_unparseable_date():
    mod = _load_module()
    import email

    msg = email.message.Message()
    msg["Date"] = "not-a-real-date-string"
    assert mod._message_epoch(msg) is None


def test_min_arrival_epoch_cli_arg_is_accepted():
    """Sanity-check that the CLI parses --min-arrival-epoch without env setup."""
    mod = _load_module()
    import os

    saved_user = os.environ.get("GMAIL_USER")
    saved_pw = os.environ.get("GMAIL_APP_PASSWORD")
    try:
        os.environ.pop("GMAIL_USER", None)
        os.environ.pop("GMAIL_APP_PASSWORD", None)
        # main() returns 2 when env vars are missing — but only AFTER parsing
        # args. If the flag were unknown, argparse would exit with code 2 via
        # SystemExit before env check. Both paths exit 2, so distinguish by
        # ensuring no SystemExit is raised.
        rc = mod.main(["--min-arrival-epoch", "1776010259"])
        assert rc == 2
    finally:
        if saved_user is not None:
            os.environ["GMAIL_USER"] = saved_user
        if saved_pw is not None:
            os.environ["GMAIL_APP_PASSWORD"] = saved_pw


def test_fetch_latest_code_skips_messages_older_than_min_arrival_epoch(monkeypatch):
    """End-to-end-ish: fake an IMAP backend with stale + fresh messages."""
    mod = _load_module()
    import email.message

    stale = email.message.Message()
    stale["From"] = "no-reply@us.greenhouse-mail.io"
    stale["Subject"] = "Your security code"
    stale["Date"] = "Mon, 20 Apr 2026 16:01:55 +0000"
    stale.set_payload(
        "Hi Igor, your code is: STAL3abc. Regards."
    )

    fresh = email.message.Message()
    fresh["From"] = "no-reply@us.greenhouse-mail.io"
    fresh["Subject"] = "Your security code"
    fresh["Date"] = "Mon, 20 Apr 2026 16:10:59 +0000"
    fresh.set_payload(
        "Hi Igor, your code is: FrEsH123. Regards."
    )

    messages = {b"1": stale.as_bytes(), b"2": fresh.as_bytes()}

    class FakeIMAP:
        def __init__(self, host, port):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def login(self, user, password):
            return ("OK", [b"Logged in"])

        def select(self, mailbox):
            return ("OK", [b"2"])

        def search(self, charset, *criteria):
            return ("OK", [b" ".join(messages.keys())])

        def fetch(self, msg_id, spec):
            raw = messages[msg_id]
            return ("OK", [(f"{msg_id.decode()} (RFC822 {{len}})".encode(), raw)])

        def logout(self):
            return ("BYE", [b"OK"])

    monkeypatch.setattr(mod.imaplib, "IMAP4_SSL", FakeIMAP)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)

    import email.utils

    fresh_epoch = email.utils.parsedate_to_datetime(fresh["Date"]).timestamp()

    # Submit happened at 16:05 — stale (16:01) predates it, fresh (16:10) is after.
    submit_epoch = email.utils.parsedate_to_datetime(
        "Mon, 20 Apr 2026 16:05:00 +0000"
    ).timestamp()

    code = mod.fetch_latest_code(
        user="u",
        app_password="p",
        poll_attempts=1,
        poll_interval=0.0,
        min_arrival_epoch=submit_epoch,
    )
    assert code == "FrEsH123"

    # Without the filter, newest (fresh) still wins — sanity check the fixture.
    code_unfiltered = mod.fetch_latest_code(
        user="u",
        app_password="p",
        poll_attempts=1,
        poll_interval=0.0,
    )
    assert code_unfiltered == "FrEsH123"

    # If we set threshold well PAST fresh + skew tolerance, both are skipped.
    future_epoch = fresh_epoch + 3600.0
    assert (
        mod.fetch_latest_code(
            user="u",
            app_password="p",
            poll_attempts=1,
            poll_interval=0.0,
            min_arrival_epoch=future_epoch,
        )
        is None
    )
