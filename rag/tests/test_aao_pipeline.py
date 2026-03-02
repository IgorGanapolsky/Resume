import json
from pathlib import Path
import sys

# Add the scripts directory to the path so we can import the modules
sys.path.append(str(Path(__file__).resolve().parent.parent.parent / "scripts"))

from ci_submit_pipeline import SubmitAnswers, _enhance_answers_with_jsonld
from ralph_loop_ci import (
    create_artifacts,
    RoleProfile,
    _extract_and_parse_json,
    SwarmMetrics,
)


def test_swarm_metrics():
    metrics = SwarmMetrics()
    metrics.log_latency("anthropic", 1.5)
    metrics.log_latency("anthropic", 2.0)
    metrics.log_latency("gemini", 0.5)
    metrics.log_consensus(8.5, [8.0, 9.0, 8.5])

    prom_data = metrics.export_prometheus()
    assert 'swarm_llm_latency_seconds{provider="anthropic"} 1.7500' in prom_data
    assert 'swarm_llm_latency_seconds{provider="gemini"} 0.5000' in prom_data
    assert "swarm_average_score 8.50" in prom_data
    assert "swarm_consensus_rate" in prom_data


def test_extract_and_parse_json():
    # 1. Clean JSON
    assert _extract_and_parse_json('{"a": 1}') == {"a": 1}

    # 2. Markdown block
    assert _extract_and_parse_json('Here is the data:\n```json\n{"b": 2}\n```') == {
        "b": 2
    }

    # 3. Messy text around JSON
    assert _extract_and_parse_json('Random text { "c": 3 } more text') == {"c": 3}

    # 4. Trailing commas (Self-healing)
    assert _extract_and_parse_json('{"d": 4,}') == {"d": 4}
    assert _extract_and_parse_json("[1, 2, 3,]") == [1, 2, 3]

    # 5. Single quotes (Self-healing attempt)
    # Note: Our simple regex replacement might be limited, but let's test basic case
    # Current implementation: cleaned = re.sub(r"(?<=[:\[,])\s*'(.*?)'\s*(?=[,\]}])", r' "\1" ', cleaned)
    # This specifically looks for single quotes used as value delimiters.
    assert _extract_and_parse_json("{\"e\": 'value'}") == {"e": "value"}


def test_enhance_answers_with_jsonld(tmp_path, monkeypatch):
    # Mock ROOT in ci_submit_pipeline
    monkeypatch.setattr("ci_submit_pipeline.ROOT", tmp_path)

    # Setup test directories
    company = "TestCompany"
    role = "AI Engineer"
    base_dir = tmp_path / "applications" / "testcompany" / "tailored_resumes"
    base_dir.mkdir(parents=True, exist_ok=True)

    jsonld_file = base_dir / "2026-03-01_testcompany_ai-engineer.jsonld"
    jsonld_file.write_text(
        json.dumps(
            {
                "@type": "Person",
                "description": "I am the perfect match because I build AI.",
            }
        ),
        encoding="utf-8",
    )

    base_answers = SubmitAnswers(
        work_authorization_us=True,
        require_sponsorship=False,
        role_interest="I like AI",
        eeo_default="Decline",
    )

    enhanced = _enhance_answers_with_jsonld(base_answers, company, role)
    assert enhanced.match_justification == "I am the perfect match because I build AI."
    assert enhanced.role_interest == "I like AI"  # original untouched
    assert base_answers.match_justification == ""  # no side effects


def test_create_artifacts_jsonld(tmp_path, monkeypatch):
    # Mock ROOT and directories in ralph_loop_ci
    monkeypatch.setattr("ralph_loop_ci.ROOT", tmp_path)
    monkeypatch.setattr("ralph_loop_ci.APPLICATIONS_DIR", tmp_path / "applications")
    monkeypatch.setattr("ralph_loop_ci.BASE_RESUME", tmp_path / "resumes" / "base.html")

    resumes_dir = tmp_path / "resumes"
    resumes_dir.mkdir()
    (resumes_dir / "base.html").write_text("<html>base resume</html>")

    # Mock the LLM call to return a specific justification
    def mock_tailor(*args, **kwargs):
        return "<html>tailored</html>", "My JSONLD justification"

    monkeypatch.setattr("ralph_loop_ci.tailor_resume_with_llm", mock_tailor)
    monkeypatch.setattr(
        "os.getenv", lambda k, default=None: "dummy_key" if "API_KEY" in k else default
    )

    job = {
        "company": "Anthropic",
        "title": "FDE",
        "url": "http://anthropic.com/jobs/1",
        "description": "do AI things",
    }
    profile = RoleProfile(track="fde", score=100, signals=["python"], is_relevant=True)

    # Stub the docx converter to avoid pandoc/pandoc dependency in tests
    monkeypatch.setattr("ralph_loop_ci._ensure_docx_from_html", lambda x, y: None)

    create_artifacts(job, "2026-03-01", profile)

    # Verify the jsonld file was created
    company_dir = tmp_path / "applications" / "anthropic" / "tailored_resumes"
    jsonld_files = list(company_dir.glob("*.jsonld"))
    assert len(jsonld_files) == 1

    data = json.loads(jsonld_files[0].read_text())
    assert data["@type"] == "Person"
    assert data["jobTitle"] == "fde"
    assert data["description"] == "My JSONLD justification"

    # Verify the signature is present and valid
    assert "agent_signature" in data
    assert data["agent_signature"]["algorithm"] == "HMAC-SHA256"

    import agent_identity

    sig = data["agent_signature"]["value"]
    content_to_verify = data.copy()
    content_to_verify.pop("agent_signature")
    assert agent_identity.verify_artifact(content_to_verify, sig)
