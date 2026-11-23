import os
from pathlib import Path

from context_curator.eval import stickiness as st
from context_curator.eval.real_corpus import harvest_trace
from context_curator.replay.schema import ToolCall, ToolResult, Trace, UserPrompt

BASE = Path(__file__).parent / "_traces"
SAMPLES = [str(BASE / "sample_a.jsonl"), str(BASE / "sample_b.jsonl")]


def _turn(i, path, prompt=None):
    return [UserPrompt(turn_index=i, text=prompt or f"turn {i}"),
            ToolCall(call_id=f"c{i}", name="Read", args={"file_path": path}),
            ToolResult(call_id=f"c{i}", content=f"content of {path} at {i}")]


def _trace():
    ev = []
    for i in range(6):
        ev += _turn(i, f"/filler/{i}.py")
    ev += _turn(6, "/filler/0.py")           # turn 6 re-fetches the turn-0 file
    ev += _turn(7, "/other/x.py")
    return Trace(session_id="s", source="t", events=ev)


def test_gap_zero_is_the_original_harvest():
    t = _trace()
    assert harvest_trace(t) == harvest_trace(t, gap=0)


def test_gap_pushes_the_gold_window_into_the_future():
    t = _trace()
    # gap 0: fixtures at turns 5 and 6 see the turn-6 refetch inside [i, i+5]
    assert [fx.name for fx in harvest_trace(t, gap=0)] == ["s:t5", "s:t6"]
    # gap 2: window [i+2, i+7] -> turn 5 looks at 7..12 and misses the turn-6 refetch; turns < 5
    # have fewer than 5 candidates -> no fixtures at all
    assert harvest_trace(t, gap=2) == []
    # gap 1: window [i+1, i+6] -> turn 5 still sees turn 6; turn 6 (window 7..12) no longer does
    assert [fx.name for fx in harvest_trace(t, gap=1)] == ["s:t5"]


def test_live_arm_scores_with_the_shipped_ranker(monkeypatch):
    fx = harvest_trace(_trace())[0]
    calls = []
    real = st.stickiness_rank
    monkeypatch.setattr(st, "stickiness_rank", lambda p, c: calls.append(1) or real(p, c))
    assert 0.0 <= st.live_ndcg(fx) <= 1.0 and calls


def test_subagent_transcripts_are_skipped(tmp_path):
    sub = tmp_path / "proj" / "sess" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-1.jsonl").write_bytes(Path(SAMPLES[0]).read_bytes())
    assert st.is_subagent_transcript(str(sub / "agent-1.jsonl"))
    assert not st.is_subagent_transcript(SAMPLES[0])


def test_discovery_sessions_are_excluded_by_sha():
    sha = st.file_sha16(SAMPLES[0])
    by, manifest = st.load_sessions(SAMPLES, exclude_shas={sha}, gap=0)
    assert sha not in manifest.values()
    assert all(s for s in manifest)                     # session ids recorded
    assert set(by) <= set(manifest)


def test_below_the_floor_is_feasibility_only_with_no_decision():
    rep = st.run(SAMPLES, exclude_shas=set(), min_sessions=45, seed=0)
    assert rep["status"] == "feasibility-only"
    assert rep["decision"] is None
    assert set(rep["gaps"]) == {1, 2, 3, 5}
    assert {"stickiness_live", "bm25", "refetch_solo"} <= set(rep["arms"])
    text = st.format_report(rep)
    assert "feasibility-only" in text and "manifest" in text


def test_decision_rule_matches_the_preregistration():
    assert st.decide(status="verdict", lo=0.01, hi=0.05) == "KEEP stickiness"
    assert (st.decide(status="verdict", lo=0.01, hi=0.05, decaying=True)
            == "KEEP stickiness, pending review (gap sweep decaying)")
    assert st.decide(status="verdict", lo=-0.01, hi=0.05, decaying=True) == "REVERT to BM25"
    assert st.decide(status="verdict", lo=-0.01, hi=0.05) == "REVERT to BM25"
    assert st.decide(status="verdict", lo=-0.09, hi=-0.02) == "REVERT to BM25"
    assert st.decide(status="inconclusive-underpowered", lo=0.2, hi=0.5) == "HARVEST more"
    assert st.decide(status="feasibility-only", lo=0.2, hi=0.3) is None


def test_decaying_flag():
    assert st.is_decaying(gap0=0.30, gap5=0.10, gap5_fixtures=4) is True
    assert st.is_decaying(gap0=0.30, gap5=0.20, gap5_fixtures=4) is False
    assert st.is_decaying(gap0=-0.1, gap5=-0.3, gap5_fixtures=4) is False   # no lift to decay
    # no gap-5 fixtures -> undefined, not a spurious "decaying" from an empty mean of 0
    assert st.is_decaying(gap0=0.30, gap5=0.0, gap5_fixtures=0) is None


def test_main_prints_a_report(capsys):
    st.main([*SAMPLES, "--min-sessions", "45"])
    assert "Stickiness" in capsys.readouterr().out


def test_paths_are_not_required_to_be_absolute():
    rel = os.path.relpath(SAMPLES[0])
    assert st.file_sha16(rel) == st.file_sha16(SAMPLES[0])


def test_a_grown_copy_of_a_discovery_session_is_still_excluded(tmp_path, monkeypatch):
    # a transcript that grew after pinning has a new sha but the same session id
    grown = tmp_path / "grown.jsonl"
    grown.write_bytes(Path(SAMPLES[0]).read_bytes() + b"\n")
    sid = st.parse_transcript(SAMPLES[0]).session_id
    monkeypatch.setattr(st, "DISCOVERY_SESSIONS", ((sid[:8], sid[-12:]),))
    by, manifest = st.load_sessions([str(grown), SAMPLES[1]], exclude_shas=set(),
                                    exclude_sessions=st.DISCOVERY_SESSIONS, gap=0)
    assert sid not in manifest and sid not in by


def test_explicitly_excluded_session_ids_are_dropped():
    sid = st.parse_transcript(SAMPLES[0]).session_id
    rep = st.run(SAMPLES, exclude_shas=set(), exclude_session_ids={sid}, min_sessions=45)
    assert st.file_sha16(SAMPLES[0]) not in rep["manifest"]


def test_fixtures_per_project_are_reported():
    rep = st.run(SAMPLES, exclude_shas=set(), min_sessions=45)
    assert sum(rep["fixtures_per_project"].values()) == rep["n_fixtures"]


def test_include_discovery_is_sensitivity_only_and_renders_no_decision(capsys):
    st.main([*SAMPLES, "--min-sessions", "1", "--include-discovery"])
    out = capsys.readouterr().out
    assert "sensitivity-only" in out and "decision: none" in out
