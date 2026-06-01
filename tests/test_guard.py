from context_curator.guard.config import load_config
from context_curator.guard.paths import is_sensitive_path
from context_curator.guard.secrets import scan_secrets


def _cfg():
    return load_config()


def test_sensitive_paths_positive():
    g = _cfg().sensitive_globs
    assert is_sensitive_path(".env", g)
    assert is_sensitive_path("config/.env.production", g)
    assert is_sensitive_path("/home/u/.aws/credentials", g)
    assert is_sensitive_path("deploy/id_rsa", g)
    assert is_sensitive_path("secrets-prod", g)            # basename, no slash


def test_sensitive_paths_negative():
    g = _cfg().sensitive_globs
    assert not is_sensitive_path("src/app.py", g)
    assert not is_sensitive_path("README.md", g)


def test_secret_positive():
    p = _cfg().secret_patterns
    assert scan_secrets("AKIAIOSFODNN7EXAMPLE", p) == "aws-access-key-id"
    assert scan_secrets("-----BEGIN OPENSSH PRIVATE KEY-----", p) == "private-key-block"
    assert scan_secrets('api_key = "abcdef0123456789ABCDEF"', p) == "generic-secret"


def test_secret_negative_realistic_code():
    p = _cfg().secret_patterns
    # the I5 false-positive class: ordinary code must NOT trip the guard
    assert scan_secrets("token = make_token()", p) is None
    assert scan_secrets("password = get_hashed_password_value", p) is None
    assert scan_secrets("commit 9f1c2e7a4b8d3f6e0a1b2c3d4e5f60718293a4b5", p) is None


def test_scan_is_capped(monkeypatch):
    from context_curator.guard import config
    p = _cfg().secret_patterns
    huge = "x" * (config.GUARD_MAX_SCAN + 1000) + 'api_key="abcdef0123456789ABCD"'
    # secret is past the cap -> not found (bounded scan, no hang)
    assert scan_secrets(huge, p) is None
