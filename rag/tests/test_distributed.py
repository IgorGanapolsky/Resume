"""Tests for distributed.py runtime helper behavior."""

import pytest

from distributed import create_runtime


def test_runtime_off_mode():
    rt = create_runtime(mode="off")
    assert rt.enabled is False
    assert rt.world_size == 1
    assert rt.is_leader is True


def test_runtime_auto_single_process(monkeypatch):
    monkeypatch.delenv("WORLD_SIZE", raising=False)
    monkeypatch.delenv("RANK", raising=False)
    monkeypatch.delenv("LOCAL_RANK", raising=False)
    rt = create_runtime(mode="auto")
    assert rt.enabled is False
    assert rt.reason


def test_runtime_on_without_world_size_fails(monkeypatch):
    monkeypatch.setenv("WORLD_SIZE", "1")
    monkeypatch.setenv("RANK", "0")
    monkeypatch.setenv("LOCAL_RANK", "0")
    with pytest.raises(RuntimeError, match="WORLD_SIZE<=1"):
        create_runtime(mode="on")


def test_runtime_invalid_mode_raises():
    with pytest.raises(ValueError, match="Unknown dist mode"):
        create_runtime(mode="banana")
