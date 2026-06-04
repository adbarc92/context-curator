import json

from context_curator.observe import decision_log as dl
from context_curator.observe import statusline as sl


def _patch(tmp_path, monkeypatch):
    d = tmp_path / "decisions"
    monkeypatch.setattr(dl, "decisions_dir", lambda: d)
    return d


def test_render_exact_line(tmp_path, monkeypatch):
    _patch(tmp_path, monkeypatch)
    dl.record_decision("s1", "p1", "recency", ["a", "b", "c"])
    dl.record_decision("s1", "p2", "recency", ["b", "c", "d"])   # +d / -a -> +1/-1, ws 3
    out = sl._line_for(json.dumps({"session_id": "s1"}))
    assert out == "CC ws:3 +1/-1 [recency]"


def test_session_present_but_no_file_is_idle_not_newest(tmp_path, monkeypatch):
    _patch(tmp_path, monkeypatch)
    dl.record_decision("other", "p", "recency", ["z"])           # a different session's file exists
    out = sl._line_for(json.dumps({"session_id": "s1"}))         # s1 has no file yet
    assert out == "CC ·"                                    # idle, NOT other's data


def test_blank_session_falls_back_to_newest(tmp_path, monkeypatch):
    _patch(tmp_path, monkeypatch)
    dl.record_decision("only", "p", "curator", ["a"])
    out = sl._line_for(json.dumps({"session_id": ""}))
    assert out == "CC ws:1 +1/-0 [curator]"


def test_malformed_stdin_is_idle(tmp_path, monkeypatch):
    _patch(tmp_path, monkeypatch)
    assert sl._line_for("not json at all") == "CC ·"


def test_non_string_session_id_is_idle(tmp_path, monkeypatch):
    _patch(tmp_path, monkeypatch)
    assert sl._line_for(json.dumps({"session_id": 123})) == "CC ·"   # non-string -> graceful idle
