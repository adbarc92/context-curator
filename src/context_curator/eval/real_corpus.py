"""Real-transcript corpus harvest (design §3). Turns a parsed `Trace` into per-turn `Fixture`s
with DOWNSTREAM-USE gold (a prior chunk is gold iff its file-path entity is re-FETCHED by a call
within W turns, excluding verify-Read-after-Edit). Deterministic; no bge, no LLM. Entities are
extracted from each chunk's producing `ToolCall.args` (recovered via the call_id embedded in the
chunk key)."""
from __future__ import annotations

import os
import random
from dataclasses import dataclass

from context_curator.eval.bm25 import bm25_scores
from context_curator.eval.fixtures import Fixture, FixtureChunk
from context_curator.eval.metrics import recall_at_k
from context_curator.replay.capture.transcript import parse_transcript
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

    chunks_before: list[list[tuple[str, str, set[str], str | None]]] = []
    running: list[tuple[str, str, set[str], str | None]] = []
    for t in turns:
        chunks_before.append(list(running))
        for res in t.results:
            producing = call_by_id.get(res.call_id)
            ents = extract_entities(producing) if producing else set()
            running.append((res.call_id, res.content, ents, producing.name if producing else None))

    fixtures: list[Fixture] = []
    for i, t in enumerate(turns):
        candidates = chunks_before[i]
        if len(candidates) < min_candidates:
            continue
        refetched: set[str] = set()
        for tw in turns[i: i + w + 1]:
            # per-turn reset: excludes a SAME-TURN verify-Read-after-Edit only (pinned rule §3.3);
            # a cross-turn re-read of an edited entity still counts as a genuine re-fetch.
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
        gold = [key for key, _c, ents, _tool in candidates
                if ents and any(entities_match({e}, ents) for e in refetched)]
        if not gold:
            continue
        fixtures.append(Fixture(
            name=f"{trace.session_id}:t{t.index}",
            chunks=[FixtureChunk(key=k, content=c, producing_tool=tool, entities=sorted(ents))
                    for k, c, ents, tool in candidates],
            prompt=t.prompt, recent_tools=[], gold_keys=gold,
            split="train", session_id=trace.session_id,
        ))
    return fixtures


def harvest_corpus(paths: list[str], *, w: int = 5, min_candidates: int = 5,
                   test_frac: float = 0.75, seed: int = 0) -> list[Fixture]:
    """Harvest every transcript, then assign train/test BY WHOLE SESSION (no session straddles a
    split — prevents leakage, design §3.4). `test_frac` = fraction of SESSIONS placed in test."""
    by_session: dict[str, list[Fixture]] = {}
    for p in paths:
        for fx in harvest_trace(parse_transcript(p), w=w, min_candidates=min_candidates):
            by_session.setdefault(fx.session_id or "unknown", []).append(fx)
    sessions = sorted(by_session)
    rng = random.Random(seed)
    rng.shuffle(sessions)
    n_test = max(1, round(len(sessions) * test_frac)) if sessions else 0
    test_sessions = set(sessions[:n_test])
    out: list[Fixture] = []
    for s in sessions:
        split = "test" if s in test_sessions else "train"
        for fx in by_session[s]:
            out.append(fx.model_copy(update={"split": split}))
    return out


@dataclass
class LexicalBiasReport:
    gold_recall: float
    control_recall: float
    margin: float
    degenerate: bool


def lexical_bias(fixtures: list[Fixture], *, k: int = 3, margin: float = 0.15,
                 seed: int = 0) -> LexicalBiasReport:
    """Design §5.2: BM25 R@k on the gold set vs a per-fixture random non-gold control of the same
    count. `degenerate` iff gold_recall >= control_recall + margin (gold is lexically trivial)."""
    rng = random.Random(seed)
    g_recs, c_recs = [], []
    for fx in fixtures:
        docs = {c.key: c.content for c in fx.chunks}
        ranked = [key for key, _s in sorted(bm25_scores(fx.prompt, docs).items(),
                                            key=lambda kv: (-kv[1], kv[0]))]
        gold = set(fx.gold_keys)
        g_recs.append(recall_at_k(ranked, gold, k))
        non_gold = [c.key for c in fx.chunks if c.key not in gold]
        control = set(rng.sample(non_gold, min(len(gold), len(non_gold)))) if non_gold else set()
        c_recs.append(recall_at_k(ranked, control, k) if control else 0.0)
    gr = sum(g_recs) / len(g_recs) if g_recs else 0.0
    cr = sum(c_recs) / len(c_recs) if c_recs else 0.0
    return LexicalBiasReport(gr, cr, margin, gr >= cr + margin)
