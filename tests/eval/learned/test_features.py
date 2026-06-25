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
