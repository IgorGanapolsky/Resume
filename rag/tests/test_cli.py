"""Integration tests for cli.py commands."""

import csv
import json

import pytest

from tests.conftest import SAMPLE_ROWS


class TestBuild:
    def test_creates_jsonl(self, isolated_cli):
        isolated_cli.build()
        apps_path = isolated_cli.DATA_DIR / "applications.jsonl"
        assert apps_path.exists()
        records = [
            json.loads(line)
            for line in apps_path.read_text().splitlines()
            if line.strip()
        ]
        assert len(records) == 3

    def test_records_have_required_fields(self, isolated_cli):
        isolated_cli.build()
        apps_path = isolated_cli.DATA_DIR / "applications.jsonl"
        for line in apps_path.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            for field in [
                "app_id",
                "company",
                "role",
                "status",
                "application_method",
                "tags",
                "artifacts",
                "updated_at",
            ]:
                assert field in rec, f"Missing field {field!r} in {rec.get('company')}"

    def test_deduplicates_identical_rows(self, isolated_cli, tmp_path):
        """Duplicate rows with same company/role/url produce only one record."""
        dup_row = SAMPLE_ROWS[0].copy()
        path = tmp_path / "dup_tracker.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(SAMPLE_ROWS[0].keys()))
            w.writeheader()
            w.writerow(dup_row)
            w.writerow(dup_row)  # exact duplicate

        import cli as cli_mod

        cli_mod.TRACKER_CSV = path
        cli_mod.build()

        apps_path = cli_mod.DATA_DIR / "applications.jsonl"
        records = [
            json.loads(line)
            for line in apps_path.read_text().splitlines()
            if line.strip()
        ]
        assert len(records) == 1

    def test_application_method_inferred(self, isolated_cli):
        isolated_cli.build()
        apps_path = isolated_cli.DATA_DIR / "applications.jsonl"
        records = {
            json.loads(line)["company"]: json.loads(line)
            for line in apps_path.read_text().splitlines()
            if line.strip()
        }
        assert records["Acme AI"]["application_method"] == "ashby"
        assert records["Beta Corp"]["application_method"] == "lever"

    def test_status_normalized(self, isolated_cli):
        isolated_cli.build()
        apps_path = isolated_cli.DATA_DIR / "applications.jsonl"
        records = [
            json.loads(line)
            for line in apps_path.read_text().splitlines()
            if line.strip()
        ]
        statuses = {r["company"]: r["status"] for r in records}
        assert statuses["Acme AI"] == "Applied"
        assert statuses["Beta Corp"] == "Draft"
        assert statuses["Gamma Infra"] == "Blocked"

    def test_skips_empty_rows(self, isolated_cli, tmp_path):
        path = tmp_path / "sparse_tracker.csv"
        fieldnames = list(SAMPLE_ROWS[0].keys())
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerow(SAMPLE_ROWS[0])
            w.writerow({k: "" for k in fieldnames})  # blank row
        isolated_cli.TRACKER_CSV = path
        isolated_cli.build()

        apps_path = isolated_cli.DATA_DIR / "applications.jsonl"
        records = [
            json.loads(line)
            for line in apps_path.read_text().splitlines()
            if line.strip()
        ]
        assert len(records) == 1

    def test_empty_tracker_creates_empty_lancedb_table(self, isolated_cli, tmp_path):
        path = tmp_path / "empty_tracker.csv"
        fieldnames = list(SAMPLE_ROWS[0].keys())
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

        isolated_cli.TRACKER_CSV = path
        isolated_cli.build()

        apps_path = isolated_cli.DATA_DIR / "applications.jsonl"
        assert apps_path.exists()
        assert apps_path.read_text().strip() == ""

        if isolated_cli.lancedb is not None:
            db = isolated_cli.lancedb.connect(str(isolated_cli.LANCEDB_DIR))
            table = db.open_table("applications")
            assert table.count_rows() == 0

    def test_bootstraps_thompson_model(self, isolated_cli):
        isolated_cli.build()
        arms_path = isolated_cli.ARMS_JSON
        assert arms_path.exists()
        arms = json.loads(arms_path.read_text())
        # Applied record has tags ai;remote;ml → at least those arms
        assert any(k.startswith("cat:") for k in arms)
        assert any(k.startswith("method:") for k in arms)

    def test_creates_events_log_on_build(self, isolated_cli):
        isolated_cli.build()
        log_path = isolated_cli.LOG_DIR / "events.jsonl"
        assert log_path.exists()


class TestStatus:
    def test_shows_counts(self, isolated_cli, capsys):
        isolated_cli.build()
        isolated_cli.status()
        out = capsys.readouterr().out
        assert "Applied" in out
        assert "Draft" in out
        assert "Blocked" in out

    def test_shows_pending_drafts(self, isolated_cli, capsys):
        isolated_cli.build()
        isolated_cli.status()
        out = capsys.readouterr().out
        assert "Beta Corp" in out

    def test_no_index_prints_helpful_message(self, isolated_cli, capsys):
        isolated_cli.status()
        out = capsys.readouterr().out
        assert "build" in out.lower()


