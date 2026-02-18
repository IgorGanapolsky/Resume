"""Thompson Sampling RLHF engine for job applications.

Each "arm" is a (category, method) pair derived from application tags and ATS.
Reward signals come from recorded outcomes (response, interview, offer, etc.).

Usage:
    model = ThompsonModel(Path("data/arms.json"))
    model.record_outcome(["ai", "remote"], "ashby", "response")
    model.recommend(k=5)
    model.stats()
"""

import json
import math
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# Reward values for each outcome type. Kept in [0, 1] so Beta updates are bounded.
OUTCOME_REWARDS: Dict[str, float] = {
    "blocked": 0.0,       # ATS spam block — no signal beyond method friction
    "no_response": 0.05,  # Applied but heard nothing (small positive: at least not bounced)
    "rejected": 0.2,      # Got to review; some engagement
    "response": 0.5,      # Recruiter reached out
    "interview": 0.8,     # Technical interview scheduled
    "offer": 1.0,         # Offer received
}

VALID_OUTCOMES = frozenset(OUTCOME_REWARDS)


@dataclass
class Arm:
    name: str
    alpha: float = 1.0   # Beta prior: pseudo-successes + 1
    beta: float = 1.0    # Beta prior: pseudo-failures + 1
    pulls: int = 0
    total_reward: float = 0.0

    def sample(self) -> float:
        """Draw one Thompson sample from Beta(alpha, beta)."""
        return random.betavariate(self.alpha, self.beta)

    def update(self, reward: float) -> None:
        """Update arm with a reward in [0, 1]."""
        reward = max(0.0, min(1.0, reward))
        self.alpha += reward
        self.beta += 1.0 - reward
        self.pulls += 1
        self.total_reward += reward

    @property
    def mean_reward(self) -> float:
        """Posterior mean of the Beta distribution."""
        return self.alpha / (self.alpha + self.beta)

    @property
    def confidence(self) -> float:
        """UCB-style confidence bonus (decreases with more pulls)."""
        if self.pulls == 0:
            return 1.0
        return math.sqrt(2.0 * math.log(max(1, self.pulls + 1)) / (self.pulls + 1))


class ThompsonModel:
    """Persisted Thompson Sampling model over application arms.

    Arms have the form:
        "cat:<tag>"     — e.g. "cat:ai", "cat:remote", "cat:healthcare"
        "method:<ats>"  — e.g. "method:ashby", "method:greenhouse"
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.arms: Dict[str, Arm] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for name, d in data.items():
                self.arms[name] = Arm(**d)
        except Exception:
            pass  # Corrupt file → start fresh; will overwrite on next save

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({k: asdict(v) for k, v in self.arms.items()}, indent=2),
            encoding="utf-8",
        )

    def _get_or_create(self, arm_name: str) -> Arm:
        if arm_name not in self.arms:
            self.arms[arm_name] = Arm(name=arm_name)
        return self.arms[arm_name]

    def record_outcome(
        self,
        tags: List[str],
        method: str,
        outcome: str,
        *,
        save: bool = True,
    ) -> None:
        """Record an outcome and update relevant arms.

        Args:
            tags:    Application tags (e.g. ["ai", "remote", "healthcare"]).
            method:  Application method (e.g. "ashby", "greenhouse", "direct").
            outcome: One of VALID_OUTCOMES.
            save:    Whether to persist arms.json immediately.
        """
        if outcome not in VALID_OUTCOMES:
            raise ValueError(
                f"Unknown outcome {outcome!r}. Valid: {sorted(VALID_OUTCOMES)}"
            )
        reward = OUTCOME_REWARDS[outcome]

        for tag in tags:
            self._get_or_create(f"cat:{tag}").update(reward)

        self._get_or_create(f"method:{method}").update(reward)

        if save:
            self.save()

    def recommend(self, *, k: int = 5) -> List[Tuple[str, float]]:
        """Return top-k arms by Thompson sample (exploration-aware).

        Returns list of (arm_name, sampled_value) sorted descending.
        """
        if not self.arms:
            return []
        sampled = [(name, arm.sample()) for name, arm in self.arms.items()]
        sampled.sort(key=lambda x: x[1], reverse=True)
        return sampled[:k]

    def stats(self) -> List[Dict]:
        """Return arm statistics sorted by mean reward descending."""
        rows = []
        for name, arm in self.arms.items():
            rows.append(
                {
                    "arm": name,
                    "pulls": arm.pulls,
                    "mean_reward": round(arm.mean_reward, 3),
                    "alpha": round(arm.alpha, 2),
                    "beta": round(arm.beta, 2),
                    "total_reward": round(arm.total_reward, 2),
                }
            )
        rows.sort(key=lambda r: r["mean_reward"], reverse=True)
        return rows

    def bootstrap_from_records(
        self, records: List[Dict], *, save: bool = True
    ) -> None:
        """Seed Thompson model from historical application records in JSONL.

        For Applied/Blocked records we have partial signals; Draft/Closed are skipped.
        """
        status_to_outcome = {
            "Applied": "no_response",   # Optimistic baseline until real feedback
            "Blocked": "blocked",
            "Rejected": "rejected",
            "Offer": "offer",
        }
        for rec in records:
            status = rec.get("status", "")
            outcome = status_to_outcome.get(status)
            if outcome is None:
                continue
            tags = rec.get("tags", [])
            method = rec.get("application_method", "direct")
            self.record_outcome(tags, method, outcome, save=False)

        if save:
            self.save()
