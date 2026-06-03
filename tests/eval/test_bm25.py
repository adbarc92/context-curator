from context_curator.eval.bm25 import bm25_scores


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
