"""Tests for rlhf.py — Thompson Sampling RLHF engine."""

import json
import random

import pytest

from rlhf import Arm, ThompsonModel, OUTCOME_REWARDS, VALID_OUTCOMES


class TestArm:
    def test_initial_state(self):
        arm = Arm(name="cat:ai")
        assert arm.alpha == 1.0
        assert arm.beta == 1.0
        assert arm.pulls == 0
        assert arm.total_reward == 0.0

    def test_mean_reward_uniform_prior(self):
        arm = Arm(name="cat:ai")
        assert arm.mean_reward == pytest.approx(0.5)

    def test_update_high_reward(self):
        arm = Arm(name="cat:ai")
        arm.update(1.0)
        assert arm.alpha > 1.0
        assert arm.beta == pytest.approx(1.0)
        assert arm.pulls == 1
        assert arm.total_reward == pytest.approx(1.0)

    def test_update_zero_reward(self):
        arm = Arm(name="cat:ai")
        arm.update(0.0)
        assert arm.alpha == pytest.approx(1.0)
        assert arm.beta > 1.0

    def test_update_clips_to_unit_interval(self):
        arm = Arm(name="cat:ai")
        arm.update(2.0)   # above 1.0 → clipped
        arm.update(-1.0)  # below 0.0 → clipped
        assert arm.alpha <= 3.0
        assert arm.beta <= 3.0

    def test_sample_returns_float_in_unit_interval(self):
        arm = Arm(name="cat:ai", alpha=2.0, beta=5.0)
        for _ in range(50):
            s = arm.sample()
            assert 0.0 <= s <= 1.0

    def test_mean_reward_converges_with_high_rewards(self):
        arm = Arm(name="cat:ai")
        for _ in range(100):
            arm.update(1.0)
        assert arm.mean_reward > 0.95


class TestThompsonModel:
    def test_empty_model_returns_no_arms(self, tmp_path):
        model = ThompsonModel(tmp_path / "arms.json")
        assert model.arms == {}
        assert model.recommend() == []
        assert model.stats() == []

    def test_record_outcome_creates_arms(self, tmp_path):
        model = ThompsonModel(tmp_path / "arms.json")
        model.record_outcome(["ai", "remote"], "ashby", "response")
        assert "cat:ai" in model.arms
        assert "cat:remote" in model.arms
        assert "method:ashby" in model.arms

    def test_record_outcome_invalid_raises(self, tmp_path):
        model = ThompsonModel(tmp_path / "arms.json")
        with pytest.raises(ValueError, match="Unknown outcome"):
            model.record_outcome(["ai"], "ashby", "flying_unicorn")

    def test_persists_and_reloads(self, tmp_path):
        path = tmp_path / "arms.json"
        m1 = ThompsonModel(path)
        m1.record_outcome(["ai"], "greenhouse", "offer")

        m2 = ThompsonModel(path)
        assert "cat:ai" in m2.arms
        assert m2.arms["cat:ai"].pulls == 1

    def test_corrupt_file_starts_fresh(self, tmp_path):
        path = tmp_path / "arms.json"
        path.write_text("NOT JSON {{{")
        model = ThompsonModel(path)
        assert model.arms == {}

    def test_recommend_returns_top_k(self, tmp_path):
        random.seed(42)
        model = ThompsonModel(tmp_path / "arms.json")
        for tag in ["ai", "remote", "healthcare", "fintech", "mobile"]:
            model.record_outcome([tag], "direct", "response", save=False)
        top = model.recommend(k=3)
        assert len(top) == 3
        assert all(isinstance(name, str) and isinstance(val, float) for name, val in top)

    def test_recommend_sorted_descending(self, tmp_path):
        random.seed(0)
        model = ThompsonModel(tmp_path / "arms.json")
        for tag in ["a", "b", "c"]:
            model.record_outcome([tag], "direct", "offer", save=False)
        top = model.recommend(k=10)
        vals = [v for _, v in top]
        assert vals == sorted(vals, reverse=True)

    def test_stats_sorted_by_mean_reward(self, tmp_path):
        model = ThompsonModel(tmp_path / "arms.json")
        model.record_outcome(["ai"], "ashby", "offer", save=False)
        model.record_outcome(["mobile"], "ashby", "blocked", save=False)
        stats = model.stats()
        means = [s["mean_reward"] for s in stats]
        assert means == sorted(means, reverse=True)

    def test_all_outcome_rewards_covered(self):
        assert set(OUTCOME_REWARDS.keys()) == VALID_OUTCOMES

    @pytest.mark.parametrize("outcome,expected_min,expected_max", [
        ("blocked", 0.0, 0.1),
        ("no_response", 0.0, 0.15),
        ("rejected", 0.1, 0.3),
        ("response", 0.4, 0.6),
        ("interview", 0.7, 0.9),
        ("offer", 1.0, 1.0),
    ])
    def test_reward_ordering(self, outcome, expected_min, expected_max):
        r = OUTCOME_REWARDS[outcome]
        assert expected_min <= r <= expected_max, f"{outcome}: {r} not in [{expected_min}, {expected_max}]"

    def test_bootstrap_from_records(self, tmp_path):
        records = [
            {"status": "Applied", "tags": ["ai", "remote"], "application_method": "ashby"},
            {"status": "Blocked", "tags": ["infra"], "application_method": "ashby"},
            {"status": "Draft", "tags": ["mobile"], "application_method": "lever"},
        ]
        model = ThompsonModel(tmp_path / "arms.json")
        model.bootstrap_from_records(records)

        # Applied and Blocked get recorded; Draft is skipped
        assert "cat:ai" in model.arms
        assert "cat:infra" in model.arms
        assert "cat:mobile" not in model.arms

    def test_save_no_error_when_dir_missing(self, tmp_path):
        path = tmp_path / "nested" / "deep" / "arms.json"
        model = ThompsonModel(path)
        model.record_outcome(["ai"], "direct", "response")
        assert path.exists()
