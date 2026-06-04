"""Self-correcting precision gate (design §5.4). Replaces a guessed MIN_SESSIONS: a verdict is
rendered only when the session-clustered CI is precise enough to place the effect (width <= MEI);
otherwise INCONCLUSIVE-underpowered with a computed needed-N (how many sessions to reach the
target)."""
from __future__ import annotations

import math
from dataclasses import dataclass

_FLOOR = 3   # definitional minimum sessions for the clustered bootstrap to be meaningful


@dataclass
class GateResult:
    status: str               # "harness-only" | "inconclusive-underpowered" | "verdict"
    needed_n: int | None      # sessions needed to reach width<=MEI (only when underpowered)


def precision_gate(*, lo: float, hi: float, n_sessions: int, mei: float = 0.10) -> GateResult:
    if n_sessions < _FLOOR:
        return GateResult("harness-only", None)
    width = hi - lo
    if not math.isfinite(width) or width > mei:
        if math.isfinite(width) and width > 0:
            needed = math.ceil(n_sessions * (width / mei) ** 2)
        else:
            needed = None
        return GateResult("inconclusive-underpowered", needed)
    return GateResult("verdict", None)
