"""Generate synthetic single-session transcripts (no private content).

NOTE on sessions: `parse_transcript` models ONE session per file (it keeps the *last*
`sessionId` seen and emits a single `Trace`; `harvest_trace` stamps that one id onto every
fixture). A real Claude Code transcript file therefore == one session. To genuinely exercise the
split-BY-session path with >=2 sessions, `harvest_corpus` must be given multiple FILES — so this
generator emits TWO files, each carrying a single distinct session ("A" -> sample_a.jsonl,
"B" -> sample_b.jsonl). Each file is built so >=1 downstream-use fixture survives: a target file is
Read first, >=5 distinct filler files are Read next (>=5 prior candidates), then a later turn Greps
the target's directory (a re-fetch -> the target chunk becomes gold).

Run: uv run python tests/eval/_traces/_gen_sample.py
"""
import json
from pathlib import Path


def session(sid: str, root: str) -> list[dict]:
    recs: list[dict] = []

    def user_prompt(text: str) -> None:
        recs.append({"type": "user", "isSidechain": False, "sessionId": sid,
                     "message": {"role": "user", "content": text}})

    def assistant_tool(call_id: str, name: str, args: dict) -> None:
        recs.append({"type": "assistant", "isSidechain": False, "sessionId": sid,
                     "message": {"role": "assistant", "content": [
                         {"type": "tool_use", "id": call_id, "name": name, "input": args}]}})

    def tool_result(call_id: str, content: str) -> None:
        recs.append({"type": "user", "isSidechain": False, "sessionId": sid,
                     "message": {"role": "user", "content": [
                         {"type": "tool_result", "tool_use_id": call_id, "content": content}]}})

    target = f"{root}/auth.py"
    # turn 0: read the target file (the candidate that will become gold)
    user_prompt("open the auth file")
    assistant_tool(f"{sid}-c0", "Read", {"file_path": target})
    tool_result(f"{sid}-c0", "def login(): ...")
    # turns 1..5: read 5 distinct filler files (building >=5 prior candidates)
    for i in range(1, 6):
        user_prompt(f"look at helper {i}")
        cid = f"{sid}-c{i}"
        assistant_tool(cid, "Read", {"file_path": f"{root}/helper_{i}.py"})
        tool_result(cid, f"helper {i} body")
    # turn 6: grep the target's directory -> re-fetch of target -> gold
    user_prompt("search the auth dir for login")
    assistant_tool(f"{sid}-g", "Grep", {"path": root})
    tool_result(f"{sid}-g", "auth.py: def login")
    return recs


def _write(path: Path, sid: str, root: str) -> None:
    recs = session(sid, root)
    path.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
    print(f"wrote {len(recs)} records (session {sid}) to {path}")


def main() -> None:
    here = Path(__file__).parent
    # One file per session — one real `.jsonl` == one session; multiple sessions come from
    # multiple FILES. Each file yields >=1 fixture on its own.
    _write(here / "sample_a.jsonl", "A", "/proj/a")
    _write(here / "sample_b.jsonl", "B", "/proj/b")


if __name__ == "__main__":
    main()
