import math
from context_curator.eval.fixtures import Fixture, FixtureChunk
from context_curator.eval.learned.features import (
    canon_tool, feature_names, candidate_matrix, fit_norm, apply_norm,
)
from context_curator.eval.real_corpus import harvest_trace
from context_curator.replay.schema import ToolCall, ToolResult, Trace, UserPrompt


def _trace(events):
    return Trace(session_id="sess-1", source="t", events=events)


def _prior(n):
    ev = []
    for i in range(n):
        ev.append(UserPrompt(turn_index=i, text=f"filler {i}"))
        ev.append(ToolCall(call_id=f"f{i}", name="Read", args={"file_path": f"/filler/{i}.py"}))
        ev.append(ToolResult(call_id=f"f{i}", content=f"filler {i}"))
    return ev


def test_harvest_persists_tool_and_entities():
    events = [
        UserPrompt(turn_index=0, text="open auth"),
        ToolCall(call_id="g", name="Grep", args={"path": "/a"}),
        ToolResult(call_id="g", content="match in /a"),
        *_prior(5),
        UserPrompt(turn_index=6, text="search auth dir"),
        ToolCall(call_id="r", name="Read", args={"file_path": "/a/b.py"}),
        ToolResult(call_id="r", content="x"),
    ]
    fxs = harvest_trace(_trace(events), w=5, min_candidates=5)
    fx = [f for f in fxs if f.prompt == "search auth dir"][0]
    chunk_g = [c for c in fx.chunks if c.key == "g"][0]
    assert chunk_g.producing_tool == "Grep"
    assert chunk_g.entities  # /a was extracted as an entity


def test_canon_tool_lowercases_and_buckets_unknown():
    assert canon_tool("Read") == "read"
    assert canon_tool("Grep") == "grep"
    assert canon_tool("WebFetch") == "other"
    assert canon_tool(None) == "other"


def _fx():
    return Fixture(
        name="s:t1",
        prompt="warehouse restock",
        gold_keys=["k1"],
        session_id="s",
        chunks=[
            FixtureChunk(key="k0", content="unrelated text", producing_tool="Bash"),
            FixtureChunk(key="k1", content="warehouse restock logic", producing_tool="Read"),
        ],
    )


def test_candidate_matrix_shape_and_label():
    X, y, keys = candidate_matrix(_fx())
    assert keys == ["k0", "k1"]
    assert y == [0, 1]
    assert len(X) == 2 and len(X[0]) == len(feature_names())
    # recency_rank: oldest=0.0, newest=1.0 over 2 chunks
    ri = feature_names().index("recency_rank")
    assert X[0][ri] == 0.0 and X[1][ri] == 1.0


def test_norm_zscore_handles_constant_column():
    X = [[1.0, 5.0], [3.0, 5.0]]
    means, stds = fit_norm(X)
    assert means == [2.0, 5.0]
    assert stds[1] == 1.0  # constant column → std 1, not 0
    Z = apply_norm(X, means, stds)
    assert Z[0][0] == -1.0 and Z[1][0] == 1.0
