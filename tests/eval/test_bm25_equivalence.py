"""The Counter-based bm25_scores must be BIT-IDENTICAL to the implementation every published
keystone number was produced with (frozen verbatim below), so past verdicts stay reproducible."""
import math
import random
import re

from context_curator.eval.bm25 import bm25_scores


def _reference_bm25(prompt, docs, *, k1=1.5, b=0.75):    # frozen copy of the pre-Track-A code
    def tok(text):
        return re.findall(r"[a-z0-9]+", text.lower())
    m = len(docs)
    toks = {key: tok(text) for key, text in docs.items()}
    df = {}
    for tl in toks.values():
        for term in set(tl):
            df[term] = df.get(term, 0) + 1
    idf = {t: math.log((m - n + 0.5) / (n + 0.5) + 1.0) for t, n in df.items()}
    avgdl = (sum(len(tl) for tl in toks.values()) / m) if m else 0.0
    q = tok(prompt)
    out = {}
    for key, tl in toks.items():
        dl = len(tl)
        tf = {}
        for term in tl:
            tf[term] = tf.get(term, 0) + 1
        s = 0.0
        for term in q:
            if term not in tf:
                continue
            f = tf[term]
            denom = f + k1 * (1 - b + b * (dl / avgdl if avgdl else 0.0))
            s += idf.get(term, 0.0) * (f * (k1 + 1) / denom if denom else 0.0)
        out[key] = s
    return out


# Unicode edge cases: lower() maps the Kelvin sign and dotted capital I to ASCII letters, and a lone
# surrogate must not crash or merge tokens.
_VOCAB = ["alpha", "beta", "gamma", "Delta", "eps42", "x", "y_z", "the", "a", "file.py", "Ω", "",
          "\u212a", "\u0130stanbul", "\u00dfa", "a1b2", "--", "\ud800", "ALPHA's", "the-the",
          "\n\tthe", "  "]


def _text(rng):
    sep = rng.choice([" ", "", "\n"])
    return sep.join(rng.choice(_VOCAB) for _ in range(rng.randint(0, 40)))


def test_bit_identical_to_reference_on_randomized_inputs():
    rng = random.Random(0)
    for _ in range(3000):
        docs = {f"k{i}": _text(rng) for i in range(rng.randint(0, 12))}
        prompt = _text(rng)
        assert bm25_scores(prompt, docs) == _reference_bm25(prompt, docs)


def test_edge_cases_identical():
    for prompt, docs in [("", {}), ("a", {}), ("", {"k": "a b"}), ("a a a", {"k": ""}),
                         ("alpha", {"k1": "", "k2": ""}), ("alpha alpha", {"k": "alpha"})]:
        assert bm25_scores(prompt, docs) == _reference_bm25(prompt, docs)
