"""Unit verification for generic bounded tmux wakes (CAP-034, CAP-036)."""

from __future__ import annotations

import subprocess

from foil.delivery import TmuxWakeService, WakeState
from foil.registry import TmuxTarget


def target(**changes) -> TmuxTarget:
    values = {
        "session_name": "foil-example-12345678",
        "window_name": "builder-12345678",
        "session_id": "$1",
        "window_id": "@1",
    }
    values.update(changes)
    return TmuxTarget(**values)


class RecordingRunner:
    def __init__(self, returncodes: list[int] | None = None) -> None:
        self.calls: list[tuple[list[str], dict]] = []
        self.returncodes = iter(returncodes or [0, 0, 0])

    def __call__(self, argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, next(self.returncodes), "", "")


def test_wake_is_skipped_without_queued_messages() -> None:
    runner = RecordingRunner()

    result = TmuxWakeService(runner=runner).wake_if_queued(target(), queued_count=0)

    assert result.state is WakeState.SKIPPED_EMPTY
    assert runner.calls == []


def test_wake_uses_fixed_bounded_literal_argv_and_no_shell() -> None:
    runner = RecordingRunner()
    service = TmuxWakeService(runner=runner)

    result = service.wake_if_queued(target(), queued_count=3)

    assert result.state is WakeState.SENT
    assert [call[0] for call in runner.calls] == [
        ["tmux", "has-session", "-t", "$1"],
        ["tmux", "send-keys", "-t", "@1", "-l", service.WAKE_TEXT],
        ["tmux", "send-keys", "-t", "@1", "Enter"],
    ]
    assert len(service.WAKE_TEXT.encode("utf-8")) <= service.MAX_WAKE_BYTES
    assert all(call[1]["shell"] is False for call in runner.calls)
    assert all(call[1]["timeout"] == service.TIMEOUT_SECONDS for call in runner.calls)


def test_wake_reports_not_running_without_sending_keys() -> None:
    runner = RecordingRunner(returncodes=[1])

    result = TmuxWakeService(runner=runner).wake_if_queued(target(), queued_count=1)

    assert result.state is WakeState.NOT_RUNNING
    assert len(runner.calls) == 1


def test_wake_requires_valid_native_tmux_ids() -> None:
    runner = RecordingRunner()

    result = TmuxWakeService(runner=runner).wake_if_queued(
        target(window_id="@1;run-hostile-command"),
        queued_count=1,
    )

    assert result.state is WakeState.INVALID_TARGET
    assert runner.calls == []


def test_wake_is_bounded_to_one_attempt_without_retry() -> None:
    runner = RecordingRunner(returncodes=[0, 1])

    result = TmuxWakeService(runner=runner).wake_if_queued(target(), queued_count=1)

    assert result.state is WakeState.FAILED
    assert len(runner.calls) == 2
