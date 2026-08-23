"""Pure seat relaunch precedence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TmuxProbeState(StrEnum):
    ALIVE = "alive"
    DEAD = "dead"
    UNKNOWN = "unknown"


class ResumeAction(StrEnum):
    REVIVE_TMUX = "revive_tmux"
    RESUME_NATIVE = "resume_native"
    START_FRESH = "start_fresh"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class ResumeEvidence:
    tmux_state: TmuxProbeState
    force_fresh: bool = False
    tmux_identity_matches: bool | None = None
    native_session_id: str | None = None
    native_resume_supported: bool = False
    native_resume_failed: bool = False

    def __post_init__(self) -> None:
        if self.native_resume_failed and not self.native_session_id:
            raise ValueError("native_resume_failed requires a native_session_id")


@dataclass(frozen=True, slots=True)
class ResumeDecision:
    action: ResumeAction
    reason: str


def resolve_resume(evidence: ResumeEvidence) -> ResumeDecision:
    """Choose a side-effect-free relaunch action from validated evidence."""

    if evidence.force_fresh:
        return ResumeDecision(ResumeAction.START_FRESH, "operator_requested_fresh")

    if evidence.tmux_state is TmuxProbeState.UNKNOWN:
        return ResumeDecision(ResumeAction.BLOCKED, "tmux_liveness_unknown")

    mismatch_prefix = ""
    if evidence.tmux_state is TmuxProbeState.ALIVE:
        if evidence.tmux_identity_matches is None:
            return ResumeDecision(ResumeAction.BLOCKED, "tmux_identity_unknown")
        if evidence.tmux_identity_matches:
            return ResumeDecision(ResumeAction.REVIVE_TMUX, "matching_tmux_alive")
        mismatch_prefix = "tmux_identity_mismatch_"

    if evidence.native_resume_failed:
        return ResumeDecision(ResumeAction.START_FRESH, "native_resume_failed")

    if not evidence.native_session_id:
        return ResumeDecision(
            ResumeAction.START_FRESH,
            f"{mismatch_prefix}native_session_missing",
        )

    if not evidence.native_resume_supported:
        return ResumeDecision(
            ResumeAction.START_FRESH,
            f"{mismatch_prefix}native_resume_unsupported",
        )

    return ResumeDecision(
        ResumeAction.RESUME_NATIVE,
        f"{mismatch_prefix}native_session_available",
    )
