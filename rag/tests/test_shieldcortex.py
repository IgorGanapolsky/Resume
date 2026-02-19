"""Tests for shieldcortex.py — PII detection and redaction."""

import pytest
from shieldcortex import assert_no_high_risk_pii, gate_text, redact, scan_pii


class TestScanPii:
    def test_detects_ssn(self):
        findings = scan_pii("My SSN is 123-45-6789 and nothing else.")
        assert len(findings) == 1
        assert findings[0].kind == "ssn"
        assert findings[0].excerpt == "123-45-6789"

    def test_no_ssn_in_clean_text(self):
        assert scan_pii("Applied on 2026-02-18 for the role.") == []

    def test_detects_dob_with_context(self):
        text = "DOB: 01/15/1985 please fill in"
        findings = scan_pii(text)
        dob_findings = [f for f in findings if f.kind == "dob"]
        assert len(dob_findings) == 1

    def test_ignores_date_without_dob_context(self):
        text = "Applied on 02/18/2026 for the role."
        findings = scan_pii(text)
        assert not any(f.kind == "dob" for f in findings)

    def test_detects_multiple_ssns(self):
        text = "First: 111-22-3333, second: 444-55-6666"
        findings = scan_pii(text)
        ssns = [f for f in findings if f.kind == "ssn"]
        assert len(ssns) == 2

    def test_partial_ssn_not_flagged(self):
        assert scan_pii("code 123-45") == []
        assert scan_pii("ref 12-345-678") == []


class TestRedact:
    def test_redacts_ssn(self):
        result = redact("SSN: 123-45-6789 in text")
        assert "123-45-6789" not in result
        assert "[REDACTED_SSN]" in result

    def test_redacts_dob_with_context(self):
        result = redact("date of birth 03/22/1990 provided")
        assert "03/22/1990" not in result
        assert "[REDACTED_DOB]" in result

    def test_preserves_application_dates(self):
        result = redact("Applied on 02/18/2026 for the role.")
        assert "02/18/2026" in result

    def test_clean_text_unchanged(self):
        text = "Senior ML Engineer at Acme, applied via Ashby."
        assert redact(text) == text

    def test_multiple_ssn_redacted(self):
        result = redact("A: 111-22-3333 B: 444-55-6666")
        assert "111-22-3333" not in result
        assert "444-55-6666" not in result
        assert result.count("[REDACTED_SSN]") == 2


class TestAssertNoHighRiskPii:
    def test_clean_text_passes(self):
        assert_no_high_risk_pii("Normal job application text, no PII here.")

    def test_ssn_raises(self):
        with pytest.raises(ValueError, match="High-risk PII detected: ssn"):
            assert_no_high_risk_pii("My SSN is 999-88-7777.")

    def test_dob_with_context_raises(self):
        with pytest.raises(ValueError, match="High-risk PII detected: dob"):
            assert_no_high_risk_pii("DOB 04/01/1982 on record")

    def test_context_included_in_error(self):
        with pytest.raises(ValueError, match="some_file.txt"):
            assert_no_high_risk_pii("SSN: 000-11-2222", context="some_file.txt")

    def test_error_message_has_colon_separator(self):
        """Regression test: error message must include ': ' after 'detected'."""
        with pytest.raises(ValueError) as exc_info:
            assert_no_high_risk_pii("SSN: 000-11-2222")
        assert "detected: " in str(exc_info.value)


class TestGateText:
    def test_allow_for_clean_text(self):
        result = gate_text("Applied for role with no PII.", context="unit")
        assert result.action == "allow"
        assert result.text == "Applied for role with no PII."

    def test_quarantine_for_redacted_text(self):
        result = gate_text("contact me at user@example.com", context="unit")
        assert result.action == "quarantine"
        assert "[REDACTED_EMAIL]" in result.text

    def test_block_for_high_risk_pii(self):
        ssn_like = "123-45-" + "6789"
        with pytest.raises(ValueError, match="High-risk PII detected"):
            gate_text(f"SSN: {ssn_like}", context="unit")
