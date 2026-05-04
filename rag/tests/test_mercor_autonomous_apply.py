"""Tests for the guarded Mercor autonomous apply lane."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    script_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "mercor_autonomous_apply.py"
    )
    spec = importlib.util.spec_from_file_location(
        "mercor_autonomous_apply_test_mod", script_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def test_detect_status_blocks_login_and_dob():
    mod = _load_module()

    assert mod.detect_status("Sign in to existing Mercor account") == "not_logged_in"
    assert mod.detect_status("Please enter your date of birth") == "dob_required"
    assert (
        mod.detect_status(
            "Welcome to the Software Engineering Expert CU Assessment. "
            "Part 1 Knowledge Check. Part 2 Code Sample. "
            "No AI tools. All work must be entirely your own."
        )
        == "manual_assessment_no_ai_required"
    )


def test_detect_status_recognizes_submitted_and_closed():
    mod = _load_module()

    assert mod.detect_status("Your application has been submitted") == "submitted"
    assert (
        mod.detect_status("This listing is not accepting applications currently")
        == "closed"
    )


def test_choose_resume_prefers_newest_pdf(tmp_path):
    mod = _load_module()
    old_docx = tmp_path / "old.docx"
    new_pdf = tmp_path / "new.pdf"
    old_docx.write_bytes(b"docx")
    new_pdf.write_bytes(b"pdf")

    assert mod.choose_resume(tmp_path) == new_pdf
