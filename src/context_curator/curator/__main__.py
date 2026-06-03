"""`python -m context_curator.curator` — run the curator; `--prefetch` downloads the model and
exits."""
from __future__ import annotations

import sys

from context_curator.curator.server import make_embedder, run
from context_curator.store.paths import resolve_db_path


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--prefetch" in argv:                  # provisioning prerequisite (design §5.1 C2)
        make_embedder().embed("prefetch")
        return 0
    return run(resolve_db_path())


if __name__ == "__main__":
    raise SystemExit(main())
