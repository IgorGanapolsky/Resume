"""Tests for role-aware resume tailoring in scripts/ralph_loop_ci.py."""

from __future__ import annotations

import csv
import importlib.util
import sys
import zipfile
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


def test_classify_role_accepts_member_of_technical_staff(loop_mod):
    job = {
        "company": "Inferact",
        "title": "Member of Technical Staff, Exceptional Generalist",
        "location": "Remote, US",
        "job_type": "Full time",
        "tags": "ai;python;infrastructure",
        "description": "Build production agent infrastructure and platform systems.",
        "url": "https://jobs.ashbyhq.com/inferact/abc123",
    }
    profile = loop_mod.classify_role(job)
    assert profile.is_relevant is True
    assert profile.track == "general"


def test_infer_remote_profile_remote_feed(loop_mod):
    job = {
        "company": "Exadel",
        "title": "Senior Data Engineer",
        "location": "Remote, US",
        "job_type": "Full time",
        "tags": "python;data",
        "description": "Distributed team across US time zones.",
        "url": "https://remoteOK.com/remote-jobs/remote-senior-data-engineer-exadel-1130396",
    }
    policy, score, evidence = loop_mod.infer_remote_profile(job)
    assert policy == "remote"
    assert score >= 85
    assert "remote_feed_source" in evidence


def test_infer_submission_lane(loop_mod):
    assert loop_mod.infer_submission_lane("ashby") == "ci_auto"
    assert loop_mod.infer_submission_lane("greenhouse") == "ci_auto"
    assert loop_mod.infer_submission_lane("direct") == "manual"


def test_infer_method_rejects_ashby_accommodation_form(loop_mod):
    assert (
        loop_mod.infer_method(
            "https://jobs.ashbyhq.com/deel/form/accommodation-requests"
        )
        == "direct"
    )


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
        philosophy="Integration is a social problem",
        distinctive_achievements=[
            "Architected a self-healing CI pipeline",
            "shipping small experiments weekly",
        ],
    )

    tailored = loop_mod.tailor_resume_html(base_html, profile)

    assert "Forward-Deployed AI/Software Engineer" in tailored
    assert "<strong>FORWARD-DEPLOYED COMPETENCIES</strong>" in tailored
    assert "Architected a self-healing CI pipeline" in tailored
    assert "shipping small experiments weekly" in tailored


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
    resume_docx = resume_html.with_suffix(".docx")
    assert resume_docx.exists()
    with zipfile.ZipFile(resume_docx) as zf:
        assert "word/document.xml" in zf.namelist()

    cover_path = (
        applications_dir
        / company_slug
        / "cover_letters"
        / f"{artifacts['cover_stem']}.md"
    )
    assert cover_path.exists()
    cover_text = cover_path.read_text(encoding="utf-8")
    # Role and company are named; the full ElevenLabs FDE title appears once.
    assert "ElevenLabs" in cover_text
    assert "Forward Deployed Engineer - Software Engineer" in cover_text
    # Old template phrases must never ship again.
    lowered = cover_text.lower()
    for phrase in loop_mod.AI_TELL_PHRASES:
        assert phrase not in lowered, f"AI-tell phrase leaked: {phrase!r}"


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
    monkeypatch.setattr(loop_mod, "discover_company_boards", lambda: [])

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


def test_main_defaults_to_auto_submit_discovery_only(loop_mod, tmp_path, monkeypatch):
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
    monkeypatch.setattr(
        loop_mod,
        "discover_remotive",
        lambda: [
            {
                "company": "OpenEvidence",
                "title": "Software Engineer",
                "location": "Remote",
                "salary": "",
                "job_type": "Full time",
                "source": "ashby",
                "url": "https://jobs.ashbyhq.com/openevidence/abc123",
                "listing_url": "https://remotive.com/jobs/1",
                "tags": "ai;python",
                "description": "Python API integrations and infrastructure.",
            },
            {
                "company": "FeedOnly",
                "title": "Software Engineer",
                "location": "Remote",
                "salary": "",
                "job_type": "Full time",
                "source": "remoteok",
                "url": "https://remoteok.com/remote-jobs/feed-only-1",
                "listing_url": "https://remoteok.com/remote-jobs/feed-only-1",
                "tags": "ai;python",
                "description": "Python platform engineering role.",
            },
        ],
    )
    monkeypatch.setattr(loop_mod, "discover_remoteok", lambda: [])
    monkeypatch.setattr(loop_mod, "discover_company_boards", lambda: [])
    monkeypatch.setattr(sys, "argv", ["ralph_loop_ci.py", "--max-new-jobs", "5"])

    loop_mod.main()

    with tracker_csv.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["Company"] == "OpenEvidence"
    assert rows[0]["Submission Lane"] == "ci_auto"


