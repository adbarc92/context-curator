from context_curator.curator import runtime


def test_proof_roundtrip():
    token = runtime.new_token()
    nonce = runtime.new_nonce()
    assert runtime.make_proof(token, nonce) == runtime.make_proof(token, nonce)
    assert runtime.verify_proof(token, nonce, runtime.make_proof(token, nonce))
    assert not runtime.verify_proof(token, nonce, "deadbeef")


def test_runtime_file_atomic_roundtrip_and_mode(tmp_path):
    import os
    p = tmp_path / ".curator.json"
    info = {"state": "ready", "pid": 4321, "port": 5000,
            "token": runtime.new_token(), "dim": 384, "started_at": "2026-06-02T00:00:00+00:00",
            "embedder": "hashing"}
    runtime.write_runtime(p, info)
    assert runtime.read_runtime(p) == info
    if os.name != "nt":                                  # POSIX perms only
        assert (p.stat().st_mode & 0o777) == 0o600


_DEADLINES = {"warming": 15, "provisioning": 300}


def test_discover_absent_file_respawns(tmp_path):
    d = runtime.discover(tmp_path / "nope.json", age_s=lambda _iso: 0.0, deadlines=_DEADLINES,
                         alive=lambda _pid: True)
    assert d.state is None and d.respawn is True


_READY_INFO = {"state": "ready", "pid": 1, "port": 5000, "token": "t",
               "dim": 384, "started_at": "2026-06-02T00:00:00+00:00", "embedder": "bge"}
_WARMING_INFO = {"state": "warming", "pid": 1, "port": 1, "token": "t",
                 "dim": 384, "started_at": "2026-06-02T00:00:00+00:00", "embedder": "bge"}
_WARMING_DEAD_INFO = {"state": "warming", "pid": 999999, "port": 1, "token": "t",
                      "dim": 384, "started_at": "2026-06-02T00:00:00+00:00", "embedder": "bge"}


def test_discover_ready_connects(tmp_path):
    p = tmp_path / ".curator.json"
    runtime.write_runtime(p, _READY_INFO)
    d = runtime.discover(p, age_s=lambda _iso: 1.0, deadlines=_DEADLINES, alive=lambda _pid: True)
    assert d.state == "ready" and d.respawn is False


def test_discover_live_warming_no_respawn(tmp_path):
    p = tmp_path / ".curator.json"
    runtime.write_runtime(p, _WARMING_INFO)
    d = runtime.discover(p, age_s=lambda _iso: 1.0, deadlines=_DEADLINES, alive=lambda _pid: True)
    assert d.state == "warming" and d.respawn is False


def test_discover_dead_or_stale_warming_respawns(tmp_path):
    p = tmp_path / ".curator.json"
    runtime.write_runtime(p, _WARMING_DEAD_INFO)
    dead = runtime.discover(
        p, age_s=lambda _iso: 1.0, deadlines=_DEADLINES, alive=lambda _pid: False
    )
    assert dead.respawn is True
    stale = runtime.discover(
        p, age_s=lambda _iso: 999.0, deadlines=_DEADLINES, alive=lambda _pid: True
    )
    assert stale.respawn is True
