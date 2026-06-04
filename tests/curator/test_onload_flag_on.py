"""M4d flag-ON regression: with CC_CURATOR_ONLOAD=1 the bge cosine gate must EXCLUDE a
chunk that is clearly off-topic (its bge cosine sits below ONLOAD_BGE_COSINE_THRESHOLD).

This is a real-bge gate test (the threshold is a bge-cosine), so it uses FastEmbedEmbedder
gated by importorskip — mirroring tests/test_curator_bge.py — and the ACTUAL 0.55 threshold
(no monkeypatched threshold). The seeding/flag/handler-invocation pattern mirrors
tests/test_curator_handler.py (SqliteStore seeded via .store(), flag via the config module,
handler.handle_onload returning {"ok": True, "keys": [...]}).
"""
import pytest

pytest.importorskip("fastembed")                                # only with the `embed` extra

from context_curator.curator import config, handler  # noqa: E402
from context_curator.embeddings import FastEmbedEmbedder  # noqa: E402
from context_curator.store.sqlite_store import SqliteStore  # noqa: E402


def test_onload_excludes_known_irrelevant_chunk(tmp_path, monkeypatch):
    # Flag ON (the gate, dark by default, is the thing under test). Use the real bge embedder
    # for BOTH the store write-time embeddings and the handler's on-demand/scoring embedder so
    # the cosines compared at the gate are genuine bge-small cosines.
    monkeypatch.setenv("CC_CURATOR_ONLOAD", "1")
    monkeypatch.setattr(config, "CURATOR_ONLOAD_ENABLED", True)  # env read at import time

    emb = FastEmbedEmbedder()
    store = SqliteStore(db_path=str(tmp_path / "onload.db"), embedder=emb)

    prompt = "how do I rotate JWT refresh tokens"
    # ON-TOPIC: about auth/token rotation -> bge cosine above the 0.55 gate.
    store.store("topic:auth", "rotate the JWT refresh token by issuing a new token and "
                              "revoking the old credential during authentication")
    # KNOWN-IRRELEVANT: CSS layout, semantically far from JWT auth -> bge cosine below the gate.
    store.store("topic:css", "use CSS grid to arrange the layout columns and pick "
                             "background colors for the page")

    out = handler.handle_onload(
        store, emb, {"prompt": prompt, "k": 10, "token_budget": None}
    )
    selected_keys = out["keys"]

    # NON-NEGOTIABLE: the off-topic chunk is dropped BY THE GATE (its bge cosine < threshold).
    # This is the core contract — NOT a self-fulfilling "the relevant chunk got picked" check.
    assert "topic:css" not in selected_keys

    # Recency-fallback on an empty/zero-survivor gate is the HOOK's job, not handle_onload's
    # (handle_onload returns the gated keys verbatim); that path is covered by
    # tests/test_onload_recency.py and tests/test_hooks_onload.py.
