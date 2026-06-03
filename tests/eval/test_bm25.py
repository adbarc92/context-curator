from context_curator.embeddings import HashingEmbedder
from context_curator.eval.bm25 import Bm25Target, bm25_scores
from context_curator.replay.schema import TaskSignal
from context_curator.store.memory import InMemoryStore


def test_bm25_prefers_rare_term_match_over_common_term_match():
    # docs: d_rare matches a term in only 1 doc (high IDF); d_common matches a term in all docs.
    docs = {
        "d_rare": "authentication token rotation",
        "d_common": "the the the system system",
        "n1": "the system system the",
        "n2": "the the system the system",
    }
    # 'the' (common) is shared with all docs; 'authentication' only with d_rare
    prompt = "the authentication"
    scores = bm25_scores(prompt, docs)
    assert scores["d_rare"] > scores["d_common"]    # rare-term match wins on IDF


def test_bm25target_ranks_candidates_by_bm25():
    store = InMemoryStore(embedder=HashingEmbedder(dim=16))
    store.store("rare", "authentication token rotation", ttl_s=None)
    store.store("common", "the the system system", ttl_s=None)
    # filler docs so "the" is shared across the pool (low IDF) while "authentication"
    # stays rare (high IDF) — the rare-term match must win.
    store.store("n1", "the system system the", ttl_s=None)
    store.store("n2", "the the system the system", ttl_s=None)
    sig = TaskSignal(turn_index=0, prompt="the authentication", subtask_id=None,
                     recent_tool_calls=[])
    d = Bm25Target().decide(sig, store)
    keys = [c.key for c in d.candidates]
    assert keys[0] == "rare"             # ranked best by BM25
