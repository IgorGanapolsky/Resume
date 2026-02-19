"""Lightweight distributed runtime helpers for optional torch.distributed use.

Design goals:
- Safe by default: auto mode falls back to single-process behavior.
- No hard dependency on PyTorch for normal CLI operation.
- Explicit failure in mode='on' when distributed prerequisites are missing.
"""

import os
from dataclasses import dataclass
from typing import Any, List, Optional


@dataclass
class DistRuntime:
    enabled: bool
    mode: str
    backend: str
    rank: int = 0
    world_size: int = 1
    local_rank: int = 0
    reason: str = ""
    _dist: Any = None
    _initialized_here: bool = False

    @property
    def is_leader(self) -> bool:
        return self.rank == 0

    def gather_objects(self, payload: Any, *, dst: int = 0) -> Optional[List[Any]]:
        if not self.enabled:
            return [payload]
        gathered = [None] * self.world_size if self.rank == dst else None
        self._dist.gather_object(payload, gathered, dst=dst)
        return gathered

    def barrier(self) -> None:
        if self.enabled:
            self._dist.barrier()

    def finalize(self) -> None:
        if self.enabled and self._initialized_here:
            self._dist.destroy_process_group()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _import_torch_dist():
    try:
        import torch  # type: ignore
        import torch.distributed as dist  # type: ignore

        return torch, dist
    except Exception:
        return None, None


def create_runtime(
    *,
    mode: str = "auto",
    backend: str = "auto",
    requested_world_size: Optional[int] = None,
) -> DistRuntime:
    """Create distributed runtime context.

    Args:
        mode:
          - "off": always single process.
          - "auto": enable only when launched under distributed env (WORLD_SIZE>1).
          - "on": require distributed; raise if unavailable/misconfigured.
        backend:
          - "auto": choose "nccl" when CUDA available, else "gloo".
          - explicit backend name ("gloo" / "nccl").
        requested_world_size:
          Optional expected world size; if provided in mode='on' and does not match
          environment world size, runtime creation fails.
    """
    mode = (mode or "auto").strip().lower()
    backend = (backend or "auto").strip().lower()
    if mode not in {"off", "auto", "on"}:
        raise ValueError(f"Unknown dist mode: {mode!r}")

    if mode == "off":
        return DistRuntime(
            enabled=False,
            mode=mode,
            backend="none",
            reason="disabled by mode=off",
        )

    env_world_size = _env_int("WORLD_SIZE", 1)
    env_rank = _env_int("RANK", 0)
    env_local_rank = _env_int("LOCAL_RANK", 0)

    if requested_world_size and requested_world_size > 1:
        if env_world_size <= 1 and mode == "on":
            raise RuntimeError(
                "Distributed mode requires torchrun/multi-process launch when "
                "requested world_size > 1."
            )
        if (
            env_world_size > 1
            and env_world_size != requested_world_size
            and mode == "on"
        ):
            raise RuntimeError(
                f"Requested world_size={requested_world_size}, but env WORLD_SIZE={env_world_size}."
            )

    if env_world_size <= 1:
        if mode == "on":
            raise RuntimeError(
                "Distributed mode is on, but WORLD_SIZE<=1. Launch with torchrun."
            )
        return DistRuntime(
            enabled=False,
            mode=mode,
            backend="none",
            reason="single process environment",
        )

    torch, dist = _import_torch_dist()
    if torch is None or dist is None:
        if mode == "on":
            raise RuntimeError("PyTorch distributed is unavailable.")
        return DistRuntime(
            enabled=False,
            mode=mode,
            backend="none",
            reason="torch.distributed unavailable",
        )

    chosen_backend = backend
    if chosen_backend == "auto":
        chosen_backend = "nccl" if torch.cuda.is_available() else "gloo"

    initialized_here = False
    if not dist.is_initialized():
        if chosen_backend == "nccl" and torch.cuda.is_available():
            device_count = max(torch.cuda.device_count(), 1)
            torch.cuda.set_device(env_local_rank % device_count)
        dist.init_process_group(
            backend=chosen_backend, rank=env_rank, world_size=env_world_size
        )
        initialized_here = True

    return DistRuntime(
        enabled=True,
        mode=mode,
        backend=chosen_backend,
        rank=env_rank,
        world_size=env_world_size,
        local_rank=env_local_rank,
        reason="",
        _dist=dist,
        _initialized_here=initialized_here,
    )
