from context_curator.capture.file_ledger import capture_file_write
from context_curator.capture.subagent import capture_subagent_summary
from context_curator.embeddings import HashingEmbedder
from context_curator.store.memory import InMemoryStore


def _store():
    return InMemoryStore(embedder=HashingEmbedder(dim=16))


def test_file_write_ledger_entry():
    s = _store()
    key = capture_file_write(s, session_id="sess1", tool_name="Write", path="src/a.py")
    assert key == "shared:file_ledger:src/a.py"
    c = s.retrieve(key)
    assert c.tags == ["file-touch"]
    assert c.source == "file-ledger"
    assert c.provenance == "sess1"


def test_file_write_provenance_never_none():
    s = _store()
    key = capture_file_write(s, session_id="", tool_name="Edit", path="x")
    assert s.retrieve(key).provenance == "unknown-session"


def test_subagent_summary_chunk():
    s = _store()
    key = capture_subagent_summary(s, subagent_id="sub9", summary="explored auth",
                                   contracts_touched=["auth"])
    assert key == "shared:exploration:sub9"
    c = s.retrieve(key)
    assert c.content == "explored auth"
    assert c.source == "subagent:explore"
    assert "exploration" in c.tags and "auth" in c.tags


def test_subagent_empty_summary_noop():
    s = _store()
    assert capture_subagent_summary(s, subagent_id="sub9", summary="") is None
