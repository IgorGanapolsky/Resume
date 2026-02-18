"""Tests for memalign.py — normalization and embedding helpers."""

import pytest
from memalign import (
    infer_application_method,
    normalize_row,
    normalize_status,
    parse_tags,
    slug,
    stable_id,
)


class TestSlug:
    def test_basic(self):
        assert slug("Acme AI") == "acme-ai"

    def test_special_chars(self):
        assert slug("D-Wave Quantum!") == "d-wave-quantum"

    def test_empty(self):
        assert slug("") == "unknown"

    def test_none_like(self):
        assert slug("   ") == "unknown"

    def test_already_slug(self):
        assert slug("acme-corp") == "acme-corp"

    def test_leading_trailing_hyphens(self):
        result = slug("--hello--")
        assert result == "hello"


class TestStableId:
    def test_deterministic(self):
        a = stable_id("Acme", "Engineer", "https://example.com/job/1")
        b = stable_id("Acme", "Engineer", "https://example.com/job/1")
        assert a == b

    def test_different_urls_differ(self):
        a = stable_id("Acme", "Engineer", "https://example.com/job/1")
        b = stable_id("Acme", "Engineer", "https://example.com/job/2")
        assert a != b

    def test_format(self):
        result = stable_id("Acme Corp", "ML Engineer", "https://x.com/jobs/1")
        assert result.startswith("acme-corp__ml-engineer__")
        parts = result.split("__")
        assert len(parts) == 3
        assert len(parts[2]) == 10  # SHA256 prefix


class TestParseTags:
    def test_basic(self):
        assert parse_tags("ai;remote;ml") == ["ai", "remote", "ml"]

    def test_empty_string(self):
        assert parse_tags("") == []

    def test_none_like(self):
        assert parse_tags(None) == []  # type: ignore[arg-type]

    def test_strips_whitespace(self):
        assert parse_tags("ai ; remote ; ml") == ["ai", "remote", "ml"]

    def test_filters_empty_segments(self):
        assert parse_tags("ai;;remote") == ["ai", "remote"]


class TestNormalizeStatus:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("applied", "Applied"),
            ("APPLIED", "Applied"),
            ("draft", "Draft"),
            ("in progress", "Draft"),
            ("closed", "Closed"),
            ("blocked", "Blocked"),
            ("rejected", "Rejected"),
            ("offer", "Offer"),
            ("Unknown Status", "Unknown Status"),  # passthrough
            ("", "Draft"),  # default
        ],
    )
    def test_mapping(self, raw, expected):
        assert normalize_status(raw) == expected


class TestInferApplicationMethod:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://jobs.ashbyhq.com/acme/123", "ashby"),
            ("https://job-boards.greenhouse.io/acme/jobs/456", "greenhouse"),
            ("https://jobs.lever.co/acme/789", "lever"),
            ("https://wellfound.com/company/acme/jobs/1", "wellfound"),
            ("https://angel.co/company/acme/jobs", "wellfound"),
            ("https://work.mercor.com/jobs/list_abc/software-engineer", "mercor"),
            ("https://myworkdayjobs.com/en-US/acme/job/engineer", "workday"),
            ("https://acme.com/careers/openings", "direct"),
            ("", "direct"),
        ],
    )
    def test_patterns(self, url, expected):
        assert infer_application_method(url) == expected

    def test_case_insensitive(self):
        assert infer_application_method("HTTPS://JOBS.ASHBYHQ.COM/ACME/1") == "ashby"


class TestNormalizeRow:
    def test_adds_app_id(self):
        row = {
            "Company": "Acme",
            "Role": "Engineer",
            "Career Page URL": "https://jobs.ashbyhq.com/acme/1",
            "Tags": "ai;remote",
            "Status": "applied",
        }
        result = normalize_row(row)
        assert "app_id" in result
        assert result["app_id"].startswith("acme__engineer__")

    def test_normalizes_tags(self):
        row = {
            "Company": "X",
            "Role": "Y",
            "Career Page URL": "",
            "Tags": "a;b;c",
            "Status": "",
        }
        result = normalize_row(row)
        assert result["Tags"] == ["a", "b", "c"]

    def test_normalizes_status(self):
        row = {
            "Company": "X",
            "Role": "Y",
            "Career Page URL": "",
            "Tags": "",
            "Status": "applied",
        }
        result = normalize_row(row)
        assert result["Status"] == "Applied"

    def test_adds_application_method(self):
        row = {
            "Company": "X",
            "Role": "Y",
            "Career Page URL": "https://jobs.lever.co/x/1",
            "Tags": "",
            "Status": "",
        }
        result = normalize_row(row)
        assert result["application_method"] == "lever"

    def test_stable_id_is_deterministic(self):
        row = {
            "Company": "Acme",
            "Role": "SWE",
            "Career Page URL": "https://x.com/j/1",
            "Tags": "",
            "Status": "",
        }
        r1 = normalize_row(row)
        r2 = normalize_row(row)
        assert r1["app_id"] == r2["app_id"]
