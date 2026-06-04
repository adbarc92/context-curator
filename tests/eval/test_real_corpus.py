from pathlib import Path

import context_curator.eval as e
from context_curator.eval.real_corpus import (
    entities_match,
    extract_entities,
    harvest_corpus,
    harvest_trace,
    lexical_bias,
)
from context_curator.replay.schema import ToolCall, ToolResult, Trace, UserPrompt


def _c(name, **args):
    return ToolCall(call_id="x", name=name, args=args)


def test_extract_path_args():
    assert extract_entities(_c("Read", file_path="/a/b.py"))
    assert extract_entities(_c("Grep", path="/a"))
    assert extract_entities(_c("NotebookEdit", notebook_path="/a/n.ipynb"))
    # pattern-only Glob (no path) yields no entity
    assert extract_entities(_c("Glob", pattern="**/*.py")) == set()
    # path-less tool yields nothing
    assert extract_entities(_c("Bash", command="ls")) == set()


def test_equivalence_exact_and_dir_containment():
    read = extract_entities(_c("Read", file_path="/a/b.py"))
    grep_dir = extract_entities(_c("Grep", path="/a"))
    other = extract_entities(_c("Read", file_path="/c/d.py"))
    assert entities_match(read, read)            # exact
    assert entities_match(grep_dir, read)        # /a contains /a/b.py
    assert not entities_match(read, other)       # disjoint
    assert not entities_match(extract_entities(_c("Glob", pattern="*")), read)  # empty never match


def _trace(events):
    return Trace(session_id="sess-1", source="t", events=events)


def _prior_chunks(n):
    ev = []
    for i in range(n):
        ev.append(UserPrompt(turn_index=i, text=f"filler {i}"))
        ev.append(ToolCall(call_id=f"f{i}", name="Read", args={"file_path": f"/filler/{i}.py"}))
        ev.append(ToolResult(call_id=f"f{i}", content=f"filler content {i}"))
    return ev


def test_harvest_marks_refetch_as_gold():
    events = [
        UserPrompt(turn_index=0, text="open the auth file"),
        ToolCall(call_id="g", name="Read", args={"file_path": "/a/b.py"}),
        ToolResult(call_id="g", content="def login(): ..."),
        *_prior_chunks(5),
        UserPrompt(turn_index=6, text="search the auth dir"),
        ToolCall(call_id="r", name="Grep", args={"path": "/a"}),
        ToolResult(call_id="r", content="match"),
    ]
    fxs = harvest_trace(_trace(events), w=5, min_candidates=5)
    f6 = [f for f in fxs if f.prompt == "search the auth dir"]
    assert f6 and "g" in f6[0].gold_keys
    assert f6[0].session_id == "sess-1"


def test_verify_read_after_edit_is_not_gold():
    events = [
        UserPrompt(turn_index=0, text="open file"),
        ToolCall(call_id="g", name="Read", args={"file_path": "/a/b.py"}),
        ToolResult(call_id="g", content="x"),
        *_prior_chunks(5),
        UserPrompt(turn_index=6, text="fix and verify"),
        ToolCall(call_id="e", name="Edit", args={"file_path": "/a/b.py"}),
        ToolResult(call_id="e", content="edited"),
        ToolCall(call_id="v", name="Read", args={"file_path": "/a/b.py"}),
        ToolResult(call_id="v", content="x2"),
    ]
    fxs = harvest_trace(_trace(events), w=5, min_candidates=5)
    # The verify-Read of the just-edited file must NOT promote chunk "g" to gold; with no other
    # downstream re-fetch the turn carries zero gold and is dropped entirely.
    f6 = [f for f in fxs if f.prompt == "fix and verify"]
    assert all("g" not in f.gold_keys for f in fxs)
    assert f6 == []


def test_drops_turns_below_min_candidates():
    events = [
        UserPrompt(turn_index=0, text="first"),
        ToolCall(call_id="g", name="Read", args={"file_path": "/a/b.py"}),
        ToolResult(call_id="g", content="x"),
        UserPrompt(turn_index=1, text="second re-reads"),
        ToolCall(call_id="r", name="Read", args={"file_path": "/a/b.py"}),
        ToolResult(call_id="r", content="x2"),
    ]
    assert harvest_trace(_trace(events), w=5, min_candidates=5) == []


def test_harvest_corpus_splits_distinct_sessions_across_splits():
    base = Path(e.__file__).parent.parent.parent.parent / "tests" / "eval" / "_traces"
    paths = [str(base / "sample_a.jsonl"), str(base / "sample_b.jsonl")]
    fxs = harvest_corpus(paths, w=5, min_candidates=5, test_frac=0.5, seed=0)
    assert fxs, "samples must yield >=1 fixture"
    sessions = {f.session_id for f in fxs}
    assert len(sessions) >= 2, "expected >=2 distinct sessions (one per file)"
    # no session straddles a split
    for s in sessions:
        assert len({f.split for f in fxs if f.session_id == s}) == 1
    # with test_frac=0.5 over 2 sessions, the split is genuinely exercised: both splits present
    assert {f.split for f in fxs} == {"train", "test"}


def test_lexical_bias_flags_when_gold_is_lexically_trivial():
    from context_curator.eval.fixtures import Fixture, FixtureChunk
    chunks = [FixtureChunk(key="gold", content="authentication token rotation")] + \
             [FixtureChunk(key=f"n{i}", content="the system the system") for i in range(6)]
    fx = Fixture(name="f", chunks=chunks, prompt="authentication token",
                 gold_keys=["gold"], split="test")
    rep = lexical_bias([fx], k=3, margin=0.15, seed=0)
    assert rep.degenerate is True
    assert rep.gold_recall > rep.control_recall
