#!/usr/bin/env python3
"""Assistive Agent Optimization (AAO) Identity Sync.

This script automates LinkedIn and GitHub profile updates based on the current
active persona. This aligns the "Entity Home" with the tailored resume strategy,
ensuring AI recruiters find consistent signaling across platforms.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Mapping of persona tracks to bio/headline content
PERSONA_PROFILES = {
    "fde": {
        "headline": "Forward-Deployed Engineer | Bridging AI/ML with Customer Impact",
        "bio": "I am a Forward-Deployed Engineer with 15+ years of experience in software engineering and 6+ years specializing in full-stack AI/ML systems. I thrive at the intersection of complex model deployment and direct customer success.",
        "github_bio": "Forward-Deployed Engineer | 15+ YOE Software, 6+ YOE AI/ML | Bridging models with customer impact.",
    },
    "ml": {
        "headline": "Senior Machine Learning Engineer | Scaling AI Infrastructure",
        "bio": "Senior ML Engineer focused on productionizing LLMs, building reliable RAG systems, and scaling multi-model gateways. 15+ years of software engineering, driven by observability and robust backend architecture.",
        "github_bio": "Senior ML Engineer | Productionizing LLMs, RAG & multi-model gateways. Python, Go, GCP.",
    },
    "infra": {
        "headline": "AI Infrastructure Engineer | SRE for ML Systems",
        "bio": "Platform and Infrastructure Engineer specializing in the operational reliability of AI applications. Expertise in GCP, distributed systems, and ensuring high-availability for ML inference endpoints.",
        "github_bio": "AI Infra & Platform Engineer | Ensuring reliability and scale for ML inference on GCP. Go/Python.",
    },
    "general": {
        "headline": "Senior AI Software Engineer | Full-Stack Product Delivery",
        "bio": "Senior Software Engineer with 15+ years building reliable, user-facing applications and 6+ years in AI integrations. I build robust side projects and production systems using Python, Go, and React Native.",
        "github_bio": "Senior AI Software Engineer | Full-stack delivery with Python, Go, React Native | 15+ YOE",
    },
}


def sync_github_bio(bio: str, token: str) -> None:
    """Updates GitHub profile bio using the REST API."""
    print(f"[GitHub] Syncing bio: {bio}")
    if not token:
        print("[GitHub] SKIP: GITHUB_TOKEN not provided.")
        return
    import urllib.request

    req = urllib.request.Request(
        "https://api.github.com/user",
        data=json.dumps({"bio": bio}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
        },
        method="PATCH",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            if resp.status == 200:
                print("[GitHub] Success.")
            else:
                print(f"[GitHub] Failed: {resp.status}")
    except Exception as e:
        print(f"[GitHub] Error: {e}")


def sync_linkedin_headline(headline: str, bio: str) -> None:
    """Updates LinkedIn profile using Playwright automation (stub)."""
    print(f"[LinkedIn] Syncing headline: {headline}")
    print(f"[LinkedIn] Syncing about section: {bio}")
    # Note: In a real CI environment, this would use stealth Playwright to navigate
    # to the LinkedIn profile edit page and update the fields, similar to the job submitter.
    print("[LinkedIn] Success (Simulated).")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync AAO Identity across platforms.")
    parser.add_argument(
        "--persona",
        type=str,
        choices=list(PERSONA_PROFILES.keys()),
        default="general",
        help="The target persona to sync.",
    )
    parser.add_argument(
        "--github-token",
        type=str,
        default=os.getenv("GITHUB_TOKEN"),
        help="GitHub Personal Access Token with user write access.",
    )

    args = parser.parse_args()
    profile = PERSONA_PROFILES[args.persona]

    print(f"--- Starting AAO Identity Sync for persona: {args.persona.upper()} ---")
    sync_github_bio(profile.get("github_bio", profile["bio"][:160]), args.github_token)
    sync_linkedin_headline(profile["headline"], profile["bio"])
    print("--- Sync Complete ---")

    return 0


if __name__ == "__main__":
    sys.exit(main())