def test_main_can_admit_manual_discovery_with_explicit_quota(
    loop_mod, tmp_path, monkeypatch
):
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
    monkeypatch.setattr(
        loop_mod,
        "discover_remotive",
        lambda: [
            {
                "company": "OpenEvidence",
                "title": "Software Engineer",
                "location": "Remote",
                "salary": "",
                "job_type": "Full time",
                "source": "ashby",
                "url": "https://jobs.ashbyhq.com/openevidence/abc123",
                "listing_url": "https://remotive.com/jobs/1",
                "tags": "ai;python",
                "description": "Python API integrations and infrastructure.",
            },
            {
                "company": "FeedOnly",
                "title": "Software Engineer",
                "location": "Remote",
                "salary": "",
                "job_type": "Full time",
                "source": "remoteok",
                "url": "https://remoteok.com/remote-jobs/feed-only-1",
                "listing_url": "https://remoteok.com/remote-jobs/feed-only-1",
                "tags": "ai;python",
                "description": "Python platform engineering role.",
            },
        ],
    )
    monkeypatch.setattr(loop_mod, "discover_remoteok", lambda: [])
    monkeypatch.setattr(loop_mod, "discover_company_boards", lambda: [])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ralph_loop_ci.py",
            "--max-new-jobs",
            "5",
            "--max-manual-jobs",
            "1",
        ],
    )

    loop_mod.main()

    with tracker_csv.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    assert [row["Submission Lane"] for row in rows] == ["ci_auto", "manual"]


def test_load_company_boards_reads_shipped_config(loop_mod):
    boards = loop_mod._load_company_boards()
    assert len(boards) >= 20, f"expected >=20 configured boards, got {len(boards)}"
    companies = {b["company"].lower() for b in boards}
    # Core targets the user named explicitly.
    for required in {"openai", "xai", "anthropic"}:
        assert required in companies, f"{required} missing from company_boards.json"
    # Every entry must be greenhouse or ashby (auto-submit lanes).
    for b in boards:
        assert b["ats"] in {"greenhouse", "ashby"}
        assert b["slug"]


def test_discover_greenhouse_board_canonicalizes_url(loop_mod, monkeypatch):
    payload = {
        "jobs": [
            {
                "id": 99887766,
                "title": "Senior Software Engineer",
                "absolute_url": "https://databricks.com/company/careers/open-positions/job?gh_jid=99887766",
                "location": {"name": "Remote — United States"},
                "content": "&lt;p&gt;Python distributed systems engineer.&lt;/p&gt;",
                "departments": [{"name": "Engineering Platform"}],
            }
        ]
    }
    monkeypatch.setattr(loop_mod, "_fetch_json", lambda _u: payload)
    jobs = list(loop_mod.discover_greenhouse_board("databricks", "Databricks"))
    assert len(jobs) == 1
    job = jobs[0]
    assert job["company"] == "Databricks"
    assert job["title"] == "Senior Software Engineer"
    # Canonical URL is required so the Greenhouse adapter can render the form.
    assert job["url"] == "https://job-boards.greenhouse.io/databricks/jobs/99887766"
    assert job["location"] == "Remote — United States"
    assert "Python" in job["description"]
    assert "engineering-platform" in job["tags"]


def test_discover_greenhouse_board_skips_on_fetch_error(loop_mod, monkeypatch):
    def _boom(_u):
        raise RuntimeError("network down")

    monkeypatch.setattr(loop_mod, "_fetch_json", _boom)
    jobs = list(loop_mod.discover_greenhouse_board("anthropic", "Anthropic"))
    assert jobs == []


