"""Structured contracts for agent-facing RAG endpoints."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


CONTRACT_RETRIEVE_V1 = "rag.retrieve.v1"
CONTRACT_VERSION = "2026-02-19"

RETRIEVE_ENVELOPE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": [
        "contract",
        "contract_version",
        "provider",
        "generated_at",
        "request",
        "results",
    ],
    "properties": {
        "contract": {"const": CONTRACT_RETRIEVE_V1},
        "contract_version": {"type": "string"},
        "provider": {"type": "string"},
        "generated_at": {"type": "string"},
        "request": {"type": "object"},
        "results": {"type": "array"},
    },
}


class ContractError(ValueError):
    """Raised when a payload does not satisfy a contract."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def build_retrieve_request(
    *,
    query: str,
    k: int,
    status: Optional[str],
    method: Optional[str],
) -> Dict[str, Any]:
    req = {
        "query": (query or "").strip(),
        "k": int(k),
        "status": (status or "").strip() or None,
        "method": (method or "").strip() or None,
    }
    validate_retrieve_request(req)
    return req


def validate_retrieve_request(payload: Dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise ContractError("retrieve request must be an object")

    query = payload.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ContractError("retrieve request query must be a non-empty string")
    if len(query) > 512:
        raise ContractError("retrieve request query exceeds 512 characters")

    k = payload.get("k")
    if not isinstance(k, int):
        raise ContractError("retrieve request k must be an integer")
    if k < 1 or k > 200:
        raise ContractError("retrieve request k must be in [1, 200]")

    for key in ("status", "method"):
        value = payload.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            raise ContractError(f"retrieve request {key} must be a string or null")
        if len(value.strip()) > 120:
            raise ContractError(f"retrieve request {key} exceeds 120 characters")


def _canonicalize_retrieve_item(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "app_id": str(item.get("app_id", "")),
        "company": str(item.get("company", "")),
        "role": str(item.get("role", "")),
        "status": str(item.get("status", "")),
        "method": str(item.get("method", "")),
        "tags": [str(t) for t in item.get("tags", [])],
        "score": round(float(item.get("score", 0.0)), 4),
        "context": str(item.get("context", "")),
        "evidence": [str(e) for e in item.get("evidence", [])],
    }


def validate_retrieve_item(item: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(item, dict):
        raise ContractError("retrieve result item must be an object")
    for key in ("app_id", "company", "role", "status", "method", "context"):
        value = item.get(key)
        if not isinstance(value, str):
            raise ContractError(f"retrieve result item {key} must be a string")

    tags = item.get("tags")
    if not _is_string_list(tags):
        raise ContractError("retrieve result item tags must be a list[str]")
    evidence = item.get("evidence")
    if not _is_string_list(evidence):
        raise ContractError("retrieve result item evidence must be a list[str]")

    score = item.get("score")
    if not isinstance(score, (int, float)):
        raise ContractError("retrieve result item score must be numeric")
    if score < 0:
        raise ContractError("retrieve result item score must be non-negative")

    out = _canonicalize_retrieve_item(item)
    if len(out["context"]) > 320:
        out["context"] = out["context"][:320]
    return out


def validate_retrieve_payload(payload: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(payload, list):
        raise ContractError("retrieve payload must be a list")
    if len(payload) > 200:
        raise ContractError("retrieve payload cannot exceed 200 results")

    return [validate_retrieve_item(item) for item in payload]


def build_retrieve_envelope(
    *,
    request: Dict[str, Any],
    results: List[Dict[str, Any]],
    provider: str,
) -> Dict[str, Any]:
    validate_retrieve_request(request)
    validated = validate_retrieve_payload(results)
    if not isinstance(provider, str) or not provider.strip():
        raise ContractError("provider must be a non-empty string")

    return {
        "contract": CONTRACT_RETRIEVE_V1,
        "contract_version": CONTRACT_VERSION,
        "provider": provider.strip(),
        "generated_at": _utc_now(),
        "request": request,
        "results": validated,
    }
