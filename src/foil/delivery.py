"""Controller mailbox delivery and bounded tmux wake (CAP-020, CAP-034, CAP-036)."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from foil.mailbox import DeliveryState, MailboxMessage, MailboxStore
from foil.registry import RegistryStore, TmuxTarget

_SESSION_ID = re.compile(r"^\$[0-9]+$")
_WINDOW_ID = re.compile(r"^@[0-9]+$")


class WakeState(StrEnum):
    SENT = "sent"
    SKIPPED_EMPTY = "skipped_empty"
    DUPLICATE_SKIPPED = "duplicate_skipped"
    INVALID_TARGET = "invalid_target"
    NOT_RUNNING = "not_running"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class WakeResult:
    state: WakeState

    def to_dict(self) -> dict[str, str]:
        return {"state": self.state.value}


class TmuxWakeService:
    WAKE_TEXT = "Foil mail is queued. Poll your mailbox and acknowledge messages."
    MAX_WAKE_BYTES = 128
    TIMEOUT_SECONDS = 3.0

    def __init__(
        self,
        *,
        executable: str = "tmux",
        runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
    ):
        self.executable = executable
        self.runner = runner
        if len(self.WAKE_TEXT.encode("utf-8")) > self.MAX_WAKE_BYTES:
            raise ValueError("wake text exceeds its fixed byte bound")

    def wake_if_queued(self, target: TmuxTarget, *, queued_count: int) -> WakeResult:
        if type(queued_count) is not int or queued_count < 0:
            raise ValueError("queued_count must be a non-negative integer")
        if queued_count == 0:
            return WakeResult(WakeState.SKIPPED_EMPTY)
        if not self._valid_target(target):
            return WakeResult(WakeState.INVALID_TARGET)

        probe = self._run([self.executable, "has-session", "-t", target.session_id])
        if probe is None:
            return WakeResult(WakeState.UNAVAILABLE)
        if probe.returncode != 0:
            return WakeResult(WakeState.NOT_RUNNING)

        literal = self._run(
            [
                self.executable,
                "send-keys",
                "-t",
                target.window_id,
                "-l",
                self.WAKE_TEXT,
            ]
        )
        if literal is None or literal.returncode != 0:
            return WakeResult(WakeState.FAILED)

        enter = self._run(
            [self.executable, "send-keys", "-t", target.window_id, "Enter"]
        )
        if enter is None or enter.returncode != 0:
            return WakeResult(WakeState.FAILED)
        return WakeResult(WakeState.SENT)

    def _run(self, argv: list[str]) -> subprocess.CompletedProcess[Any] | None:
        try:
            return self.runner(
                argv,
                check=False,
                shell=False,
                timeout=self.TIMEOUT_SECONDS,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return None

    @staticmethod
    def _valid_target(target: TmuxTarget) -> bool:
        return bool(
            isinstance(target, TmuxTarget)
            and isinstance(target.session_id, str)
            and _SESSION_ID.fullmatch(target.session_id)
            and isinstance(target.window_id, str)
            and _WINDOW_ID.fullmatch(target.window_id)
        )


@dataclass(frozen=True, slots=True)
class MessageDeliveryResult:
    message: MailboxMessage
    duplicate: bool
    state: DeliveryState
    wake: WakeResult

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": self.message.to_dict(),
            "duplicate": self.duplicate,
            "delivery": {
                "state": self.state.value,
                "wake": self.wake.to_dict(),
            },
        }


class MessageDeliveryService:
    def __init__(
        self,
        mailbox: MailboxStore,
        registry: RegistryStore,
        waker: TmuxWakeService,
    ):
        self.mailbox = mailbox
        self.registry = registry
        self.waker = waker

    def send(self, message: MailboxMessage) -> MessageDeliveryResult:
        seat = self.registry.read_seat(message.fleet_id, message.recipient_seat_id)
        written = self.mailbox.enqueue(message)
        if written.duplicate:
            wake = WakeResult(WakeState.DUPLICATE_SKIPPED)
        else:
            queued_count = len(
                self.mailbox.pending(message.fleet_id, message.recipient_seat_id)
            )
            wake = self.waker.wake_if_queued(seat.tmux, queued_count=queued_count)
        return MessageDeliveryResult(
            message=message,
            duplicate=written.duplicate,
            state=DeliveryState.QUEUED,
            wake=wake,
        )
