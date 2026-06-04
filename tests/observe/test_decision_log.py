import pytest

from context_curator.observe import decision_log as dl


@pytest.fixture
def decisions(tmp_path, monkeypatch):
    d = tmp_path / "decisions"
    monkeypatch.setattr(dl, "decisions_dir", lambda: d)
    return d


def test_record_then_read_roundtrip(decisions):
    dl.record_decision("s1", "do the thing", "recency", ["a", "b"])
    recs = dl.read_recent("s1", 5)
    assert len(recs) == 1
    r = recs[0]
    assert r.session_id == "s1" and r.source == "recency"
    assert r.injected_keys == ["a", "b"] and r.working_set_size == 2
    assert r.paged_in == ["a", "b"] and r.paged_out == []      # first turn: all in, none out
    assert r.prompt_preview == "do the thing" and r.ts.endswith("Z")


def test_page_in_out_delta_vs_previous(decisions):
    dl.record_decision("s1", "p1", "recency", ["a", "b", "c"])
    dl.record_decision("s1", "p2", "recency", ["b", "c", "d"])   # a leaves, d enters
    r = dl.read_recent("s1", 1)[0]
    assert r.paged_in == ["d"] and r.paged_out == ["a"]


def test_per_session_isolation(decisions):
    dl.record_decision("s1", "p", "recency", ["a"])
    dl.record_decision("s2", "p", "recency", ["z"])
    assert dl.read_recent("s1", 5)[0].injected_keys == ["a"]
    assert dl.read_recent("s2", 5)[0].injected_keys == ["z"]


def test_tail_read_discards_torn_final_line(decisions):
    decisions.mkdir(parents=True)
    p = dl.decision_log_path("s1")
    good = '{"ts":"2026-06-04T00:00:00Z","session_id":"s1","prompt_preview":"p","source":"recency","injected_keys":["a"],"working_set_size":1,"paged_in":["a"],"paged_out":[]}'  # noqa: E501
    p.write_bytes((good + "\n" + '{"ts":"torn').encode("utf-8"))   # complete record + torn partial
    recs = dl.read_recent("s1", 5)
    assert len(recs) == 1 and recs[0].injected_keys == ["a"]    # torn fragment dropped
    dl.record_decision("s1", "p2", "recency", ["a", "b"])        # writer's prev-read survives torn tail  # noqa: E501
    assert dl.read_recent("s1", 1)[0].paged_in == ["b"]


def test_record_decision_is_fail_open(decisions, monkeypatch):
    bad_parent = decisions.parent / "not_a_dir"
    bad_parent.write_text("x")                                  # a file, not a dir
    monkeypatch.setattr(dl, "decisions_dir", lambda: bad_parent / "sub")
    dl.record_decision("s1", "p", "recency", ["a"])             # mkdir/open fails -> swallowed


def test_newest_session_file(decisions):
    import os
    dl.record_decision("old", "p", "recency", ["a"])
    dl.record_decision("new", "p", "recency", ["b"])
    os.utime(dl.decision_log_path("old"), (1000, 1000))         # pin mtimes (avoid tie-flake)
    os.utime(dl.decision_log_path("new"), (2000, 2000))
    newest = dl._newest_session_file()
    assert newest is not None and "new" in newest.name
