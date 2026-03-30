#!/usr/bin/env python3
"""Shared candidate profile loader for local automation scripts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_PROFILE_JSON = (
    ROOT / "applications" / "job_applications" / "candidate_profile.json"
)


def load_candidate_profile(path: Path = CANDIDATE_PROFILE_JSON) -> Dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid candidate profile payload: {path}")

    profile = {str(key): str(value).strip() for key, value in payload.items()}
    required = ("first_name", "last_name", "email", "phone")
    missing = [key for key in required if not profile.get(key, "").strip()]
    if missing:
        raise RuntimeError(
            f"Candidate profile missing required fields: {', '.join(missing)}"
        )

    profile.setdefault(
        "full_name",
        f"{profile['first_name']} {profile['last_name']}".strip(),
    )
    profile.setdefault("address", profile.get("address_line1", "").strip())
    if not profile.get("full_address", "").strip():
        parts = [
            profile.get("address_line1", "").strip(),
            ", ".join(
                part
                for part in [
                    profile.get("city", "").strip(),
                    profile.get("state", "").strip(),
                ]
                if part
            ).strip(", "),
            profile.get("postal_code", "").strip(),
        ]
        profile["full_address"] = ", ".join(part for part in parts if part)
    profile["phone_digits"] = re.sub(r"\D+", "", profile["phone"])
    profile["location_short"] = ", ".join(
        part
        for part in [
            profile.get("city", "").strip(),
            profile.get("state", "").strip(),
        ]
        if part
    )
    return profile
