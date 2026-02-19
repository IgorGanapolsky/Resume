"""Provider adapter for structured RAG payloads."""

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol

from contracts import (
    build_retrieve_envelope,
    build_retrieve_request,
    validate_retrieve_payload,
)


class StructuredAdapter(Protocol):
    name: str

    def normalize_retrieve_request(
        self, *, query: str, k: int, status: Optional[str], method: Optional[str]
    ) -> Dict[str, Any]:
        ...

    def validate_retrieve_results(
        self, results: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        ...

    def render_retrieve_json(
        self,
        *,
        request: Dict[str, Any],
        results: List[Dict[str, Any]],
        envelope: bool,
    ) -> str:
        ...


@dataclass(frozen=True)
class LocalStructuredAdapter:
    """Local adapter with strict contract validation."""

    name: str = "local_fusion_v1"

    def normalize_retrieve_request(
        self, *, query: str, k: int, status: Optional[str], method: Optional[str]
    ) -> Dict[str, Any]:
        return build_retrieve_request(query=query, k=k, status=status, method=method)

    def validate_retrieve_results(
        self, results: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        return validate_retrieve_payload(results)

    def render_retrieve_json(
        self,
        *,
        request: Dict[str, Any],
        results: List[Dict[str, Any]],
        envelope: bool,
    ) -> str:
        validated = self.validate_retrieve_results(results)
        if envelope:
            payload = build_retrieve_envelope(
                request=request, results=validated, provider=self.name
            )
        else:
            payload = validated
        return json.dumps(payload, ensure_ascii=True, indent=2)


def get_structured_adapter(provider: str = "local") -> StructuredAdapter:
    key = (provider or "local").strip().lower()
    if key in {"local", "default", "local_fusion"}:
        return LocalStructuredAdapter()
    raise ValueError(
        f"Unknown provider {provider!r}. Valid providers: local, default, local_fusion."
    )
