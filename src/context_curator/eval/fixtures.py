"""Eval fixtures (design §3.2). A fixture = chunks (chronological, oldest-first) + a
task + planted (blind-labeled) gold keys. `pin` is intentionally absent."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel


class FixtureChunk(BaseModel):
    model_config = {"extra": "forbid"}   # reject a stray `pin` (M3)
    key: str
    content: str
    tags: list[str] = []


class Fixture(BaseModel):
    name: str
    chunks: list[FixtureChunk]            # CHRONOLOGICAL: oldest first, newest last
    prompt: str
    recent_tools: list[str] = []
    gold_keys: list[str]
    split: Literal["train", "test"] = "train"


def load_fixtures(directory: str) -> list[Fixture]:
    out: list[Fixture] = []
    for path in sorted(Path(directory).glob("*.json")):
        out.append(Fixture(**json.loads(path.read_text(encoding="utf-8"))))
    return out
