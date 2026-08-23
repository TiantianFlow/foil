"""Deterministic, shell-safe tmux display names."""

from __future__ import annotations

import hashlib
import re
import unicodedata

_UNSAFE_RUN = re.compile(r"[^a-z0-9]+")


def _digest8(stable_id: str) -> str:
    if not isinstance(stable_id, str) or not stable_id:
        raise ValueError("stable_id must not be empty")
    encoded = stable_id.encode("utf-8", errors="surrogatepass")
    return hashlib.sha256(encoded).hexdigest()[:8]


def _slug(display_name: str, fallback: str) -> str:
    if not isinstance(display_name, str):
        raise ValueError("display_name must be a string")
    normalized = unicodedata.normalize("NFKD", display_name)
    ascii_name = normalized.encode("ascii", errors="ignore").decode("ascii").lower()
    slug = _UNSAFE_RUN.sub("-", ascii_name).strip("-")
    return slug or fallback


def _bounded_name(
    display_name: str,
    stable_id: str,
    *,
    prefix: str | None,
    fallback: str,
    max_length: int,
) -> str:
    suffix = _digest8(stable_id)
    slug = _slug(display_name, fallback)
    fixed_length = len(suffix) + 1
    if prefix is not None:
        fixed_length += len(prefix) + 1
    slug = slug[: max_length - fixed_length].rstrip("-") or fallback
    parts = [slug, suffix] if prefix is None else [prefix, slug, suffix]
    return "-".join(parts)


def tmux_session_name(display_name: str, stable_id: str) -> str:
    """Return `capstan-<fleet-slug>-<identity-suffix>` within 80 characters."""

    return _bounded_name(
        display_name,
        stable_id,
        prefix="capstan",
        fallback="fleet",
        max_length=80,
    )


def tmux_window_name(display_name: str, stable_id: str) -> str:
    """Return `<seat-slug>-<identity-suffix>` within 60 characters."""

    return _bounded_name(
        display_name,
        stable_id,
        prefix=None,
        fallback="seat",
        max_length=60,
    )
