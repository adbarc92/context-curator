from context_curator.embeddings import HashingEmbedder
from context_curator.hooks.post_tool_use import handle as post_handle
from context_curator.hooks.subagent_stop import extract_summary
from context_curator.store.memory import InMemoryStore


def _store():
    return InMemoryStore(embedder=HashingEmbedder(dim=16))


def test_write_captures_ledger_and_result():
    s = _store()
    event = {"tool_name": "Write", "session_id": "s1",
             "tool_input": {"file_path": "src/a.py", "content": "code"},
             "tool_response": "ok", "call_id": "c0"}
    r = post_handle(event, s)
    assert r.exit_code == 0
    assert s.retrieve("shared:file_ledger:src/a.py") is not None
    assert s.retrieve("session:s1:tool:c0") is not None


def test_read_excluded_from_ledger():
    s = _store()
    event = {"tool_name": "Read", "session_id": "s1",
             "tool_input": {"file_path": "src/a.py"}, "tool_response": "data", "call_id": "c1"}
    post_handle(event, s)
    assert s.list("shared:file_ledger") == []          # Read does not write the ledger
    assert s.retrieve("session:s1:tool:c1") is not None # but its result is captured


def test_dict_tool_response_is_coerced():
    s = _store()
    event = {"tool_name": "Grep", "session_id": "s1",
             "tool_input": {}, "tool_response": {"matches": ["a", "b"]}, "call_id": "c2"}
    r = post_handle(event, s)
    assert r.exit_code == 0
    assert s.retrieve("session:s1:tool:c2") is not None   # no crash on dict response


def test_two_tools_two_chunks():
    s = _store()
    for cid in ("c0", "c1"):
        post_handle({"tool_name": "Bash", "session_id": "s1", "tool_input": {},
                     "tool_response": f"out-{cid}", "call_id": cid}, s)
    assert len(s.list("session:s1")) == 2


def test_extract_summary_from_transcript(tmp_path):
    import json

    def _msg(text):
        return json.dumps({"type": "assistant",
                           "message": {"content": [{"type": "text", "text": text}]}})

    tp = tmp_path / "t.jsonl"
    tp.write_text(_msg("first") + "\n" + _msg("final summary") + "\n", encoding="utf-8")
    assert extract_summary({"transcript_path": str(tp)}) == "final summary"
    assert extract_summary({}) == ""                       # no path -> empty -> no-op


def test_read_result_is_tagged_with_its_file_entity(tmp_path):
    from context_curator.entities import canon_path
    s = _store()
    f = str(tmp_path / "a.py")
    post_handle({"tool_name": "Read", "session_id": "s1", "tool_input": {"file_path": f},
                 "tool_response": "data", "tool_use_id": "toolu_1"}, s)
    assert s.retrieve("session:s1:tool:toolu_1").tags == ["read", f"entity:{canon_path(f)}"]


def test_grep_path_is_an_entity_and_bash_has_none(tmp_path):
    from context_curator.entities import canon_path
    s = _store()
    post_handle({"tool_name": "Grep", "session_id": "s1",
                 "tool_input": {"pattern": "x", "path": str(tmp_path)},
                 "tool_response": "m", "tool_use_id": "g"}, s)
    post_handle({"tool_name": "Bash", "session_id": "s1", "tool_input": {"command": "ls"},
                 "tool_response": "o", "tool_use_id": "b"}, s)
    assert s.retrieve("session:s1:tool:g").tags == ["grep", f"entity:{canon_path(str(tmp_path))}"]
    assert s.retrieve("session:s1:tool:b").tags == ["bash"]


def test_identical_rereads_are_distinct_chunks_when_tool_use_id_is_present():
    # Claude Code sends tool_use_id (not call_id). Without it the key is a content hash, so
    # re-reading an unchanged file overwrote one row and the refetch signal never accumulated.
    s = _store()
    for tid in ("toolu_a", "toolu_b"):
        post_handle({"tool_name": "Read", "session_id": "s1", "tool_input": {"file_path": "/r/a"},
                     "tool_response": "same content", "tool_use_id": tid}, s)
    assert len(s.list("session:s1")) == 2
