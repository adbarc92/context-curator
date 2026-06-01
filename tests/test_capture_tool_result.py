from context_curator.capture.tool_result import CAPTURE_MAX_CONTENT, capture_tool_result
from context_curator.embeddings import HashingEmbedder
from context_curator.store.memory import InMemoryStore


def _store():
    return InMemoryStore(embedder=HashingEmbedder(dim=16))


def test_ordinal_key_for_replay():
    s = _store()
    key = capture_tool_result(s, session_id="s1", tool_name="Read", content="x",
                              call_id="c0", ordinal=3, ttl_s=None, max_content=None)
    assert key == "session:s1:tool:000003:c0"


def test_live_key_uses_call_id_then_content_hash():
    s = _store()
    k1 = capture_tool_result(s, session_id="s1", tool_name="Read", content="x", call_id="abc")
    assert k1 == "session:s1:tool:abc"
    k2 = capture_tool_result(s, session_id="s1", tool_name="Read", content="hello")
    assert k2.startswith("session:s1:tool:") and len(k2.split(":")[-1]) == 12  # content hash


def test_error_skipped():
    s = _store()
    assert capture_tool_result(s, session_id="s1", tool_name="Bash", content="boom",
                               error=True) is None


def test_two_distinct_results_two_keys():
    s = _store()
    capture_tool_result(s, session_id="s1", tool_name="Read", content="a", call_id="c0")
    capture_tool_result(s, session_id="s1", tool_name="Read", content="b", call_id="c1")
    assert len(s.list("session:s1")) == 2


def test_truncation_only_when_max_content_set():
    s = _store()
    big = "x" * (CAPTURE_MAX_CONTENT + 100)
    k = capture_tool_result(s, session_id="s1", tool_name="Read", content=big,
                            call_id="c0", max_content=CAPTURE_MAX_CONTENT)
    assert s.retrieve(k).content.endswith("…[truncated]")
    k2 = capture_tool_result(s, session_id="s1", tool_name="Read", content=big,
                             call_id="c1", max_content=None)
    assert s.retrieve(k2).content == big  # replay path: no truncation
