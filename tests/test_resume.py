import pytest

from capstan.resume import (
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
