"""Tests for role-aware resume tailoring in scripts/ralph_loop_ci.py."""

from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

import pytest


def _load_ralph_loop_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "ralph_loop_ci.py"
    spec = importlib.util.spec_from_file_location("ralph_loop_ci_test_mod", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


@pytest.fixture
def loop_mod():
    return _load_ralph_loop_module()


def test_classify_role_detects_fde_signals(loop_mod):
    job = {
        "company": "ElevenLabs",
        "title": "Forward Deployed Engineer - Software Engineer",
        "location": "Remote",
        "job_type": "Full time",
        "tags": "ai;api;integration",
        "description": (
            "Collaborate with customer engineers and executives. "
            "Proficiency in Python and API integration. "
            "Build voice and audio workflows."
        ),
        "url": "https://jobs.ashbyhq.com/elevenlabs/abc123",
    }
    profile = loop_mod.classify_role(job)

    assert profile.is_relevant is True
    assert profile.track == "fde"
    assert "customer-integration" in profile.signals
    assert "python" in profile.signals
    assert "voice-audio" in profile.signals


def test_classify_role_filters_non_technical_roles(loop_mod):
    job = {
        "company": "Example Corp",
        "title": "Enterprise Account Executive",
        "location": "Remote",
        "job_type": "Full time",
        "tags": "sales;enterprise",
        "description": "Own pipeline and quota for enterprise accounts.",
        "url": "https://jobs.example.com/roles/ae",
    }
    profile = loop_mod.classify_role(job)
    assert profile.is_relevant is False


def test_classify_role_filters_content_manager_false_positive(loop_mod):
    job = {
        "company": "NMI",
        "title": "Content Manager",
        "location": "Remote, US",
        "job_type": "Full time",
        "tags": "embedded;technical;writer;support;api",
        "description": "Own content operations and support documentation.",
        "url": "https://example.com/jobs/content-manager",
    }
    profile = loop_mod.classify_role(job)
    assert profile.is_relevant is False
    assert profile.track in {"general", "fde"}


def test_tailor_resume_html_for_fde_profile(loop_mod):
    base_resume_path = (
        Path(__file__).resolve().parents[2]
        / "resumes"
        / "Igor_Ganapolsky_AI_Systems_Engineer_2026-02-17.html"
    )
    base_html = base_resume_path.read_text(encoding="utf-8")
    profile = loop_mod.RoleProfile(
        track="fde",
        score=5,
        signals=["customer-integration", "python"],
        is_relevant=True,
    )

    tailored = loop_mod.tailor_resume_html(base_html, profile)

    assert "Forward-Deployed AI/Software Engineer" in tailored
    assert "<strong>FORWARD-DEPLOYED COMPETENCIES</strong>" in tailored
    assert "customer service load by <strong>35%</strong>" in tailored
    assert "reduced support volume <strong>40%</strong>" in tailored


def test_create_artifacts_writes_tailored_resume_and_requirements(
    loop_mod, tmp_path, monkeypatch
):
    root = tmp_path
    applications_dir = root / "applications"
    resumes_dir = root / "resumes"
    applications_dir.mkdir(parents=True, exist_ok=True)
    resumes_dir.mkdir(parents=True, exist_ok=True)

    base_resume_src = (
        Path(__file__).resolve().parents[2]
        / "resumes"
        / "Igor_Ganapolsky_AI_Systems_Engineer_2026-02-17.html"
    )
    base_resume = resumes_dir / "base_resume.html"
    base_resume.write_text(
        base_resume_src.read_text(encoding="utf-8"), encoding="utf-8"
    )

    monkeypatch.setattr(loop_mod, "ROOT", root)
    monkeypatch.setattr(loop_mod, "APPLICATIONS_DIR", applications_dir)
    monkeypatch.setattr(loop_mod, "BASE_RESUME", base_resume)

    job = {
        "company": "ElevenLabs",
        "title": "Forward Deployed Engineer - Software Engineer",
        "location": "Remote",
        "job_type": "Full time",
        "salary": "",
        "source": "ashby",
        "tags": "ai;integration",
        "description": (
            "Collaborate daily with customer engineers and executives. "
            "Proficiency in Python and APIs integration."
        ),
        "url": "https://jobs.ashbyhq.com/elevenlabs/abc123",
    }
    profile = loop_mod.classify_role(job)
    artifacts = loop_mod.create_artifacts(job, "2026-02-19", profile)

    job_md = root / artifacts["job_md"]
    assert job_md.exists()
    job_md_text = job_md.read_text(encoding="utf-8")
    assert "## Key Requirements" in job_md_text
    assert "Python proficiency for integration-heavy services." in job_md_text

    company_slug = loop_mod._slug(job["company"])
    role_slug = loop_mod._slug(job["title"])[:64]
    resume_html = (
        applications_dir
        / company_slug
        / "tailored_resumes"
        / f"2026-02-19_{company_slug}_{role_slug}.html"
    )
    assert resume_html.exists()
    resume_text = resume_html.read_text(encoding="utf-8")
    assert "Forward-Deployed AI/Software Engineer" in resume_text

    cover_path = (
        applications_dir
        / company_slug
        / "cover_letters"
        / f"{artifacts['cover_stem']}.md"
    )
    assert cover_path.exists()
    cover_text = cover_path.read_text(encoding="utf-8")
    assert "customer-facing, integration-heavy delivery work" in cover_text


def test_main_dry_run_does_not_create_artifacts(loop_mod, tmp_path, monkeypatch):
    tracker_csv = tmp_path / "application_tracker.csv"
    fieldnames = [
        "Company",
        "Role",
        "Location",
        "Salary Range",
        "Status",
        "Date Applied",
        "Follow Up Date",
        "Response",
        "Interview Stage",
        "Days To Response",
        "Response Type",
        "Cover Letter Used",
        "What Worked",
        "Tags",
        "Notes",
        "Career Page URL",
    ]
    with tracker_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

    monkeypatch.setattr(loop_mod, "ROOT", tmp_path)
    monkeypatch.setattr(loop_mod, "TRACKER_CSV", tracker_csv)
    monkeypatch.setattr(loop_mod, "APPLICATIONS_DIR", tmp_path / "applications")

    job = {
        "company": "ElevenLabs",
        "title": "Forward Deployed Engineer - Software Engineer",
        "location": "Remote",
        "salary": "",
        "job_type": "Full time",
        "source": "ashby",
        "url": "https://jobs.ashbyhq.com/elevenlabs/abc123",
        "tags": "ai;integration",
        "description": "Customer-facing API integrations with Python.",
    }
    monkeypatch.setattr(loop_mod, "discover_remotive", lambda: [job])
    monkeypatch.setattr(loop_mod, "discover_remoteok", lambda: [])

    called = {"value": False}

    def _should_not_run(*args, **kwargs):
        called["value"] = True
        raise AssertionError("create_artifacts must not run during --dry-run")

    monkeypatch.setattr(loop_mod, "create_artifacts", _should_not_run)
    monkeypatch.setattr(
        sys, "argv", ["ralph_loop_ci.py", "--dry-run", "--max-new-jobs", "1"]
    )

    loop_mod.main()

    assert called["value"] is False
    assert not (tmp_path / "applications" / "elevenlabs").exists()
