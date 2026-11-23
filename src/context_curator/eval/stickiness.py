"""Powered confirmatory run for the stickiness ranker — the analysis frozen in
docs/superpowers/stickiness-powered-prereg.md. Scores the SHIPPED `stickiness_rank` (the function
the UserPromptSubmit hook runs) against BM25 on real-transcript fixtures, with a session-clustered
CI, the precision gate, a corpus-size floor, a gap sweep, a replication arm and a sensitivity run.

    uv run python -m context_curator.eval.stickiness [TRANSCRIPT ...] [--min-sessions N]

Deterministic (seed 0). Gold labels are CWD-dependent (relative paths resolve via os.path)."""
from __future__ import annotations

import argparse
import glob
import hashlib
import os
from collections.abc import Iterable

from context_curator.eval.fixtures import Fixture
from context_curator.eval.learned.feasibility import bm25_ndcg, needed_n_range, prior_refetch_scores
from context_curator.eval.metrics import ndcg_at_k
from context_curator.eval.precision_gate import precision_gate
from context_curator.eval.real_corpus import harvest_trace, lexical_bias
from context_curator.eval.stats import cluster_bootstrap_ci
from context_curator.onload.stickiness import stickiness_rank
from context_curator.replay.capture.transcript import parse_transcript

# The 5 sessions the hypothesis came from (learned-feasibility-prereg.md), by sha256[:16].
DISCOVERY_SHAS = frozenset({"47f3cfd7e9fb9c19", "c94080b5cfc8deb5", "529929af76991fe3",
                            "b183be9bdb7ea27a", "4fcc66d424b7e55d"})
MIN_SESSIONS = 45
SWEEP_GAPS = (1, 2, 3, 5)
DEFAULT_GLOB = "src/context_curator/eval/fixtures/_real_local/**/*.jsonl"
MEI = 0.10
K = 10


