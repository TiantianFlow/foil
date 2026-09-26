"""User-facing errors and a credential-shaped-text heuristic."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_SECRET_KEY_PARTS = (
    "access_token",
    "api_key",
    "apikey",
    "auth_token",
    "authorization",
    "cookie",
    "credential",
    "password",
    "private_key",
    "private-key",
    "privatekey",
    "secret",
)
_SECRET_VALUE_PREFIXES = (
    "bearer ",
    "gho_",
    "ghp_",
    "ghr_",
    "ghs_",
    "ghu_",
    "github_pat_",
    "sk-",
    "xox",
)


class FoilError(Exception):
    """A one-line failure already formatted for stderr."""


def die(message: str) -> None:
    raise FoilError(message if message.startswith("foil:") else f"foil: {message}")


def assert_no_secret(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if isinstance(key, str) and any(part in key.lower() for part in _SECRET_KEY_PARTS):
                raise FoilError("foil: credential-shaped text refused")
            assert_no_secret(child)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            assert_no_secret(child)
        return
    if isinstance(value, str):
        lowered = value.strip().lower()
        if any(lowered.startswith(prefix) for prefix in _SECRET_VALUE_PREFIXES):
            raise FoilError("foil: credential-shaped text refused")
