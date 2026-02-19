"""Tests for structured retrieval contracts and adapters."""

import json

import pytest

from contracts import (
    CONTRACT_RETRIEVE_V1,
    ContractError,
    build_retrieve_envelope,
    build_retrieve_request,
    validate_retrieve_item,
    validate_retrieve_payload,
)
from structured_adapter import get_structured_adapter


class TestRetrieveContracts:
    def test_build_retrieve_request(self):
        req = build_retrieve_request(
            query="  ml engineer  ",
            k=7,
            status=" Applied ",
            method=" ashby ",
        )
        assert req["query"] == "ml engineer"
        assert req["k"] == 7
        assert req["status"] == "Applied"
        assert req["method"] == "ashby"

    def test_build_retrieve_request_rejects_empty_query(self):
        with pytest.raises(ContractError, match="non-empty"):
            build_retrieve_request(query="  ", k=5, status=None, method=None)

    def test_validate_retrieve_item(self):
        item = validate_retrieve_item(
            {
                "app_id": "a1",
                "company": "Acme",
                "role": "ML Engineer",
                "status": "Applied",
                "method": "ashby",
                "tags": ["ai", "ml"],
                "score": 0.12349,
                "context": "company=Acme role=ML Engineer",
                "evidence": ["applications/acme/submissions/confirm.png"],
            }
        )
        assert item["score"] == 0.1235
        assert item["company"] == "Acme"

    def test_validate_retrieve_payload_rejects_bad_tags(self):
        with pytest.raises(ContractError, match="tags"):
            validate_retrieve_payload(
                [
                    {
                        "app_id": "a1",
                        "company": "Acme",
                        "role": "ML Engineer",
                        "status": "Applied",
                        "method": "ashby",
                        "tags": "ai;ml",
                        "score": 0.2,
                        "context": "x",
                        "evidence": [],
                    }
                ]
            )

    def test_build_retrieve_envelope(self):
        req = build_retrieve_request(
            query="backend remote",
            k=3,
            status=None,
            method=None,
        )
        payload = [
            {
                "app_id": "a1",
                "company": "Acme",
                "role": "Backend Engineer",
                "status": "Applied",
                "method": "direct",
                "tags": ["backend"],
                "score": 0.7,
                "context": "company=Acme role=Backend Engineer",
                "evidence": [],
            }
        ]
        env = build_retrieve_envelope(request=req, results=payload, provider="local")
        assert env["contract"] == CONTRACT_RETRIEVE_V1
        assert env["request"]["query"] == "backend remote"
        assert len(env["results"]) == 1


class TestStructuredAdapter:
    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown provider"):
            get_structured_adapter("unknown-provider")

    def test_render_envelope_json(self):
        adapter = get_structured_adapter("local")
        req = adapter.normalize_retrieve_request(
            query="ml",
            k=1,
            status=None,
            method=None,
        )
        out = adapter.render_retrieve_json(
            request=req,
            results=[
                {
                    "app_id": "a1",
                    "company": "Acme",
                    "role": "ML Engineer",
                    "status": "Applied",
                    "method": "ashby",
                    "tags": ["ai"],
                    "score": 0.9,
                    "context": "company=Acme role=ML Engineer",
                    "evidence": [],
                }
            ],
            envelope=True,
        )
        payload = json.loads(out)
        assert payload["contract"] == CONTRACT_RETRIEVE_V1
        assert payload["provider"] == "local_fusion_v1"
