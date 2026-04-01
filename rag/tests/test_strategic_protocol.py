import csv
from pathlib import Path

import pytest

from rag.cli import _load_tracker_rows, build


def test_tracker_schema_completeness():
    """Verify that the tracker CSV contains all strategic protocol columns."""
    csv_path = Path("applications/job_applications/application_tracker.csv")
    assert csv_path.exists()

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames

    required_strategic_cols = [
        "Referrer Name",
        "Referrer Role",
        "Referral Status",
        "Outreach Cadence (1/3/7/14)",
        "Product Proposal Path",
    ]

    for col in required_strategic_cols:
        assert col in headers, f"Missing strategic column: {col}"


def test_strategic_artifacts_linked():
    """Verify that specific Tier 1 drafts have their strategic artifacts correctly linked."""
    rows = _load_tracker_rows()

    # Check Automattic
    automattic_row = next(
        (
            r
            for r in rows
            if r["Company"] == "Automattic" and r["Role"] == "Mobile Engineer"
        ),
        None,
    )
    assert automattic_row is not None
    assert (
        automattic_row["Product Proposal Path"]
        == "applications/automattic/proposals/2026-04-01_product_proposal.md"
    )
    assert Path(automattic_row["Product Proposal Path"]).exists()

    # Check GitLab
    gitlab_row = next(
        (
            r
            for r in rows
            if r["Company"] == "GitLab"
            and r["Role"] == "Senior Software Engineer Mobile"
        ),
        None,
    )
    assert gitlab_row is not None
    assert (
        gitlab_row["Product Proposal Path"]
        == "applications/gitlab/proposals/2026-04-01_product_proposal.md"
    )
    assert Path(gitlab_row["Product Proposal Path"]).exists()


def test_rag_build_integrity():
    """Ensure the RAG build process succeeds with the expanded schema."""
    # This just ensures no exceptions are raised during the build process
    try:
        build()
    except Exception as e:
        pytest.fail(f"RAG build failed after strategic schema upgrade: {e}")
