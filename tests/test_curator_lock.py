from context_curator.curator import runtime


def test_nonblocking_lock_single_holder(tmp_path):
    lock_path = str(tmp_path / ".curator.lock")
    h1 = runtime.try_lock(lock_path)
    assert h1 is not None
    h2 = runtime.try_lock(lock_path)
    assert h2 is None
    runtime.release_lock(h1)
    h3 = runtime.try_lock(lock_path)
    assert h3 is not None
    runtime.release_lock(h3)
