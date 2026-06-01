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
    tp = tmp_path / "t.jsonl"
    tp.write_text(
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "first"}]}}) + "\n" +
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "final summary"}]}}) + "\n",
        encoding="utf-8")
    assert extract_summary({"transcript_path": str(tp)}) == "final summary"
    assert extract_summary({}) == ""                       # no path -> empty -> no-op
