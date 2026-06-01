from context_curator.embeddings import HashingEmbedder
from context_curator.replay.ingest import ingest_tool_result
from context_curator.replay.schema import ToolCall, ToolResult
from context_curator.store.memory import InMemoryStore


def _store():
    return InMemoryStore(embedder=HashingEmbedder(dim=32))


def test_ingest_writes_retrievable_chunk_with_canonical_source():
    store = _store()
    call = ToolCall(call_id="c0", name="WebFetch", args={})
    ingest_tool_result(ToolResult(call_id="c0", content="body"), call, "s1", 0, store)
    key = "session:s1:tool:000000:c0"
    chunk = store.retrieve(key)
    assert chunk is not None
    assert chunk.content == "body"
    assert chunk.tags == ["webfetch"]        # tag lowercased for matching
    assert chunk.source == "tool:WebFetch"   # source preserves canonical case (§9 audit)
    assert chunk.ttl_s is None               # replay chunks never expire


def test_error_results_are_skipped():
    store = _store()
    call = ToolCall(call_id="c0", name="Bash", args={})
    ingest_tool_result(ToolResult(call_id="c0", content="boom", error=True), call, "s1", 0, store)
    assert store.list("session:s1") == []


def test_duplicate_call_id_does_not_overwrite():
    store = _store()
    call = ToolCall(call_id="dup", name="Read", args={})
    ingest_tool_result(ToolResult(call_id="dup", content="first"), call, "s1", 0, store)
    ingest_tool_result(ToolResult(call_id="dup", content="second"), call, "s1", 1, store)
    keys = sorted(store.list("session:s1"))
    assert keys == ["session:s1:tool:000000:dup", "session:s1:tool:000001:dup"]
