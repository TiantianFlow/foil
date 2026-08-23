import hashlib
import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from capstan.naming import tmux_session_name, tmux_window_name

SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def digest8(stable_id: str) -> str:
    return hashlib.sha256(stable_id.encode("utf-8")).hexdigest()[:8]


def test_session_name_is_readable_safe_and_identity_suffixed() -> None:
    name = tmux_session_name("Fleet 🚀/Alpha", "fleet:123")

    assert name == f"capstan-fleet-alpha-{digest8('fleet:123')}"
    assert SAFE_NAME.fullmatch(name)


def test_window_name_uses_fallback_slug() -> None:
    assert tmux_window_name("🚀", "seat:123") == f"seat-{digest8('seat:123')}"


def test_stable_ids_prevent_display_name_collisions() -> None:
    assert tmux_session_name("same", "fleet-a") != tmux_session_name("same", "fleet-b")
    assert tmux_window_name("same", "seat-a") != tmux_window_name("same", "seat-b")


@given(display_name=st.text(), stable_id=st.text(min_size=1))
def test_generated_names_are_bounded_and_shell_safe(display_name: str, stable_id: str) -> None:
    session = tmux_session_name(display_name, stable_id)
    window = tmux_window_name(display_name, stable_id)

    assert len(session) <= 80
    assert len(window) <= 60
    assert SAFE_NAME.fullmatch(session)
    assert SAFE_NAME.fullmatch(window)


@pytest.mark.parametrize("factory", [tmux_session_name, tmux_window_name])
def test_empty_stable_id_is_rejected(factory) -> None:
    with pytest.raises(ValueError, match="stable_id"):
        factory("display", "")