def test_discover_ashby_board_extracts_jobs(loop_mod, monkeypatch):
    payload = {
        "jobs": [
            {
                "id": "abc-123",
                "title": "Member of Technical Staff",
                "location": "Remote",
                "employmentType": "FullTime",
                "department": "Research",
                "team": "Applied AI",
                "jobUrl": "https://jobs.ashbyhq.com/openai/abc-123",
                "applyUrl": "https://jobs.ashbyhq.com/openai/abc-123/application",
                "descriptionPlain": "Build production agent infrastructure with Python.",
                "descriptionHtml": "<p>Build production agent infrastructure with Python.</p>",
                "isListed": True,
            },
            {
                "id": "hidden-1",
                "title": "Unlisted Role",
                "jobUrl": "https://jobs.ashbyhq.com/openai/hidden-1",
                "isListed": False,
            },
        ]
    }
    monkeypatch.setattr(loop_mod, "_fetch_json", lambda _u: payload)
    jobs = list(loop_mod.discover_ashby_board("openai", "OpenAI"))
    assert len(jobs) == 1
    job = jobs[0]
    assert job["company"] == "OpenAI"
    assert job["url"] == "https://jobs.ashbyhq.com/openai/abc-123"
    assert job["source"] == "ashby:openai"
    assert "research" in job["tags"]
    assert "applied-ai" in job["tags"]
    assert loop_mod.infer_method(job["url"]) == "ashby"


def test_discover_company_boards_isolates_failures(loop_mod, monkeypatch, tmp_path):
    # Point config at a temp file so the test is hermetic.
    config = tmp_path / "company_boards.json"
    config.write_text(
        '{"boards": ['
        '{"company": "OpenAI", "ats": "ashby", "slug": "openai"},'
        '{"company": "xAI",    "ats": "greenhouse", "slug": "xai"}'
        "]}",
        encoding="utf-8",
    )
    monkeypatch.setattr(loop_mod, "COMPANY_BOARDS_CONFIG", config)

    def _flaky_ashby(slug, company):
        raise RuntimeError("ashby outage")

    def _ok_greenhouse(slug, company):
        return [
            {
                "source": f"greenhouse:{slug}",
                "company": company,
                "title": "Software Engineer",
                "location": "Remote",
                "url": f"https://job-boards.greenhouse.io/{slug}/jobs/1",
                "description": "Python.",
                "tags": "engineering",
            }
        ]

    monkeypatch.setattr(loop_mod, "discover_ashby_board", _flaky_ashby)
    monkeypatch.setattr(loop_mod, "discover_greenhouse_board", _ok_greenhouse)

    jobs = list(loop_mod.discover_company_boards())
    # Ashby raised; Greenhouse still yielded its job.
    assert len(jobs) == 1
    assert jobs[0]["company"] == "xAI"


def test_main_admits_company_board_jobs(loop_mod, tmp_path, monkeypatch):
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
    monkeypatch.setattr(loop_mod, "discover_remotive", lambda: [])
    monkeypatch.setattr(loop_mod, "discover_remoteok", lambda: [])
    monkeypatch.setattr(
        loop_mod,
        "discover_company_boards",
        lambda: [
            {
                "company": "OpenAI",
                "title": "Software Engineer — Applied",
                "location": "Remote",
                "salary": "",
                "job_type": "FullTime",
                "source": "ashby:openai",
                "url": "https://jobs.ashbyhq.com/openai/abc-123",
                "listing_url": "https://jobs.ashbyhq.com/openai/abc-123",
                "tags": "engineering;applied-ai",
                "description": "Build agent infrastructure with Python and APIs.",
            },
            {
                "company": "xAI",
                "title": "Senior Software Engineer",
                "location": "Remote",
                "salary": "",
                "job_type": "",
                "source": "greenhouse:xai",
                "url": "https://job-boards.greenhouse.io/xai/jobs/123456",
                "listing_url": "https://job-boards.greenhouse.io/xai/jobs/123456",
                "tags": "engineering",
                "description": "Python backend for LLM training infrastructure.",
            },
        ],
    )
    monkeypatch.setattr(sys, "argv", ["ralph_loop_ci.py", "--max-new-jobs", "5"])

    loop_mod.main()

    with tracker_csv.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    companies = sorted(row["Company"] for row in rows)
    assert companies == ["OpenAI", "xAI"]
    assert all(row["Submission Lane"] == "ci_auto" for row in rows)