def file_sha16(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


def is_subagent_transcript(path: str) -> bool:
    return "subagents" in os.path.normpath(path).split(os.sep)


def _parse_all(paths: Iterable[str], exclude_shas: set[str] | frozenset[str]):
    """[(sha, project, trace)] for main-session transcripts, deduplicated by session id."""
    out, seen = [], set()
    for p in sorted(paths):
        if is_subagent_transcript(p):
            continue
        sha = file_sha16(p)
        if sha in exclude_shas:
            continue
        trace = parse_transcript(p)
        if trace.session_id in seen:
            continue
        seen.add(trace.session_id)
        out.append((sha, os.path.basename(os.path.dirname(os.path.abspath(p))), trace))
    return out


def _by_session(parsed, *, gap: int) -> dict[str, list[Fixture]]:
    by: dict[str, list[Fixture]] = {}
    for _sha, _proj, trace in parsed:
        fxs = harvest_trace(trace, w=5, min_candidates=5, gap=gap)
        if fxs:
            by[trace.session_id] = fxs
    return by


def load_sessions(paths: Iterable[str], *, exclude_shas, gap: int = 0):
    """(fixtures by session id, {session id: sha16}) — sessions without fixtures are dropped."""
    parsed = _parse_all(paths, exclude_shas)
    by = _by_session(parsed, gap=gap)
    return by, {t.session_id: sha for sha, _p, t in parsed if t.session_id in by}


def live_ndcg(fx: Fixture, k: int = K) -> float:
    ranked = stickiness_rank(fx.prompt, [(c.key, c.content, c.entities) for c in fx.chunks])
    return ndcg_at_k(ranked, set(fx.gold_keys), k)


def solo_refetch_ndcg(fx: Fixture, k: int = K) -> float:
    sc = prior_refetch_scores(fx)
    ranked = [key for key, _s in sorted(sc.items(), key=lambda t: (-t[1], t[0]))]
    return ndcg_at_k(ranked, set(fx.gold_keys), k)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _lift(by: dict[str, list[Fixture]], seed: int) -> dict:
    deltas, sids, live, bm, solo = [], [], [], [], []
    for sid in sorted(by):
        for fx in by[sid]:
            lv, bv = live_ndcg(fx), bm25_ndcg(fx, K)
            live.append(lv)
            bm.append(bv)
            solo.append(solo_refetch_ndcg(fx))
            deltas.append(lv - bv)
            sids.append(sid)
    lo, hi = cluster_bootstrap_ci(deltas, sids, seed=seed)
    arms = {"stickiness_live": _mean(live), "bm25": _mean(bm), "refetch_solo": _mean(solo)}
    return {"mean": _mean(deltas), "ci": [lo, hi], "n_sessions": len(by),
            "n_fixtures": len(deltas), "deltas": deltas, "sids": sids, "arms": arms}


def decide(*, status: str, lo: float, hi: float) -> str | None:
    """The pre-registered decision rule. None when no verdict may be rendered."""
    if status == "verdict":
        return "KEEP stickiness" if lo > 0 else "REVERT to BM25"
    if status == "inconclusive-underpowered":
        return "HARVEST more"
    return None


def is_decaying(*, gap0: float, gap5: float, gap5_fixtures: int) -> bool | None:
    """Pre-registered robustness flag; None when the gap-5 sweep has no fixtures to judge."""
    if gap5_fixtures == 0:
        return None
    return gap0 > 0 and gap5 < gap0 / 2


def run(paths: Iterable[str], *, exclude_shas=DISCOVERY_SHAS, min_sessions: int = MIN_SESSIONS,
        mei: float = MEI, seed: int = 0) -> dict:
    paths = list(paths)
    parsed = _parse_all(paths, exclude_shas)
    by = _by_session(parsed, gap=0)
    primary = _lift(by, seed)
    lo, hi = primary["ci"]
    gate = precision_gate(lo=lo, hi=hi, n_sessions=len(by), mei=mei)
    status = "feasibility-only" if len(by) < min_sessions else gate.status
    nmin, nmax = needed_n_range(primary["deltas"], primary["sids"], mei=mei, seed=seed)
    gaps = {g: {k: v for k, v in _lift(_by_session(parsed, gap=g), seed).items()
                if k in ("mean", "ci", "n_fixtures")} for g in SWEEP_GAPS}
    with_discovery = (_lift(_by_session(_parse_all(paths, set()), gap=0), seed)
                      if exclude_shas else None)
    flat = [fx for fxs in by.values() for fx in fxs]
    lb = lexical_bias(flat, k=3, margin=0.15, seed=seed) if flat else None
    projects: dict[str, int] = {}
    for _sha, proj, t in parsed:
        if t.session_id in by:
            projects[proj] = projects.get(proj, 0) + 1
    return {
        "status": status, "gate": gate.status, "needed_n": gate.needed_n,
        "needed_n_range": [nmin, nmax], "decision": decide(status=status, lo=lo, hi=hi),
        "mean_delta": primary["mean"], "ci": [lo, hi], "n_sessions": len(by),
        "n_fixtures": primary["n_fixtures"], "min_sessions": min_sessions, "mei": mei,
        "arms": primary["arms"], "gaps": gaps,
        "decaying": is_decaying(gap0=primary["mean"], gap5=gaps[5]["mean"],
                                gap5_fixtures=gaps[5]["n_fixtures"]),
        "with_discovery": (None if with_discovery is None else
                           {"mean": with_discovery["mean"], "ci": with_discovery["ci"],
                            "n_sessions": with_discovery["n_sessions"]}),
        "lexical": (None if lb is None else {"gold_r3": lb.gold_recall,
                                             "control_r3": lb.control_recall,
                                             "degenerate": lb.degenerate}),
        "sessions_per_project": projects,
        "manifest": sorted(sha for sha, _p, t in parsed if t.session_id in by),
        "excluded_discovery": sorted(exclude_shas),
    }


def format_report(rep: dict) -> str:
    lo, hi = rep["ci"]
    lines = [
        "# Stickiness ranker -- powered confirmatory run (stickiness-powered-prereg.md)",
        "",
        f"status: {rep['status']} (gate: {rep['gate']}; floor {rep['min_sessions']} sessions)",
        f"decision: {rep['decision'] or 'none -- no verdict may be rendered'}",
        f"mean(stickiness_live - bm25) nDCG@{K}: {rep['mean_delta']:+.4f}; clustered 90% CI "
        f"[{lo:+.4f}, {hi:+.4f}]; MEI {rep['mei']}",
        f"n_sessions={rep['n_sessions']} n_fixtures={rep['n_fixtures']} "
        f"needed_n={rep['needed_n']} range~{rep['needed_n_range']}",
        "arms (nDCG@10): " + ", ".join(f"{k}={v:.3f}" for k, v in rep["arms"].items()),
        "gap sweep (lift, CI): " + "; ".join(
            f"g={g}: {v['mean']:+.3f} [{v['ci'][0]:+.3f}, {v['ci'][1]:+.3f}] n_fx={v['n_fixtures']}"
            for g, v in rep["gaps"].items()),
        "decaying (gap-5 lift < half gap-0): "
        + ("n/a (no gap-5 fixtures)" if rep["decaying"] is None else str(rep["decaying"])),
    ]
    wd = rep["with_discovery"]
    if wd is not None:
        lines.append(f"sensitivity incl. discovery sessions: {wd['mean']:+.4f} "
                     f"[{wd['ci'][0]:+.4f}, {wd['ci'][1]:+.4f}] n_sessions={wd['n_sessions']}")
    if rep["lexical"] is not None:
        lx = rep["lexical"]
        lines.append(f"lexical-bias: gold_R@3={lx['gold_r3']:.3f} control={lx['control_r3']:.3f} "
                     f"degenerate={lx['degenerate']}")
    lines += [f"sessions per project: {rep['sessions_per_project']}",
              f"manifest (sha256[:16]): {rep['manifest']}"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m context_curator.eval.stickiness")
    ap.add_argument("paths", nargs="*", help=f"transcripts (default: {DEFAULT_GLOB})")
    ap.add_argument("--min-sessions", type=int, default=MIN_SESSIONS)
    ap.add_argument("--include-discovery", action="store_true",
                    help="do not exclude the 5 discovery sessions (sensitivity only)")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    paths = a.paths or sorted(glob.glob(DEFAULT_GLOB, recursive=True))
    rep = run(paths, exclude_shas=set() if a.include_discovery else DISCOVERY_SHAS,
              min_sessions=a.min_sessions, seed=a.seed)
    print(format_report(rep))


if __name__ == "__main__":
    main()
