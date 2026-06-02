from context_curator.embeddings import Embedder, HashingEmbedder
from context_curator.models import Chunk
from context_curator.onload.select import onload_select, seed_select
from context_curator.policy.relevance import RelevancePolicy
from context_curator.policy.weights import ONLOAD_COSINE_THRESHOLD, ONLOAD_WEIGHTS
from context_curator.store.memory import InMemoryStore

_THRESH = ONLOAD_COSINE_THRESHOLD


class _Emb(Embedder):
    """Deterministic 3-dim embedder keyed by leading token (mirrors the policy-test fake)."""
    _V = {"auth": [1.0, 0.0, 0.0], "csv": [0.0, 1.0, 0.0], "far": [0.0, 0.0, 1.0]}

    @property
    def dim(self) -> int:
        return 3

    def embed(self, text: str) -> list[float]:
        return self._V.get(text.split()[0], [0.0, 0.0, 0.0])


def _policy():
    return RelevancePolicy(_Emb(), ONLOAD_WEIGHTS)


def _chunk(key, topic, *, pin=False):
    return Chunk(key=key, content=f"{topic} content", pin=pin, embedding=_Emb().embed(topic))


def _onload(policy, prompt, cands, *, k=10, token_budget=None):
    return onload_select(
        policy, prompt, cands, cos_threshold=_THRESH, k=k, token_budget=token_budget
    )


# --- onload_select gate + exclusions ---------------------------------------

def test_offtopic_prompt_selects_nothing():
    cands = [_chunk("a", "far"), _chunk("b", "csv")]          # cos 0 vs "auth"
    assert _onload(_policy(), "auth query", cands) == []


def test_relevant_chunk_selected():
    cands = [_chunk("rel", "auth"), _chunk("off", "far")]
    out = _onload(_policy(), "auth query", cands)
    assert [c.key for c in out] == ["rel"]


def test_pinned_excluded_even_when_relevant():
    cands = [_chunk("pinned", "auth", pin=True), _chunk("rel", "auth")]
    out = _onload(_policy(), "auth query", cands)
    assert [c.key for c in out] == ["rel"]


def test_conventions_excluded_even_when_relevant():
    cands = [_chunk("proj:myapp:conventions", "auth"), _chunk("rel", "auth")]
    out = _onload(_policy(), "auth query", cands)
    assert "proj:myapp:conventions" not in [c.key for c in out]
    assert "rel" in [c.key for c in out]


def test_k_respected():
    cands = [_chunk(f"k{i}", "auth") for i in range(5)]
    out = _onload(_policy(), "auth query", cands, k=2)
    assert len(out) == 2


# --- gate characterization with the REAL HashingEmbedder (round-3 I4) -------

def _hchunk(key, content):
    return Chunk(key=key, content=content, embedding=HashingEmbedder().embed(content))


def test_gate_excludes_lexically_disjoint_offtopic():
    policy = RelevancePolicy(HashingEmbedder(), ONLOAD_WEIGHTS)
    prompt = "authenticate authorize user session"
    relevant = _hchunk("rel", "authenticate authorize user session token")
    disjoint = _hchunk("off", "quarterly financial revenue spreadsheet")   # no shared tokens
    out = _onload(policy, prompt, [relevant, disjoint])
    keys = [c.key for c in out]
    assert "rel" in keys and "off" not in keys


def test_gate_known_limitation_stopword_overlap_passes():
    # HONEST characterization (round-3 I4): HashingEmbedder does NOT strip stopwords, so a
    # chunk sharing only stopwords scores cosine ~0.7 and is NOT excluded. We assert the
    # false positive to document the gate is lexical-and-permissive, not semantic (M4b/bge job).
    policy = RelevancePolicy(HashingEmbedder(), ONLOAD_WEIGHTS)
    prompt = "how do i and the of a to in"
    stopword_only = _hchunk("sw", "how do i and the of a to in")
    out = _onload(policy, prompt, [stopword_only])
    assert "sw" in [c.key for c in out]


# --- seed_select ------------------------------------------------------------

def _mem():
    return InMemoryStore(embedder=HashingEmbedder(dim=16))


def test_seed_includes_all_pins_even_past_budget():
    s = _mem()
    s.store("p1", "x" * 8000, pin=True)
    s.store("p2", "y" * 8000, pin=True)
    keys = {c.key for c in seed_select(s, token_budget=100)}
    assert "p1" in keys and "p2" in keys              # pins never budget-truncated (round-1 M2)


def test_seed_includes_conventions_under_budget():
    s = _mem()
    s.store("proj:myapp:conventions", "conventions body", pin=False)
    keys = {c.key for c in seed_select(s, token_budget=1500)}
    assert "proj:myapp:conventions" in keys


def test_seed_excludes_nonpin_nonconvention():
    s = _mem()
    s.store("session:s:tool:c", "ordinary captured tool output", pin=False)
    assert seed_select(s, token_budget=1500) == []


def test_seed_convention_key_boundary_not_matched():
    # ends "-conventions" not ":conventions" -> the proj:[^:]+:conventions regex must NOT match
    s = _mem()
    s.store("shared:decisions:naming-conventions", "x", pin=False)
    assert seed_select(s, token_budget=1500) == []
