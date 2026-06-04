"""Real-transcript corpus harvest (design §3). Turns a parsed `Trace` into per-turn `Fixture`s
with DOWNSTREAM-USE gold (a prior chunk is gold iff its file-path entity is re-FETCHED by a call
within W turns, excluding verify-Read-after-Edit). Deterministic; no bge, no LLM. Entities are
extracted from each chunk's producing `ToolCall.args` (recovered via the call_id embedded in the
chunk key)."""
from __future__ import annotations

import os

from context_curator.eval.fixtures import Fixture, FixtureChunk
from context_curator.replay.schema import ToolCall, ToolResult, Trace, UserPrompt

_PATH_ARGS = ("file_path", "notebook_path", "path")        # structured path args (Bash deferred)
_RETRIEVAL = {"read", "grep", "glob", "notebookread"}      # lowercased; re-fetch = needed/absent
_EDIT = {"edit", "write", "multiedit", "notebookedit"}     # edits never generate gold (churn)


def _canon(p: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(p)))


def extract_entities(call: ToolCall) -> set[str]:
    """Canonical absolute file-path entities from a tool call's args. Pattern-only / path-less
    calls yield the empty set."""
    out: set[str] = set()
    for key in _PATH_ARGS:
        v = call.args.get(key)
        if isinstance(v, str) and v:
            out.add(_canon(v))
    return out


def _contains(dir_: str, file_: str) -> bool:
    return file_.startswith(dir_.rstrip(os.sep) + os.sep)


def entities_match(a: set[str], b: set[str]) -> bool:
    """True iff some entity in `a` equals, contains, or is contained by some entity in `b`.
    Empty sets never match (pattern-only Glob / path-less calls)."""
    for x in a:
        for y in b:
            if x == y or _contains(x, y) or _contains(y, x):
                return True
    return False


class _Turn:
    __slots__ = ("index", "prompt", "subtask_id", "calls", "results")

    def __init__(self, index: int, prompt: str, subtask_id: str | None) -> None:
        self.index = index
        self.prompt = prompt
        self.subtask_id = subtask_id
        self.calls: list[ToolCall] = []
        self.results: list[ToolResult] = []


def _segment(trace: Trace) -> list[_Turn]:
    """Split the flat event list into turns delimited by UserPrompt. Events before the first
    UserPrompt are ignored (no task to attribute them to)."""
    turns: list[_Turn] = []
    for ev in trace.events:
        if isinstance(ev, UserPrompt):
            turns.append(_Turn(ev.turn_index, ev.text, ev.subtask_id))
        elif turns and isinstance(ev, ToolCall):
            turns[-1].calls.append(ev)
        elif turns and isinstance(ev, ToolResult):
            turns[-1].results.append(ev)
    return turns


def harvest_trace(trace: Trace, *, w: int = 5, min_candidates: int = 5) -> list[Fixture]:
    """Per-turn fixtures with downstream-use gold (design §3.3). Gold = a prior candidate chunk
    whose entity is re-fetched by a RETRIEVAL call within [T, T+w], excluding a re-fetch of an
    entity that was EDITED earlier in that same turn (verify-after-edit)."""
    turns = _segment(trace)
    call_by_id = {c.call_id: c for t in turns for c in t.calls}

    chunks_before: list[list[tuple[str, str, set[str]]]] = []
    running: list[tuple[str, str, set[str]]] = []
    for t in turns:
        chunks_before.append(list(running))
        for res in t.results:
            producing = call_by_id.get(res.call_id)
            ents = extract_entities(producing) if producing else set()
            running.append((res.call_id, res.content, ents))

    fixtures: list[Fixture] = []
    for i, t in enumerate(turns):
        candidates = chunks_before[i]
        if len(candidates) < min_candidates:
            continue
        refetched: set[str] = set()
        for tw in turns[i: i + w + 1]:
            edited_here: set[str] = set()
            for call in tw.calls:
                ents = extract_entities(call)
                low = call.name.lower()
                if low in _EDIT:
                    edited_here |= ents
                elif low in _RETRIEVAL:
                    for e in ents:
                        if e not in edited_here:
                            refetched.add(e)
        gold = [key for key, _c, ents in candidates
                if ents and any(entities_match({e}, ents) for e in refetched)]
        if not gold:
            continue
        fixtures.append(Fixture(
            name=f"{trace.session_id}:t{t.index}",
            chunks=[FixtureChunk(key=k, content=c) for k, c, _e in candidates],
            prompt=t.prompt, recent_tools=[], gold_keys=gold,
            split="train", session_id=trace.session_id,
        ))
    return fixtures
