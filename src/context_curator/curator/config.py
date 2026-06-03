"""Curator tunables + the runtime file/lock paths (design §5). Env-overridable."""
from __future__ import annotations

import os
from pathlib import Path


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


CURATOR_EMBEDDER = os.environ.get("CC_CURATOR_EMBEDDER", "bge")     # bge | hashing
CURATOR_ONLOAD_ENABLED = os.environ.get("CC_CURATOR_ONLOAD", "0") == "1"   # dark by default
CURATOR_DEBUG = os.environ.get("CC_CURATOR_DEBUG", "0") == "1"

WARMING_DEADLINE_S = _int("CC_CURATOR_WARMING_DEADLINE_S", 15)
PROVISION_DEADLINE_S = _int("CC_CURATOR_PROVISION_DEADLINE_S", 300)
IDLE_TIMEOUT_S = _int("CC_CURATOR_IDLE_TIMEOUT_S", 1800)
RECONCILE_BATCH = _int("CC_CURATOR_RECONCILE_BATCH", 16)
RECONCILE_INTERVAL_S = _int("CC_CURATOR_RECONCILE_INTERVAL_S", 2)
ONDEMAND_EMBED_CAP = _int("CC_CURATOR_ONDEMAND_CAP", 12)
REQUEST_DEADLINE_S = _float("CC_CURATOR_REQUEST_DEADLINE_S", 0.25)
BGE_DIM = 384


def runtime_path(db_path: str) -> Path:
    return Path(db_path).parent / ".curator.json"


def lock_path(db_path: str) -> str:
    return str(Path(db_path).parent / ".curator.lock")
