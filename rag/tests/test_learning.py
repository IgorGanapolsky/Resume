"""Tests for rag.learning RLHF report/ranking helpers."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_learning_module():
    module_path = Path(__file__).resolve().parents[1] / "learning.py"
    spec = importlib.util.spec_from_file_location("rag_learning_test_mod", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def test_rank_rows_by_learning_prefers_ci_auto_and_higher_signal():
    mod = _load_learning_module()
    arms = {
        "method:greenhouse": {
            "name": "method:greenhouse",
            "alpha": 4.0,
            "beta": 2.0,
            "pulls": 4,
            "total_reward": 3.0,
        },
        "method:ashby": {
            "name": "method:ashby",
            "alpha": 1.5,
            "beta": 4.5,
            "pulls": 4,
            "total_reward": 0.5,
        },
        "cat:platform": {
            "name": "cat:platform",
            "alpha": 3.0,
            "beta": 1.0,
            "pulls": 2,
            "total_reward": 2.0,
        },
        "cat:infra": {
            "name": "cat:infra",
            "alpha": 1.2,
            "beta": 4.8,
            "pulls": 4,
            "total_reward": 0.2,
        },
    }
    rows = [
        {
            "Company": "OpenEvidence",
            "Role": "Software Engineer - Backend and Infrastructure",
            "Status": "ReadyToSubmit",
            "Career Page URL": "https://jobs.ashbyhq.com/openevidence/abc",
            "Tags": "ai;infra",
            "Submission Lane": "ci_auto:ashby",
            "Notes": "CI submit blocked by anti-bot on 2026-04-01 via ashby. Reason=recaptcha_score_below_threshold.",
        },
        {
            "Company": "Vercel",
            "Role": "Senior Customer Support Engineer",
            "Status": "ReadyToSubmit",
            "Career Page URL": "https://job-boards.greenhouse.io/vercel/jobs/xyz",
            "Tags": "platform;support",
            "Submission Lane": "ci_auto:greenhouse",
            "Notes": "",
        },
        {
            "Company": "Manual Co",
            "Role": "Operations Manager",
            "Status": "ReadyToSubmit",
            "Career Page URL": "https://example.com/jobs/1",
            "Tags": "operations",
            "Submission Lane": "manual",
            "Notes": "",
        },
    ]

    ranked = mod.rank_rows_by_learning(rows, arms, status_filter="ready", max_rows=3)

    assert [row["company"] for row in ranked] == ["Vercel", "OpenEvidence", "Manual Co"]
    assert ranked[0]["method"] == "greenhouse"
    assert ranked[1]["antibot_history"] is True


def test_build_learning_report_summarizes_friction_and_actions():
    mod = _load_learning_module()
    arms = {
        "method:ashby": {
            "name": "method:ashby",
            "alpha": 1.5,
            "beta": 4.5,
            "pulls": 4,
            "total_reward": 0.5,
        },
        "method:greenhouse": {
            "name": "method:greenhouse",
            "alpha": 4.0,
            "beta": 2.0,
            "pulls": 4,
            "total_reward": 3.0,
        },
        "cat:ai": {
            "name": "cat:ai",
            "alpha": 2.0,
            "beta": 3.0,
            "pulls": 3,
            "total_reward": 1.0,
        },
        "cat:platform": {
            "name": "cat:platform",
            "alpha": 3.0,
            "beta": 1.0,
            "pulls": 2,
            "total_reward": 2.0,
        },
    }
    rows = [
        {
            "Company": "OpenEvidence",
            "Role": "Software Engineer",
            "Status": "ReadyToSubmit",
            "Career Page URL": "https://jobs.ashbyhq.com/openevidence/abc",
            "Tags": "ai",
            "Submission Lane": "ci_auto:ashby",
            "Notes": "Manual rescue evidence: applications/openevidence/submissions/confirm.png. recaptcha_score_below_threshold",
        },
        {
            "Company": "Vercel",
            "Role": "Support Engineer",
            "Status": "Draft",
            "Career Page URL": "https://job-boards.greenhouse.io/vercel/jobs/xyz",
            "Tags": "platform",
            "Submission Lane": "ci_auto:greenhouse",
            "Notes": "",
        },
    ]

    report = mod.build_learning_report(
        rows,
        arms,
        top_k=5,
        tracker_csv="tracker.csv",
        arms_json="arms.json",
    )

    assert report["status_counts"]["ReadyToSubmit"] == 1
    assert report["method_summary"][0]["arm"] == "method:greenhouse"
    ashby = next(item for item in report["method_summary"] if item["arm"] == "method:ashby")
    assert ashby["antibot_count"] == 1
    assert report["manual_rescue_queue"][0]["company"] == "OpenEvidence"
    assert any("anti-bot friction" in item for item in report["action_summary"])


def test_load_arms_handles_missing_and_invalid_files(tmp_path):
    mod = _load_learning_module()
    assert mod.load_arms(tmp_path / "missing.json") == {}

    broken = tmp_path / "arms.json"
    broken.write_text("{not-json", encoding="utf-8")
    assert mod.load_arms(broken) == {}

    valid = tmp_path / "ok.json"
    valid.write_text(json.dumps({"method:ashby": {"alpha": 1, "beta": 1}}), encoding="utf-8")
    assert "method:ashby" in mod.load_arms(valid)


def test_generate_learning_report_script_writes_report(tmp_path):
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    tracker = tmp_path / "tracker.csv"
    tracker.write_text(
        "Company,Role,Status,Career Page URL,Tags,Submission Lane,Notes\n"
        "Vercel,Support Engineer,ReadyToSubmit,https://job-boards.greenhouse.io/vercel/jobs/xyz,platform,ci_auto:greenhouse,\n",
        encoding="utf-8",
    )
    arms = tmp_path / "arms.json"
    arms.write_text(
        json.dumps(
            {
                "method:greenhouse": {
                    "name": "method:greenhouse",
                    "alpha": 4.0,
                    "beta": 2.0,
                    "pulls": 4,
                    "total_reward": 3.0,
                },
                "cat:platform": {
                    "name": "cat:platform",
                    "alpha": 3.0,
                    "beta": 1.0,
                    "pulls": 2,
                    "total_reward": 2.0,
                },
            }
        ),
        encoding="utf-8",
    )
    report = tmp_path / "learning.json"
    script_path = root / "scripts" / "generate_learning_report.py"
    spec = importlib.util.spec_from_file_location("generate_learning_report_test_mod", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]

    rc = module.main(
        [
            "--tracker",
            str(tracker),
            "--arms",
            str(arms),
            "--report",
            str(report),
            "--top-k",
            "5",
        ]
    )

    assert rc == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["ready_ranked"][0]["company"] == "Vercel"
