"""Tests for scripts/bootstrap_self_hosted_runner.py."""

from __future__ import annotations

import importlib.util
import io
import sys
import tarfile
from pathlib import Path

import pytest


def _load_module():
    script_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "bootstrap_self_hosted_runner.py"
    )
    spec = importlib.util.spec_from_file_location(
        "bootstrap_self_hosted_runner_test_mod", script_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def _write_tarball(path: Path, entries: list[tuple[tarfile.TarInfo, bytes | None]]) -> None:
    with tarfile.open(path, "w:gz") as tar:
        for info, payload in entries:
            fileobj = io.BytesIO(payload) if payload is not None else None
            tar.addfile(info, fileobj=fileobj)


def test_safe_extract_tarball_extracts_regular_files(tmp_path: Path):
    mod = _load_module()
    archive = tmp_path / "runner.tar.gz"
    runner_dir = tmp_path / "runner"

    file_info = tarfile.TarInfo("bin/run.sh")
    payload = b"#!/bin/sh\necho runner\n"
    file_info.size = len(payload)
    file_info.mode = 0o755
    dir_info = tarfile.TarInfo("bin")
    dir_info.type = tarfile.DIRTYPE
    dir_info.mode = 0o755

    _write_tarball(archive, [(dir_info, None), (file_info, payload)])

    with tarfile.open(archive, "r:gz") as tar:
        mod._safe_extract_tarball(tar, runner_dir)

    target = runner_dir / "bin" / "run.sh"
    assert target.read_bytes() == payload
    assert target.stat().st_mode & 0o777 == 0o755


def test_safe_extract_tarball_rejects_path_traversal(tmp_path: Path):
    mod = _load_module()
    archive = tmp_path / "runner.tar.gz"
    runner_dir = tmp_path / "runner"

    file_info = tarfile.TarInfo("../escape.sh")
    payload = b"echo nope\n"
    file_info.size = len(payload)

    _write_tarball(archive, [(file_info, payload)])

    with tarfile.open(archive, "r:gz") as tar:
        with pytest.raises(RuntimeError, match="Unsafe path in tarball"):
            mod._safe_extract_tarball(tar, runner_dir)


def test_safe_extract_tarball_rejects_unsafe_symlink(tmp_path: Path):
    mod = _load_module()
    archive = tmp_path / "runner.tar.gz"
    runner_dir = tmp_path / "runner"

    link_info = tarfile.TarInfo("bin/latest")
    link_info.type = tarfile.SYMTYPE
    link_info.linkname = "../../outside"

    _write_tarball(archive, [(link_info, None)])

    with tarfile.open(archive, "r:gz") as tar:
        with pytest.raises(RuntimeError, match="Unsafe path in tarball"):
            mod._safe_extract_tarball(tar, runner_dir)
