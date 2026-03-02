#!/usr/bin/env python3
"""Identity Layer for AI Agents.

Provides cryptographic signing and verification for agent-generated artifacts
to establish 'Entity Home' trust and bypass bot-detection heuristics.
"""

import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Dict, Any

ROOT = Path(__file__).resolve().parents[1]
AGENT_SECRET_PATH = ROOT / ".claude" / "agent_identity.key"


def get_or_create_agent_key() -> bytes:
    """Retrieves or generates a unique cryptographic key for this agent instance."""
    if AGENT_SECRET_PATH.exists():
        return AGENT_SECRET_PATH.read_bytes()

    # Generate a fresh 32-byte key
    key = os.urandom(32)
    AGENT_SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
    AGENT_SECRET_PATH.write_bytes(key)
    # Set restrictive permissions
    os.chmod(AGENT_SECRET_PATH, 0o600)
    return key


def sign_artifact(data: Dict[str, Any]) -> str:
    """Generates an HMAC-SHA256 signature for a JSON artifact."""
    key = get_or_create_agent_key()
    # Ensure deterministic serialization
    payload = json.dumps(data, sort_keys=True).encode("utf-8")
    signature = hmac.new(key, payload, hashlib.sha256).hexdigest()
    return signature


def verify_artifact(data: Dict[str, Any], signature: str) -> bool:
    """Verifies that an artifact was signed by this agent instance."""
    expected = sign_artifact(data)
    return hmac.compare_digest(expected, signature)


def inject_identity(jsonld: Dict[str, Any]) -> Dict[str, Any]:
    """Injects a verifiable agent signature into a JSON-LD structure."""
    signed_copy = jsonld.copy()
    # Remove existing signature if present to avoid circular dependency
    signed_copy.pop("agent_signature", None)

    sig = sign_artifact(signed_copy)
    jsonld["agent_signature"] = {
        "@type": "AgentSignature",
        "value": sig,
        "algorithm": "HMAC-SHA256",
        "issuance_date": jsonld.get("date_generated", ""),
    }
    return jsonld


if __name__ == "__main__":
    # Smoke test
    test_data = {"test": "data", "date_generated": "2026-03-01"}
    signature = sign_artifact(test_data)
    assert verify_artifact(test_data, signature)
    print(f"Agent Identity verified. Signature: {signature}")
