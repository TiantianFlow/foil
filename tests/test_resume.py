"""Verification for resume precedence (CAP-017–CAP-019, CAP-030, CAP-032)."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from foil.resume import (
    ResumeAction,
    ResumeEvidence,
    TmuxProbeState,
    resolve_resume,
)


@pytest.mark.parametrize(
    ("evidence", "action", "reason"),
    [
        (
            ResumeEvidence(
                force_fresh=True,
                tmux_state=TmuxProbeState.ALIVE,
                tmux_identity_matches=True,
                native_session_id="session-1",
                native_resume_supported=True,
            ),
            ResumeAction.START_FRESH,
            "operator_requested_fresh",
        ),
        (
            ResumeEvidence(
                tmux_state=TmuxProbeState.ALIVE,
                tmux_identity_matches=True,
                native_session_id="session-1",
                native_resume_supported=True,
            ),
            ResumeAction.REVIVE_TMUX,
            "matching_tmux_alive",
        ),
        (
            ResumeEvidence(
                tmux_state=TmuxProbeState.DEAD,
                native_session_id="session-1",
                native_resume_supported=True,
            ),
            ResumeAction.RESUME_NATIVE,
            "native_session_available",
        ),
        (
            ResumeEvidence(tmux_state=TmuxProbeState.DEAD),
            ResumeAction.START_FRESH,
            "native_session_missing",
        ),
        (
            ResumeEvidence(
                tmux_state=TmuxProbeState.DEAD,
                native_session_id="session-1",
                native_resume_supported=False,
            ),
            ResumeAction.START_FRESH,
            "native_resume_unsupported",
        ),
        (
            ResumeEvidence(
                tmux_state=TmuxProbeState.DEAD,
                native_session_id="session-1",
                native_resume_supported=True,
                native_resume_failed=True,
            ),
            ResumeAction.START_FRESH,
            "native_resume_failed",
        ),
        (
            ResumeEvidence(
                tmux_state=TmuxProbeState.ALIVE,
                tmux_identity_matches=False,
                native_session_id="session-1",
                native_resume_supported=True,
            ),
            ResumeAction.RESUME_NATIVE,
            "tmux_identity_mismatch_native_session_available",
        ),
        (
            ResumeEvidence(
                tmux_state=TmuxProbeState.UNKNOWN,
                native_session_id="session-1",
                native_resume_supported=True,
            ),
            ResumeAction.BLOCKED,
            "tmux_liveness_unknown",
        ),
        (
            ResumeEvidence(
                tmux_state=TmuxProbeState.ALIVE,
                tmux_identity_matches=None,
                native_session_id="session-1",
                native_resume_supported=True,
            ),
            ResumeAction.BLOCKED,
            "tmux_identity_unknown",
        ),
    ],
)
def test_resume_precedence(evidence: ResumeEvidence, action: ResumeAction, reason: str) -> None:
    decision = resolve_resume(evidence)

    assert decision.action is action
    assert decision.reason == reason


def test_native_resume_failure_flag_requires_native_evidence() -> None:
    with pytest.raises(ValueError, match="native_resume_failed"):
        ResumeEvidence(
            tmux_state=TmuxProbeState.DEAD,
            native_resume_failed=True,
        )


def test_resume_evidence_rejects_invalid_tmux_state() -> None:
    with pytest.raises(ValueError, match="tmux_state"):
        ResumeEvidence(tmux_state="bogus")


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("force_fresh", "not_a_bool"),
        ("native_resume_supported", "not_a_bool"),
        ("native_resume_failed", "not_a_bool"),
        ("tmux_identity_matches", "not_a_bool"),
        ("native_session_id", 12345),
    ],
)
def test_resume_evidence_rejects_invalid_types(field: str, bad_value) -> None:
    kwargs = {"tmux_state": TmuxProbeState.DEAD, field: bad_value}
    if field == "native_resume_failed":
        kwargs["native_session_id"] = "session-1"
    with pytest.raises((TypeError, ValueError)):
        ResumeEvidence(**kwargs)


def test_resolve_resume_handles_string_tmux_state_equality() -> None:
    evidence = ResumeEvidence(
        tmux_state="alive",
        tmux_identity_matches=True,
        native_session_id="session-1",
        native_resume_supported=True,
    )
    decision = resolve_resume(evidence)
    assert decision.action is ResumeAction.REVIVE_TMUX
    assert decision.reason == "matching_tmux_alive"


def test_mismatched_tmux_with_failed_native_resume_has_prefixed_reason() -> None:
    evidence = ResumeEvidence(
        tmux_state=TmuxProbeState.ALIVE,
        tmux_identity_matches=False,
        native_session_id="session-1",
        native_resume_supported=True,
        native_resume_failed=True,
    )
    decision = resolve_resume(evidence)
    assert decision.action is ResumeAction.START_FRESH
    assert decision.reason == "tmux_identity_mismatch_native_resume_failed"


@given(
    tmux_state=st.sampled_from(list(TmuxProbeState)),
    force_fresh=st.booleans(),
    tmux_identity_matches=st.one_of(st.none(), st.booleans()),
    has_native_session=st.booleans(),
    native_resume_supported=st.booleans(),
    native_resume_failed=st.booleans(),
)
def test_resolve_resume_properties(
    tmux_state: TmuxProbeState,
    force_fresh: bool,
    tmux_identity_matches: bool | None,
    has_native_session: bool,
    native_resume_supported: bool,
    native_resume_failed: bool,
) -> None:
    native_session_id = "session-1" if has_native_session else None
    if native_resume_failed and not native_session_id:
        return

    evidence = ResumeEvidence(
        tmux_state=tmux_state,
        force_fresh=force_fresh,
        tmux_identity_matches=tmux_identity_matches,
        native_session_id=native_session_id,
        native_resume_supported=native_resume_supported,
        native_resume_failed=native_resume_failed,
    )
    decision = resolve_resume(evidence)
    assert isinstance(decision.action, ResumeAction)
    assert isinstance(decision.reason, str)
    assert len(decision.reason) > 0

    if force_fresh:
        assert decision.action is ResumeAction.START_FRESH
        assert decision.reason == "operator_requested_fresh"
    elif tmux_state == TmuxProbeState.UNKNOWN:
        assert decision.action is ResumeAction.BLOCKED
        assert decision.reason == "tmux_liveness_unknown"
    elif tmux_state == TmuxProbeState.ALIVE and tmux_identity_matches is None:
        assert decision.action is ResumeAction.BLOCKED
        assert decision.reason == "tmux_identity_unknown"
    elif tmux_state == TmuxProbeState.ALIVE and tmux_identity_matches is True:
        assert decision.action is ResumeAction.REVIVE_TMUX
        assert decision.reason == "matching_tmux_alive"
    elif tmux_state == TmuxProbeState.ALIVE and tmux_identity_matches is False:
        assert decision.reason.startswith("tmux_identity_mismatch_")
