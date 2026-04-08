"""Tests for scripts/mercor_cdp_capture.py."""

from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path

import pytest


def _load_module():
    script_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "mercor_cdp_capture.py"
    )
    spec = importlib.util.spec_from_file_location(
        "mercor_cdp_capture_test_mod", script_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def test_validate_local_debug_url_rejects_non_http_scheme():
    mod = _load_module()
    with pytest.raises(ValueError, match="Unsupported debug endpoint scheme"):
        mod._validate_local_debug_url("file:///tmp/debug.json")


def test_validate_local_debug_url_rejects_non_localhost_host():
    mod = _load_module()
    with pytest.raises(ValueError, match="localhost debug endpoints"):
        mod._validate_local_debug_url("https://example.com/json/list")


def test_pick_page_target_prefers_matching_host(monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(
        mod,
        "_json_get",
        lambda _: [
            {"url": "https://other.example/app", "webSocketDebuggerUrl": "ws://one"},
            {
                "url": "https://work.mercor.com/application",
                "webSocketDebuggerUrl": "ws://two",
            },
        ],
    )

    target = mod._pick_page_target(9222, "work.mercor.com")
    assert target["webSocketDebuggerUrl"] == "ws://two"  # nosec B101


def test_pick_page_target_falls_back_to_first_page(monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(
        mod,
        "_json_get",
        lambda _: [
            {"url": "", "webSocketDebuggerUrl": "ws://skip"},
            {"url": "https://example.com/app", "webSocketDebuggerUrl": "ws://ok"},
        ],
    )

    target = mod._pick_page_target(9222, "work.mercor.com")
    assert target["webSocketDebuggerUrl"] == "ws://ok"  # nosec B101


def test_pick_page_target_raises_when_no_page_targets(monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod, "_json_get", lambda _: [{"url": "", "id": "tab-1"}])

    with pytest.raises(RuntimeError, match="No usable CDP page targets found"):
        mod._pick_page_target(9222, "work.mercor.com")


def test_json_get_loads_json_from_localhost(monkeypatch):
    mod = _load_module()

    class FakeResponse:
        def __enter__(self):
            return io.StringIO('[{"id":"tab-1"}]')

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *_args, **_kwargs: FakeResponse())

    payload = mod._json_get("http://localhost:9222/json/list")
    assert payload == [{"id": "tab-1"}]  # nosec B101


def test_main_uses_explicit_tab_id_and_reports_submission_hint(
    monkeypatch, tmp_path, capsys
):
    mod = _load_module()
    out_path = tmp_path / "confirmation.png"

    monkeypatch.setattr(
        mod,
        "_json_get",
        lambda _: [
            {
                "id": "tab-1",
                "url": "https://work.mercor.com/application",
                "webSocketDebuggerUrl": "ws://mercor",
            }
        ],
    )

    async def fake_capture(ws_url, url, out, **kwargs):
        assert ws_url == "ws://mercor"  # nosec B101
        assert url == "https://work.mercor.com/apply"  # nosec B101
        assert out == out_path.resolve()  # nosec B101
        out.write_bytes(b"png")
        return "Your application has been submitted"

    monkeypatch.setattr(mod, "_cdp_capture", fake_capture)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mercor_cdp_capture.py",
            "--tab-id",
            "tab-1",
            "--url",
            "https://work.mercor.com/apply",
            "--out",
            str(out_path),
        ],
    )

    mod.main()
    output = capsys.readouterr().out
    assert "STATUS=SUBMITTED" in output  # nosec B101


@pytest.mark.parametrize(
    ("body_text", "expected_status"),
    [
        ("application submitted", "STATUS=SUBMITTED_MAYBE"),
        ("continue application", "STATUS=IN_PROGRESS"),
        ("mercor sign in", "STATUS=NOT_LOGGED_IN"),
        ("something else", "STATUS=UNKNOWN"),
    ],
)
def test_main_reports_non_final_status_hints(
    monkeypatch, tmp_path, capsys, body_text, expected_status
):
    mod = _load_module()
    out_path = tmp_path / "confirmation.png"
    monkeypatch.setattr(
        mod,
        "_pick_page_target",
        lambda _port, _host: {
            "url": "https://work.mercor.com/application",
            "webSocketDebuggerUrl": "ws://mercor",
        },
    )

    async def fake_capture(*_args, **_kwargs):
        out_path.write_bytes(b"png")
        return body_text

    monkeypatch.setattr(mod, "_cdp_capture", fake_capture)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mercor_cdp_capture.py",
            "--url",
            "https://work.mercor.com/apply",
            "--out",
            str(out_path),
        ],
    )

    mod.main()
    output = capsys.readouterr().out
    assert expected_status in output  # nosec B101


def test_main_rejects_target_without_websocket(monkeypatch, tmp_path):
    mod = _load_module()
    monkeypatch.setattr(
        mod,
        "_pick_page_target",
        lambda _port, _host: {"url": "https://work.mercor.com/application"},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mercor_cdp_capture.py",
            "--url",
            "https://work.mercor.com/apply",
            "--out",
            str(tmp_path / "confirmation.png"),
        ],
    )

    with pytest.raises(SystemExit, match="missing webSocketDebuggerUrl"):
        mod.main()


def test_main_rejects_unknown_tab_id(monkeypatch, tmp_path):
    mod = _load_module()
    monkeypatch.setattr(mod, "_json_get", lambda _: [])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mercor_cdp_capture.py",
            "--tab-id",
            "missing",
            "--url",
            "https://work.mercor.com/apply",
            "--out",
            str(tmp_path / "confirmation.png"),
        ],
    )

    with pytest.raises(SystemExit, match="Tab id not found: missing"):
        mod.main()
