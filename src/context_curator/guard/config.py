"""Guard configuration (design §3.3). Defaults overridable via $CC_GUARD_CONFIG JSON."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

CAPTURE_TTL_S = 86_400      # live-capture chunk TTL
GUARD_MAX_SCAN = 262_144    # bytes; cap on secret-scan input (bounds regex cost)

DEFAULT_SENSITIVE_GLOBS = [
    "**/.env", "**/.env.*", "**/*.pem", "**/*.key", "**/id_rsa*",
    "**/.aws/**", "**/.ssh/**", "**/secrets/**", "**/*secrets*",
    "**/*.prod.*", "**/*prod*",
]
_GENERIC_SECRET_PAT = (
    r"(?i)(?:api[_-]?key|secret|token|password)"
    r"\s*[:=]\s*['\"][A-Za-z0-9_\-./+=]{16,}['\"]"
)
DEFAULT_SECRET_PATTERNS = [
    ("aws-access-key-id", r"AKIA[0-9A-Z]{16}"),
    ("private-key-block", r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ("aws-secret-quoted", r"(?i)aws_secret_access_key\s*[:=]\s*['\"][A-Za-z0-9/+=]{20,}['\"]"),
    ("generic-secret",    _GENERIC_SECRET_PAT),
]


@dataclass
class GuardConfig:
    sensitive_globs: list[str]
    secret_patterns: list[tuple[str, str]]


def load_config() -> GuardConfig:
    globs = list(DEFAULT_SENSITIVE_GLOBS)
    patterns = list(DEFAULT_SECRET_PATTERNS)
    path = os.environ.get("CC_GUARD_CONFIG")
    candidate = Path(path) if path else Path(".claude/cc-guard.json")
    if candidate.exists():
        data = json.loads(candidate.read_text(encoding="utf-8"))
        if "sensitive_globs" in data:
            globs = list(data["sensitive_globs"])
        if "secret_patterns" in data:
            patterns = [(p[0], p[1]) for p in data["secret_patterns"]]
    return GuardConfig(sensitive_globs=globs, secret_patterns=patterns)
