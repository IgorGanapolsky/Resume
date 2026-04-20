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
