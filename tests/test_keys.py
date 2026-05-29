from context_curator.keys import is_within_scope, tenant_prefix


def test_tenant_prefix_extracted():
    key = "proj:acme:tenant:t42:offloaded:9"
    assert tenant_prefix(key) == "proj:acme:tenant:t42"


def test_tenant_prefix_absent():
    assert tenant_prefix("shared:contracts:auth") is None
    assert tenant_prefix("session:abc:turn_log") is None


def test_within_scope_none_allows_everything():
    assert is_within_scope("anything:at:all", None) is True


def test_within_scope_exact_and_child():
    scope = "proj:acme:tenant:t42"
    assert is_within_scope("proj:acme:tenant:t42", scope) is True
    assert is_within_scope("proj:acme:tenant:t42:offloaded:9", scope) is True


def test_within_scope_rejects_sibling_and_prefix_collision():
    scope = "proj:acme:tenant:t42"
    # different tenant
    assert is_within_scope("proj:acme:tenant:t99:x", scope) is False
    # prefix collision must NOT match (t420 is not inside t42)
    assert is_within_scope("proj:acme:tenant:t420:x", scope) is False