class TestQuery:
    def test_query_prints_app_id_and_score(self, isolated_cli, capsys):
        if isolated_cli.lancedb is None:
            pytest.skip("lancedb not installed")

        isolated_cli.build()
        apps_path = isolated_cli.DATA_DIR / "applications.jsonl"
        first_app_id = json.loads(apps_path.read_text().splitlines()[0])["app_id"]

        isolated_cli.query("senior ml engineer", k=3)
        out = capsys.readouterr().out
        assert first_app_id in out
        assert "score=" in out

    def test_rrf_fuse_promotes_consensus_results(self, isolated_cli):
        vector_rows = [{"app_id": "a"}, {"app_id": "b"}]
        lexical_rows = [{"app_id": "b"}, {"app_id": "c"}]

        ranked = isolated_cli._rrf_fuse(vector_rows, lexical_rows, rrf_k=10)
        assert ranked[0]["app_id"] == "b"
        assert ranked[0]["_hybrid_score"] > ranked[1]["_hybrid_score"]


class TestLogEvent:
    def test_appends_to_events_jsonl(self, isolated_cli):
        isolated_cli.build()
        isolated_cli.log_event(
            "test_app_id", "follow_up", "Pinged recruiter on LinkedIn"
        )
        log_path = isolated_cli.LOG_DIR / "events.jsonl"
        events = [
            json.loads(line)
            for line in log_path.read_text().splitlines()
            if line.strip()
        ]
        follow_ups = [e for e in events if e["type"] == "follow_up"]
        assert len(follow_ups) == 1
        assert follow_ups[0]["app_id"] == "test_app_id"

    def test_event_fields_present(self, isolated_cli):
        isolated_cli.build()
        isolated_cli.log_event("id_x", "note", "Test note")
        log_path = isolated_cli.LOG_DIR / "events.jsonl"
        events = [
            json.loads(line)
            for line in log_path.read_text().splitlines()
            if line.strip()
        ]
        last = events[-1]
        assert "ts" in last
        assert "type" in last
        assert "msg" in last


class TestFeedback:
    def _get_app_id(self, isolated_cli) -> str:
        apps_path = isolated_cli.DATA_DIR / "applications.jsonl"
        records = [
            json.loads(line)
            for line in apps_path.read_text().splitlines()
            if line.strip()
        ]
        return records[0]["app_id"]

    def test_records_outcome_updates_arms(self, isolated_cli):
        isolated_cli.build()
        app_id = self._get_app_id(isolated_cli)

        from rlhf import ThompsonModel

        model_before = ThompsonModel(isolated_cli.ARMS_JSON)
        pulls_before = sum(a.pulls for a in model_before.arms.values())

        isolated_cli.feedback(app_id, "interview")

        model_after = ThompsonModel(isolated_cli.ARMS_JSON)
        pulls_after = sum(a.pulls for a in model_after.arms.values())
        assert pulls_after > pulls_before

    def test_invalid_outcome_raises(self, isolated_cli):
        isolated_cli.build()
        app_id = self._get_app_id(isolated_cli)
        with pytest.raises(SystemExit, match="Unknown outcome"):
            isolated_cli.feedback(app_id, "moon_landing")

    def test_unknown_app_id_raises(self, isolated_cli):
        isolated_cli.build()
        with pytest.raises(SystemExit, match="not found"):
            isolated_cli.feedback("nonexistent__id__000", "response")

    def test_no_index_raises(self, isolated_cli):
        with pytest.raises(SystemExit, match="build"):
            isolated_cli.feedback("any_id", "response")


class TestRecommend:
    def test_recommend_after_build(self, isolated_cli, capsys):
        isolated_cli.build()
        isolated_cli.recommend(k=5)
        out = capsys.readouterr().out
        assert (
            "Thompson" in out
            or "arm" in out.lower()
            or "cat:" in out
            or "method:" in out
        )

    def test_recommend_no_data_prints_message(self, isolated_cli, capsys):
        isolated_cli.recommend()
        out = capsys.readouterr().out
        assert "build" in out.lower() or "no" in out.lower()


class TestEmbedding:
    def test_embedding_shape(self, isolated_cli):
        import numpy as np

        vec = isolated_cli._hashing_embedding("react native mobile engineer")
        assert vec.shape == (1536,)
        assert vec.dtype == np.float32

    def test_embedding_normalized(self, isolated_cli):
        import numpy as np

        vec = isolated_cli._hashing_embedding("some text here")
        norm = float(np.linalg.norm(vec))
        assert abs(norm - 1.0) < 1e-5

    def test_different_texts_differ(self, isolated_cli):
        import numpy as np

        v1 = isolated_cli._hashing_embedding("react native mobile")
        v2 = isolated_cli._hashing_embedding("kubernetes infrastructure devops")
        sim = float(np.dot(v1, v2))
        assert sim < 0.9  # Not near-identical

    def test_bigrams_captured(self, isolated_cli):
        import numpy as np

        # "react native" as bigram should make these more similar than random text
        v1 = isolated_cli._hashing_embedding("react native developer")
        v2 = isolated_cli._hashing_embedding("react native engineer")
        v3 = isolated_cli._hashing_embedding("kubernetes cloud platform")
        sim_related = float(np.dot(v1, v2))
        sim_unrelated = float(np.dot(v1, v3))
        assert sim_related > sim_unrelated
