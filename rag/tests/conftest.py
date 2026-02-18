"""Shared pytest fixtures for RAG tests."""

import csv
import sys
from pathlib import Path

import pytest

# Ensure the rag/ package is importable from any working directory
RAG_DIR = Path(__file__).resolve().parents[1]
if str(RAG_DIR) not in sys.path:
    sys.path.insert(0, str(RAG_DIR))


SAMPLE_ROWS = [
    {
        "Company": "Acme AI",
        "Role": "Senior ML Engineer",
        "Location": "Remote",
        "Salary Range": "$150k-$180k",
        "Status": "Applied",
        "Date Applied": "2026-02-01",
        "Follow Up Date": "2026-02-08",
        "Response": "",
        "Interview Stage": "Initial",
        "Days To Response": "",
        "Response Type": "",
        "Cover Letter Used": "acme_cover",
        "What Worked": "",
        "Tags": "ai;remote;ml",
        "Notes": "Strong ML focus. Applied via Ashby.",
        "Career Page URL": "https://jobs.ashbyhq.com/acmeco/abc123",
    },
    {
        "Company": "Beta Corp",
        "Role": "React Native Developer",
        "Location": "Miami FL",
        "Salary Range": "$120k-$150k",
        "Status": "Draft",
        "Date Applied": "",
        "Follow Up Date": "",
        "Response": "",
        "Interview Stage": "Initial",
        "Days To Response": "",
        "Response Type": "",
        "Cover Letter Used": "",
        "What Worked": "",
        "Tags": "react-native;local;mobile",
        "Notes": "Local startup. Expo SDK.",
        "Career Page URL": "https://jobs.lever.co/betacorp/xyz",
    },
    {
        "Company": "Gamma Infra",
        "Role": "Platform Engineer",
        "Location": "Remote",
        "Salary Range": "$130k-$160k",
        "Status": "Blocked",
        "Date Applied": "2026-02-10",
        "Follow Up Date": "",
        "Response": "Submit blocked",
        "Interview Stage": "Initial",
        "Days To Response": "",
        "Response Type": "",
        "Cover Letter Used": "",
        "What Worked": "",
        "Tags": "ai;infra;kubernetes",
        "Notes": "Spam flagged on Ashby.",
        "Career Page URL": "https://jobs.ashbyhq.com/gamma/def456",
    },
]


@pytest.fixture
def tracker_csv(tmp_path: Path) -> Path:
    """Write sample rows to a temp tracker CSV and return its path."""
    path = tmp_path / "application_tracker.csv"
    fieldnames = list(SAMPLE_ROWS[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(SAMPLE_ROWS)
    return path


@pytest.fixture
def isolated_cli(tmp_path: Path, tracker_csv: Path, monkeypatch):
    """Patch all module-level path constants in cli to use tmp_path."""
    import cli as cli_mod

    app_dir = tmp_path / "applications"
    app_dir.mkdir()

    monkeypatch.setattr(cli_mod, "ROOT", tmp_path)
    monkeypatch.setattr(cli_mod, "RAG_DIR", tmp_path / "rag")
    monkeypatch.setattr(cli_mod, "DATA_DIR", tmp_path / "rag" / "data")
    monkeypatch.setattr(cli_mod, "LOG_DIR", tmp_path / "rag" / "logs")
    monkeypatch.setattr(cli_mod, "LANCEDB_DIR", tmp_path / "rag" / "lancedb")
    monkeypatch.setattr(cli_mod, "TRACKER_CSV", tracker_csv)
    monkeypatch.setattr(cli_mod, "APPLICATIONS_DIR", app_dir)
    monkeypatch.setattr(cli_mod, "ARMS_JSON", tmp_path / "rag" / "data" / "arms.json")

    return cli_mod
