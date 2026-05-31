from context_curator.replay.capture.synthetic import TraceBuilder
from context_curator.replay.engine import ReplayEngine
from context_curator.replay.schema import Decision, ToolResult, Trace
from context_curator.replay.target import RecencyOnlyTarget


def _engine():
    return ReplayEngine(target=RecencyOnlyTarget(tags=["read"]))


def _trace():
    return (
        TraceBuilder("s1")
        .user("turn 0")                                   # decision: nothing captured yet
        .tool("Read", {"path": "a.py"}).result("alpha")
        .user("turn 1")                                   # decision: sees alpha
        .tool("Read", {"path": "b.py"}).result("beta")
        .user("turn 2")                          # decision: sees beta, alpha (newest first)
        .build()
    )


def test_byte_identical_across_runs():
    trace = _trace()
    a = _engine().run(trace).model_dump()
    b = _engine().run(trace).model_dump()
    assert a == b


def test_turn_only_sees_prior_turns():
    log = _engine().run(_trace())
    # turn 0: empty; turn 1: alpha; turn 2: beta then alpha
    assert log.decisions[0].selected == []
    assert [s.key for s in log.decisions[1].selected] == ["session:s1:tool:000000:c0"]
    assert [s.key for s in log.decisions[2].selected] == [
        "session:s1:tool:000001:c1", "session:s1:tool:000000:c0",
    ]


class _Recorder:
    name = "recorder"

    def __init__(self):
        self.signals = []

    def decide(self, signal, store):
        self.signals.append(signal)
        return Decision(turn_index=signal.turn_index, subtask_id=signal.subtask_id,
                        prompt_preview=signal.prompt[:80], selected=[], total_tokens=0)


def test_window_holds_last_n_including_errors():
    rec = _Recorder()
    trace = (TraceBuilder("s1").tool("Read", {}).result("ok")
             .tool("Bash", {}).result("boom", error=True).user("now").build())
    ReplayEngine(target=rec, recent_window=5).run(trace)
    names = [r.name for r in rec.signals[0].recent_tool_calls]
    assert names == ["Read", "Bash"]  # both calls, incl. the errored one


def test_subtask_id_carried_to_log():
    trace = TraceBuilder("s1").user("x", subtask_id="sub-9").build()
    log = _engine().run(trace)
    assert log.decisions[0].subtask_id == "sub-9"


def test_only_error_results_yield_empty_decision():
    trace = (
        TraceBuilder("s1")
        .tool("Bash", {}).result("boom", error=True)
        .user("now")
        .build()
    )
    log = ReplayEngine(target=RecencyOnlyTarget(tags=["bash"])).run(trace)
    assert log.decisions[0].selected == []


def test_orphan_tool_result_is_skipped_not_ingested():
    # a ToolResult whose call_id has no matching ToolCall in the trace must be dropped
    base_trace = TraceBuilder("s1").user("x").build()
    orphan_result = ToolResult(call_id="ghost", content="leak")
    extra_user = TraceBuilder("s1").user("y").build().events[0]
    # Trace.events is a list (mutable), so we can append to it
    new_events = list(base_trace.events) + [orphan_result, extra_user]
    trace = Trace(session_id=base_trace.session_id, source=base_trace.source, events=new_events)
    log = ReplayEngine(target=RecencyOnlyTarget()).run(trace)
    # the orphan ToolResult must never be ingested -> no ghost chunk in any decision
    all_selected = [s.key for d in log.decisions for s in d.selected]
    assert not any("ghost" in k for k in all_selected)


def test_byte_identical_with_budget_and_multitool():
    trace = (
        TraceBuilder("s2")
        .user("t0")
        .tool("Read", {}).result("x" * 200)
        .tool("Read", {}).result("y" * 200)
        .tool("Read", {}).result("z" * 200)
        .user("t1")
        .build()
    )

    def eng():
        return ReplayEngine(target=RecencyOnlyTarget(tags=["read"], token_budget=40, k=10))

    assert eng().run(trace).model_dump() == eng().run(trace).model_dump()


def test_recent_window_must_be_positive():
    import pytest
    with pytest.raises(ValueError):
        ReplayEngine(target=RecencyOnlyTarget(), recent_window=0)