def _fde_profile(loop_mod):
    return loop_mod.RoleProfile(
        track="fde",
        score=5,
        signals=["customer-integration", "fde-title", "python"],
        is_relevant=True,
        philosophy="",
        distinctive_achievements=[],
    )


def test_cover_letter_has_no_ai_tell_phrases(loop_mod):
    job = {
        "company": "Cohere",
        "title": "Solutions Architect",
        "description": (
            "Partner with enterprise customers to deploy Cohere models in "
            "production. Own the integration arc from evaluation to launch."
        ),
        "url": "https://jobs.ashbyhq.com/cohere/abc",
    }
    cl = loop_mod.build_cover_letter(job, _fde_profile(loop_mod))
    lowered = cl.lower()
    for phrase in loop_mod.AI_TELL_PHRASES:
        assert phrase not in lowered, f"AI-tell leaked: {phrase!r}\n---\n{cl}"
    # No lowercase 'i ' at sentence position (old .lower() bug).
    assert " i build " not in lowered
    assert "\ni build" not in lowered


def test_cover_letter_varies_across_companies_and_roles(loop_mod):
    jobs = [
        {
            "company": "OpenAI",
            "title": "Member of Technical Staff — Applied",
            "description": "Build production agent infrastructure with Python.",
            "url": "https://jobs.ashbyhq.com/openai/1",
        },
        {
            "company": "xAI",
            "title": "Senior Software Engineer",
            "description": "Train LLMs at scale; deploy inference stacks.",
            "url": "https://job-boards.greenhouse.io/xai/jobs/1",
        },
        {
            "company": "Databricks",
            "title": "Solutions Architect",
            "description": "Partner with enterprise customers on LLM deployments.",
            "url": "https://job-boards.greenhouse.io/databricks/jobs/1",
        },
        {
            "company": "Stripe",
            "title": "ML Platform Engineer",
            "description": "Own inference latency and cost for payment risk models.",
            "url": "https://job-boards.greenhouse.io/stripe/jobs/1",
        },
    ]
    letters = [loop_mod.build_cover_letter(j, _fde_profile(loop_mod)) for j in jobs]
    # Each letter is unique.
    assert len(set(letters)) == len(letters), "letters are not unique per job"
    # Each letter names its company and role.
    for cl, j in zip(letters, jobs):
        assert j["company"] in cl
        assert j["title"] in cl
    # Opener sentences differ across at least 3 of the 4 letters.
    openers = {cl.split("\n\n")[1].split(".")[0] for cl in letters if "\n\n" in cl}
    assert len(openers) >= 3, f"expected >=3 distinct opener sentences, got {openers}"


def test_cover_letter_quotes_jd_anchor_when_available(loop_mod):
    job = {
        "company": "Cohere",
        "title": "Solutions Architect",
        "description": (
            "We scale enterprise deployment of production LLMs for Fortune 500 "
            "customers. You will own the technical relationship with enterprise "
            "accounts and drive adoption of Cohere platform primitives."
        ),
        "url": "https://jobs.ashbyhq.com/cohere/abc",
    }
    cl = loop_mod.build_cover_letter(job, _fde_profile(loop_mod))
    # The anchor quoter should emit a sentence verbatim from the JD.
    assert "The JD mentions:" in cl


def test_cover_letter_is_deterministic_for_same_job(loop_mod):
    job = {
        "company": "Anthropic",
        "title": "Forward Deployed Engineer",
        "description": "Work directly with customers to deploy Claude in production.",
        "url": "https://job-boards.greenhouse.io/anthropic/jobs/1",
    }
    a = loop_mod.build_cover_letter(job, _fde_profile(loop_mod))
    b = loop_mod.build_cover_letter(job, _fde_profile(loop_mod))
    assert a == b


def test_cover_letter_uses_candidate_profile_linkedin(loop_mod):
    job = {
        "company": "Anthropic",
        "title": "Software Engineer",
        "description": "Production AI work.",
        "url": "https://job-boards.greenhouse.io/anthropic/jobs/1",
    }
    cl = loop_mod.build_cover_letter(job, _fde_profile(loop_mod))
    # Must use the authoritative LinkedIn from candidate_profile.json
    # (igor-ganapolsky-859317343), not the stale short form.
    assert "igor-ganapolsky-859317343" in cl
