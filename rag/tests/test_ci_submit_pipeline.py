"""Tests for queue gating and auto-promotion in scripts/ci_submit_pipeline.py."""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
import zipfile
from pathlib import Path


def _load_module():
    script_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "ci_submit_pipeline.py"
    )
    spec = importlib.util.spec_from_file_location(
        "ci_submit_pipeline_test_mod", script_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def _write_tracker(path: Path, rows: list[dict[str, str]]) -> None:
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
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _seed_fde_artifacts(root: Path, role_slug: str, html_content: str) -> None:
    company_slug = "elevenlabs"
    resume_dir = root / "applications" / company_slug / "tailored_resumes"
    cover_dir = root / "applications" / company_slug / "cover_letters"
    jobs_dir = root / "applications" / company_slug / "jobs"
    resume_dir.mkdir(parents=True, exist_ok=True)
    cover_dir.mkdir(parents=True, exist_ok=True)
    jobs_dir.mkdir(parents=True, exist_ok=True)

    (resume_dir / f"2026-02-19_{company_slug}_{role_slug}.docx").write_bytes(b"docx")
    (resume_dir / f"2026-02-19_{company_slug}_{role_slug}.html").write_text(
        html_content, encoding="utf-8"
    )
    (cover_dir / f"2026-02-19_{company_slug}_{role_slug}.md").write_text(
        "Cover letter", encoding="utf-8"
    )
    (jobs_dir / f"2026-02-19_{company_slug}_{role_slug}_abcd1234.md").write_text(
        "Requirements: customer integrations, Python, APIs.",
        encoding="utf-8",
    )


def _seed_fde_artifacts_html_only(
    root: Path, role_slug: str, html_content: str
) -> None:
    company_slug = "elevenlabs"
    resume_dir = root / "applications" / company_slug / "tailored_resumes"
    cover_dir = root / "applications" / company_slug / "cover_letters"
    jobs_dir = root / "applications" / company_slug / "jobs"
    resume_dir.mkdir(parents=True, exist_ok=True)
    cover_dir.mkdir(parents=True, exist_ok=True)
    jobs_dir.mkdir(parents=True, exist_ok=True)

    (resume_dir / f"2026-02-19_{company_slug}_{role_slug}.html").write_text(
        html_content, encoding="utf-8"
    )
    (cover_dir / f"2026-02-19_{company_slug}_{role_slug}.md").write_text(
        "Cover letter", encoding="utf-8"
    )
    (jobs_dir / f"2026-02-19_{company_slug}_{role_slug}_abcd1234.md").write_text(
        "Requirements: customer integrations, Python, APIs.",
        encoding="utf-8",
    )


def test_queue_only_promotes_high_fit_draft(tmp_path, monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    role = "Forward Deployed Engineer - Software Engineer"
    role_slug = mod._slug(role)[:64]
    _seed_fde_artifacts(
        tmp_path,
        role_slug,
        (
            "Forward-Deployed AI/Software Engineer "
            "FORWARD-DEPLOYED COMPETENCIES "
            "customer-facing delivery "
            "integration engineering "
            "<strong>35%</strong>"
        ),
    )

    tracker = tmp_path / "application_tracker.csv"
    report = tmp_path / "report.json"
    _write_tracker(
        tracker,
        [
            {
                "Company": "ElevenLabs",
                "Role": role,
                "Location": "Remote",
                "Salary Range": "",
                "Status": "Draft",
                "Date Applied": "",
                "Follow Up Date": "",
                "Response": "",
                "Interview Stage": "Initial",
                "Days To Response": "",
                "Response Type": "",
                "Cover Letter Used": "",
                "What Worked": "",
                "Tags": "ai;integration",
                "Notes": "customer integrations",
                "Career Page URL": "https://jobs.ashbyhq.com/elevenlabs/abc123",
            }
        ],
    )

    rc = mod.run_pipeline(
        tracker_csv=tracker,
        report_path=report,
        dry_run=True,
        queue_only=True,
        max_jobs=5,
        fail_on_error=False,
        fit_threshold=70,
    )
    assert rc == 0

    with tracker.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["Status"] == "ReadyToSubmit"
    assert rows[0]["Submission Lane"] == "ci_auto:ashby"
    assert rows[0]["Remote Policy"] in {"remote", "hybrid", "unknown"}
    assert rows[0]["Remote Likelihood Score"]

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["queue_promoted_count"] == 1
    assert payload["queue_demoted_count"] == 0


def test_queue_only_keeps_low_fit_draft(tmp_path, monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    role = "Forward Deployed Engineer - Software Engineer"
    role_slug = mod._slug(role)[:64]
    _seed_fde_artifacts(
        tmp_path, role_slug, "Generic resume without required fit signals"
    )

    tracker = tmp_path / "application_tracker.csv"
    report = tmp_path / "report.json"
    _write_tracker(
        tracker,
        [
            {
                "Company": "ElevenLabs",
                "Role": role,
                "Location": "Remote",
                "Salary Range": "",
                "Status": "Draft",
                "Date Applied": "",
                "Follow Up Date": "",
                "Response": "",
                "Interview Stage": "Initial",
                "Days To Response": "",
                "Response Type": "",
                "Cover Letter Used": "",
                "What Worked": "",
                "Tags": "ai;integration",
                "Notes": "",
                "Career Page URL": "https://jobs.ashbyhq.com/elevenlabs/abc123",
            }
        ],
    )

    rc = mod.run_pipeline(
        tracker_csv=tracker,
        report_path=report,
        dry_run=True,
        queue_only=True,
        max_jobs=5,
        fail_on_error=False,
        fit_threshold=70,
    )
    assert rc == 0

    with tracker.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["Status"] == "Draft"

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["queue_promoted_count"] == 0


def test_queue_only_demotes_ready_row_when_fit_drops(tmp_path, monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    role = "Forward Deployed Engineer - Software Engineer"
    role_slug = mod._slug(role)[:64]
    _seed_fde_artifacts(tmp_path, role_slug, "Low-fit resume text")

    tracker = tmp_path / "application_tracker.csv"
    report = tmp_path / "report.json"
    _write_tracker(
        tracker,
        [
            {
                "Company": "ElevenLabs",
                "Role": role,
                "Location": "Remote",
                "Salary Range": "",
                "Status": "ReadyToSubmit",
                "Date Applied": "",
                "Follow Up Date": "",
                "Response": "",
                "Interview Stage": "Initial",
                "Days To Response": "",
                "Response Type": "",
                "Cover Letter Used": "",
                "What Worked": "",
                "Tags": "ai;integration",
                "Notes": "",
                "Career Page URL": "https://jobs.ashbyhq.com/elevenlabs/abc123",
            }
        ],
    )

    rc = mod.run_pipeline(
        tracker_csv=tracker,
        report_path=report,
        dry_run=True,
        queue_only=True,
        max_jobs=5,
        fail_on_error=False,
        fit_threshold=70,
    )
    assert rc == 0

    with tracker.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["Status"] == "Draft"

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["queue_demoted_count"] == 1


def test_queue_only_autogenerates_docx_from_html(tmp_path, monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    role = "Forward Deployed Engineer - Software Engineer"
    role_slug = mod._slug(role)[:64]
    _seed_fde_artifacts_html_only(
        tmp_path,
        role_slug,
        (
            "Forward-Deployed AI/Software Engineer "
            "FORWARD-DEPLOYED COMPETENCIES "
            "customer-facing delivery "
            "integration engineering "
            "<strong>35%</strong>"
        ),
    )

    tracker = tmp_path / "application_tracker.csv"
    report = tmp_path / "report.json"
    _write_tracker(
        tracker,
        [
            {
                "Company": "ElevenLabs",
                "Role": role,
                "Location": "Remote",
                "Salary Range": "",
                "Status": "Draft",
                "Date Applied": "",
                "Follow Up Date": "",
                "Response": "",
                "Interview Stage": "Initial",
                "Days To Response": "",
                "Response Type": "",
                "Cover Letter Used": "",
                "What Worked": "",
                "Tags": "ai;integration",
                "Notes": "customer integrations",
                "Career Page URL": "https://jobs.ashbyhq.com/elevenlabs/abc123",
            }
        ],
    )

    rc = mod.run_pipeline(
        tracker_csv=tracker,
        report_path=report,
        dry_run=True,
        queue_only=True,
        max_jobs=5,
        fail_on_error=False,
        fit_threshold=70,
    )
    assert rc == 0

    with tracker.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["Status"] == "ReadyToSubmit"

    generated_docx = (
        tmp_path
        / "applications"
        / "elevenlabs"
        / "tailored_resumes"
        / f"2026-02-19_elevenlabs_{role_slug}.docx"
    )
    assert generated_docx.exists()
    with zipfile.ZipFile(generated_docx) as zf:
        assert "word/document.xml" in zf.namelist()


def test_queue_only_blocks_non_technical_role(tmp_path, monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    role = "Office Assistant"
    role_slug = mod._slug(role)[:64]
    _seed_fde_artifacts(
        tmp_path,
        role_slug,
        "summary professional experience",
    )

    tracker = tmp_path / "application_tracker.csv"
    report = tmp_path / "report.json"
    _write_tracker(
        tracker,
        [
            {
                "Company": "Coalition Technologies",
                "Role": role,
                "Location": "Remote",
                "Salary Range": "",
                "Status": "Draft",
                "Date Applied": "",
                "Follow Up Date": "",
                "Response": "",
                "Interview Stage": "Initial",
                "Days To Response": "",
                "Response Type": "",
                "Cover Letter Used": "",
                "What Worked": "",
                "Tags": "operations;admin",
                "Notes": "",
                "Career Page URL": "https://example.com/jobs/office-assistant",
            }
        ],
    )

    rc = mod.run_pipeline(
        tracker_csv=tracker,
        report_path=report,
        dry_run=True,
        queue_only=True,
        max_jobs=5,
        fail_on_error=False,
        fit_threshold=70,
    )
    assert rc == 0
    with tracker.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["Status"] == "Draft"
    payload = json.loads(report.read_text(encoding="utf-8"))
    reasons = payload["queue_audit"][0]["reasons"]
    assert "non_technical_role" in reasons


def test_queue_only_blocks_unsupported_site_even_with_high_fit(tmp_path, monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    role = "Forward Deployed Engineer - Software Engineer"
    role_slug = mod._slug(role)[:64]
    _seed_fde_artifacts(
        tmp_path,
        role_slug,
        (
            "Forward-Deployed AI/Software Engineer "
            "FORWARD-DEPLOYED COMPETENCIES "
            "customer-facing delivery "
            "integration engineering "
            "<strong>35%</strong>"
        ),
    )

    tracker = tmp_path / "application_tracker.csv"
    report = tmp_path / "report.json"
    _write_tracker(
        tracker,
        [
            {
                "Company": "ElevenLabs",
                "Role": role,
                "Location": "Remote",
                "Salary Range": "",
                "Status": "Draft",
                "Date Applied": "",
                "Follow Up Date": "",
                "Response": "",
                "Interview Stage": "Initial",
                "Days To Response": "",
                "Response Type": "",
                "Cover Letter Used": "",
                "What Worked": "",
                "Tags": "ai;integration",
                "Notes": "customer integrations",
                "Career Page URL": "https://remotive.com/remote-jobs/software-development/some-role-123",
            }
        ],
    )

    rc = mod.run_pipeline(
        tracker_csv=tracker,
        report_path=report,
        dry_run=True,
        queue_only=True,
        max_jobs=5,
        fail_on_error=False,
        fit_threshold=70,
    )
    assert rc == 0
    with tracker.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["Status"] == "Draft"
    assert rows[0]["Submission Lane"] == "manual"
    payload = json.loads(report.read_text(encoding="utf-8"))
    reasons = payload["queue_audit"][0]["reasons"]
    assert "unsupported_site_for_ci_submit" in reasons


def test_queue_only_blocks_low_remote_likelihood(tmp_path, monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    role = "Forward Deployed Engineer - Software Engineer"
    role_slug = mod._slug(role)[:64]
    _seed_fde_artifacts(
        tmp_path,
        role_slug,
        (
            "Forward-Deployed AI/Software Engineer "
            "FORWARD-DEPLOYED COMPETENCIES "
            "customer-facing delivery "
            "integration engineering "
            "<strong>35%</strong>"
        ),
    )

    tracker = tmp_path / "application_tracker.csv"
    report = tmp_path / "report.json"
    _write_tracker(
        tracker,
        [
            {
                "Company": "ElevenLabs",
                "Role": role,
                "Location": "Onsite - New York",
                "Salary Range": "",
                "Status": "Draft",
                "Date Applied": "",
                "Follow Up Date": "",
                "Response": "",
                "Interview Stage": "Initial",
                "Days To Response": "",
                "Response Type": "",
                "Cover Letter Used": "",
                "What Worked": "",
                "Tags": "ai;integration",
                "Notes": "",
                "Career Page URL": "https://jobs.ashbyhq.com/elevenlabs/abc123",
            }
        ],
    )

    rc = mod.run_pipeline(
        tracker_csv=tracker,
        report_path=report,
        dry_run=True,
        queue_only=True,
        max_jobs=5,
        fail_on_error=False,
        fit_threshold=70,
        remote_min_score=50,
    )
    assert rc == 0
    with tracker.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["Status"] == "Draft"
    payload = json.loads(report.read_text(encoding="utf-8"))
    reasons = payload["queue_audit"][0]["reasons"]
    assert any(r.startswith("remote_likelihood_below_threshold:") for r in reasons)


def test_dry_run_skipped_rows_do_not_fail_by_default(tmp_path, monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    tracker = tmp_path / "application_tracker.csv"
    report = tmp_path / "report.json"
    _write_tracker(
        tracker,
        [
            {
                "Company": "Example",
                "Role": "Software Engineer",
                "Location": "Remote",
                "Salary Range": "",
                "Status": "ReadyToSubmit",
                "Date Applied": "",
                "Follow Up Date": "",
                "Response": "",
                "Interview Stage": "Initial",
                "Days To Response": "",
                "Response Type": "",
                "Cover Letter Used": "",
                "What Worked": "",
                "Tags": "ai;software",
                "Notes": "",
                "Career Page URL": "https://example.com/jobs/software-engineer",
            }
        ],
    )

    rc = mod.run_pipeline(
        tracker_csv=tracker,
        report_path=report,
        dry_run=True,
        queue_only=False,
        max_jobs=5,
        fail_on_error=True,
    )
    assert rc == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["failed_count"] == 0
    assert payload["skipped_count"] == 1


def test_dry_run_skipped_rows_can_be_treated_as_failures(tmp_path, monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    tracker = tmp_path / "application_tracker.csv"
    report = tmp_path / "report.json"
    _write_tracker(
        tracker,
        [
            {
                "Company": "Example",
                "Role": "Software Engineer",
                "Location": "Remote",
                "Salary Range": "",
                "Status": "ReadyToSubmit",
                "Date Applied": "",
                "Follow Up Date": "",
                "Response": "",
                "Interview Stage": "Initial",
                "Days To Response": "",
                "Response Type": "",
                "Cover Letter Used": "",
                "What Worked": "",
                "Tags": "ai;software",
                "Notes": "",
                "Career Page URL": "https://example.com/jobs/software-engineer",
            }
        ],
    )

    rc = mod.run_pipeline(
        tracker_csv=tracker,
        report_path=report,
        dry_run=True,
        queue_only=False,
        max_jobs=5,
        fail_on_error=True,
        count_skipped_as_failures=True,
    )
    assert rc == 1
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["failed_count"] == 1
    assert payload["skipped_count"] == 1


class _FakeLocator:
    def __init__(self, count_fn, on_click=None, on_fill=None):
        self._count_fn = count_fn
        self._on_click = on_click
        self._on_fill = on_fill

    @property
    def first(self):
        return self

    def count(self):
        return self._count_fn()

    def click(self, timeout=None):
        if self._on_click is not None:
            self._on_click()

    def fill(self, value: str, timeout=None):
        if self._on_fill is not None:
            self._on_fill(value)


class _FakeOptionNode:
    def __init__(self, text: str):
        self._text = text

    def inner_text(self, timeout=None):
        return self._text


class _FakeOptionList:
    def __init__(self, options: list[str]):
        self._options = options

    def count(self):
        return len(self._options)

    def nth(self, idx: int):
        return _FakeOptionNode(self._options[idx])


class _FakeSelect:
    def __init__(self, options: list[str]):
        self._options = options
        self.selected_label = None

    def locator(self, selector: str):
        if selector == "option":
            return _FakeOptionList(self._options)
        return _FakeLocator(lambda: 0)

    def select_option(self, label: str, timeout=None):
        self.selected_label = label


class _FakeContainerWithTextField:
    def __init__(self):
        self.filled = None

    def locator(self, selector: str):
        if "textarea" in selector or "input" in selector:
            return _FakeLocator(lambda: 1, on_fill=self._set)
        return _FakeLocator(lambda: 0)

    def get_by_role(self, role: str):
        return _FakeLocator(lambda: 0)

    def _set(self, value: str):
        self.filled = value


class _FakeScope:
    def __init__(self, has_file: bool = False, text: str = "", url: str = ""):
        self.has_file = has_file
        self._text = text
        self.url = url
        self.frames = []

    def locator(self, selector: str):
        if selector == "input[type='file']":
            return _FakeLocator(lambda: 1 if self.has_file else 0)
        return _FakeLocator(lambda: 0)

    def inner_text(self, selector: str):
        return self._text

    def wait_for_timeout(self, ms: int):
        return None


class _FakeAshbyPage(_FakeScope):
    def __init__(self):
        super().__init__(has_file=False)
        self.apply_clicks = 0

    def get_by_role(self, role: str, name=None):
        text = "Apply for this job"
        if role in {"button", "link"} and hasattr(name, "search") and name.search(text):
            return _FakeLocator(lambda: 1, on_click=self._open_form)
        return _FakeLocator(lambda: 0)

    def _open_form(self):
        self.apply_clicks += 1
        self.has_file = True

    def wait_for_timeout(self, ms: int):
        return None


def test_resolve_form_scope_finds_file_input_in_frame():
    mod = _load_module()
    adapter = mod.PlaywrightFormAdapter()
    page = _FakeScope(has_file=False)
    frame = _FakeScope(has_file=True)
    page.frames = [frame]

    scope = adapter._resolve_form_scope(page)
    assert scope is frame


def test_ashby_resolve_form_scope_clicks_apply_when_form_hidden():
    mod = _load_module()
    adapter = mod.AshbyAdapter()
    page = _FakeAshbyPage()

    scope = adapter._resolve_form_scope(page)
    assert scope is page
    assert page.has_file is True
    assert page.apply_clicks >= 1


def test_wait_for_confirmation_accepts_success_url():
    mod = _load_module()
    adapter = mod.AshbyAdapter()
    page = _FakeScope(
        has_file=True,
        text="",
        url="https://jobs.ashbyhq.com/example/application/submitted",
    )
    scope = _FakeScope(has_file=True, text="", url=page.url)

    assert adapter._wait_for_confirmation(page, scope) is True


def test_load_answers_from_env_parses_required_fields(monkeypatch):
    mod = _load_module()
    key = "TEST_CI_SUBMIT_ANSWERS_JSON"
    monkeypatch.setenv(
        key,
        json.dumps(
            {
                "work_authorization_us": "yes",
                "require_sponsorship": "no",
                "role_interest": "AI-heavy, LLM-first project with an industry leader.",
                "eeo_default": "Prefer not to say",
            }
        ),
    )
    answers = mod._load_answers_from_env(key)
    assert answers is not None
    assert answers.work_authorization_us is True
    assert answers.require_sponsorship is False
    assert "industry leader" in answers.role_interest


def test_execute_requires_answers_secret(tmp_path, monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    tracker = tmp_path / "application_tracker.csv"
    report = tmp_path / "report.json"
    _write_tracker(
        tracker,
        [
            {
                "Company": "Example",
                "Role": "Software Engineer",
                "Location": "Remote",
                "Salary Range": "",
                "Status": "ReadyToSubmit",
                "Date Applied": "",
                "Follow Up Date": "",
                "Response": "",
                "Interview Stage": "Initial",
                "Days To Response": "",
                "Response Type": "",
                "Cover Letter Used": "",
                "What Worked": "",
                "Tags": "ai;software",
                "Notes": "",
                "Career Page URL": "https://jobs.ashbyhq.com/example/abc123",
            }
        ],
    )

    monkeypatch.delenv("TEST_PROFILE", raising=False)
    monkeypatch.delenv("TEST_AUTH", raising=False)
    monkeypatch.delenv("TEST_ANSWERS", raising=False)
    monkeypatch.setenv(
        "TEST_PROFILE",
        json.dumps(
            {
                "first_name": "Igor",
                "last_name": "Ganapolsky",
                "email": "iganapolsky@gmail.com",
                "phone": "(201) 639-1534",
            }
        ),
    )
    monkeypatch.setenv("TEST_AUTH", json.dumps({"ashby": {}}))

    rc = mod.run_pipeline(
        tracker_csv=tracker,
        report_path=report,
        dry_run=False,
        queue_only=False,
        max_jobs=1,
        fail_on_error=False,
        profile_env="TEST_PROFILE",
        auth_env="TEST_AUTH",
        answers_env="TEST_ANSWERS",
    )
    assert rc == 2


def test_select_yes_no_on_select_handles_yes():
    mod = _load_module()
    adapter = mod.AshbyAdapter()
    select = _FakeSelect(["Select...", "Yes", "No"])
    ok = adapter._select_yes_no_on_select(select, True)
    assert ok is True
    assert select.selected_label == "Yes"


def test_select_yes_no_on_select_handles_no_with_long_label():
    mod = _load_module()
    adapter = mod.AshbyAdapter()
    select = _FakeSelect(["Select...", "No, I do not require sponsorship"])
    ok = adapter._select_yes_no_on_select(select, False)
    assert ok is True
    assert select.selected_label == "No, I do not require sponsorship"


def test_fill_yes_no_text_in_container_uses_text_field():
    mod = _load_module()
    adapter = mod.AshbyAdapter()
    container = _FakeContainerWithTextField()

    ok = adapter._fill_yes_no_text_in_container(container, False)
    assert ok is True
    assert container.filled == "No"


def test_ashby_extract_failure_details_reports_required_questions():
    mod = _load_module()
    adapter = mod.AshbyAdapter()
    scope = _FakeScope(
        text="Please review and answer all required questions before submitting your application."
    )
    page = _FakeScope(text="")

    detail = adapter._extract_failure_details(page, scope)
    assert detail == "required_questions_unanswered_after_retry"


def test_ashby_post_submit_retry_runs_fallbacks_and_retries(monkeypatch):
    mod = _load_module()
    adapter = mod.AshbyAdapter()
    scope = _FakeScope(text="")
    page = _FakeScope(text="")
    calls: list[str] = []

    monkeypatch.setattr(adapter, "_has_required_question_error", lambda s, p: True)
    monkeypatch.setattr(
        adapter,
        "_set_prefer_not_to_say_defaults",
        lambda s, p, default_text: calls.append("eeo"),
    )
    monkeypatch.setattr(
        adapter, "_fill_unanswered_radio_groups", lambda s, p: calls.append("radios")
    )
    monkeypatch.setattr(
        adapter, "_fill_unanswered_selects", lambda s, p: calls.append("selects")
    )
    monkeypatch.setattr(
        adapter, "_apply_required_answers", lambda s, p, answers: calls.append("answers")
    )
    monkeypatch.setattr(
        adapter, "_click_submit", lambda s, p: calls.append("submit") or True
    )

    answers = mod.SubmitAnswers(
        work_authorization_us=True,
        require_sponsorship=False,
        role_interest="ai-heavy, LLM-first project with an industry leader",
        eeo_default="prefer not to say",
    )
    profile = mod.Profile(
        first_name="Igor",
        last_name="Ganapolsky",
        email="iganapolsky@gmail.com",
        phone="(201) 639-1534",
    )

    ok = adapter._post_submit_retry(scope, page, profile, answers)
    assert ok is True
    assert calls == ["eeo", "radios", "selects", "answers", "submit"]


def test_ashby_post_submit_retry_no_error_banner_no_retry(monkeypatch):
    mod = _load_module()
    adapter = mod.AshbyAdapter()
    scope = _FakeScope(text="")
    page = _FakeScope(text="")

    monkeypatch.setattr(adapter, "_has_required_question_error", lambda s, p: False)
    monkeypatch.setattr(adapter, "_click_submit", lambda s, p: False)

    answers = mod.SubmitAnswers(
        work_authorization_us=True,
        require_sponsorship=False,
        role_interest="ai-heavy, LLM-first project with an industry leader",
        eeo_default="prefer not to say",
    )
    profile = mod.Profile(
        first_name="Igor",
        last_name="Ganapolsky",
        email="iganapolsky@gmail.com",
        phone="(201) 639-1534",
    )

    ok = adapter._post_submit_retry(scope, page, profile, answers)
    assert ok is False
