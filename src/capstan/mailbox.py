"""Immutable file mailbox messages and acknowledgements (CAP-020, CAP-034, CAP-036)."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Generic, TypeVar

from capstan.registry import (
    SCHEMA_VERSION,
    _assert_no_secret,
    _read_json_file,
    _validate_id,
    _validate_timestamp,
)

MAX_BODY_BYTES = 64 * 1024


class MailboxError(ValueError):
    """Mailbox input or storage is unsafe or malformed."""


class UnsupportedMailboxSchemaVersion(MailboxError):
    """The mailbox schema cannot be interpreted by this version."""


class DuplicateMessageError(MailboxError):
    """A stable mailbox ID already identifies different immutable content."""


def _mailbox_id(value: str, field_name: str) -> None:
    try:
        _validate_id(value, field_name)
    except ValueError as exc:
        raise MailboxError(f"{field_name} is not a safe stable ID") from exc


def _timestamp(value: str) -> None:
    try:
        _validate_timestamp(value)
    except ValueError as exc:
        raise MailboxError(str(exc)) from exc


def _secret_check(value: Any, *, path: str) -> None:
    try:
        _assert_no_secret(value, path=path)
    except ValueError as exc:
        raise MailboxError(str(exc)) from exc


def _validate_extensions(value: Any) -> None:
    if not isinstance(value, dict):
        raise MailboxError("extensions must be an object")
    _secret_check(value, path="extensions")


@dataclass(frozen=True, slots=True)
class MailboxMessage:
    fleet_id: str
    message_id: str
    recipient_seat_id: str
    sender_id: str
    created_at: str
    body: str
    causal_task_id: str | None = None
    extensions: dict[str, Any] = field(default_factory=dict)
    schema_version: int = field(default=SCHEMA_VERSION, init=False)

    def __post_init__(self) -> None:
        _mailbox_id(self.fleet_id, "fleet_id")
        _mailbox_id(self.message_id, "message_id")
        _mailbox_id(self.recipient_seat_id, "recipient_seat_id")
        _mailbox_id(self.sender_id, "sender_id")
        if self.causal_task_id is not None:
            _mailbox_id(self.causal_task_id, "causal_task_id")
        _timestamp(self.created_at)
        if not isinstance(self.body, str) or not self.body.strip():
            raise MailboxError("body must be a non-empty string")
        if len(self.body.encode("utf-8")) > MAX_BODY_BYTES:
            raise MailboxError(f"body exceeds {MAX_BODY_BYTES} bytes")
        _secret_check(self.body, path="body")
        _validate_extensions(self.extensions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "fleet_id": self.fleet_id,
            "message_id": self.message_id,
            "recipient_seat_id": self.recipient_seat_id,
            "sender_id": self.sender_id,
            "created_at": self.created_at,
            "body": self.body,
            "causal_task_id": self.causal_task_id,
            "extensions": self.extensions,
        }

    @classmethod
    def from_dict(cls, payload: Any) -> MailboxMessage:
        expected = {
            "schema_version",
            "fleet_id",
            "message_id",
            "recipient_seat_id",
            "sender_id",
            "created_at",
            "body",
            "causal_task_id",
            "extensions",
        }
        values = _validated_record(payload, expected, "message")
        return cls(**values)


@dataclass(frozen=True, slots=True)
class Acknowledgement:
    fleet_id: str
    message_id: str
    acknowledgement_id: str
    recipient_seat_id: str
    acknowledged_by: str
    acknowledged_at: str
    extensions: dict[str, Any] = field(default_factory=dict)
    schema_version: int = field(default=SCHEMA_VERSION, init=False)

    def __post_init__(self) -> None:
        _mailbox_id(self.fleet_id, "fleet_id")
        _mailbox_id(self.message_id, "message_id")
        _mailbox_id(self.acknowledgement_id, "acknowledgement_id")
        _mailbox_id(self.recipient_seat_id, "recipient_seat_id")
        _mailbox_id(self.acknowledged_by, "acknowledged_by")
        _timestamp(self.acknowledged_at)
        _validate_extensions(self.extensions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "fleet_id": self.fleet_id,
            "message_id": self.message_id,
            "acknowledgement_id": self.acknowledgement_id,
            "recipient_seat_id": self.recipient_seat_id,
            "acknowledged_by": self.acknowledged_by,
            "acknowledged_at": self.acknowledged_at,
            "extensions": self.extensions,
        }

    @classmethod
    def from_dict(cls, payload: Any) -> Acknowledgement:
        expected = {
            "schema_version",
            "fleet_id",
            "message_id",
            "acknowledgement_id",
            "recipient_seat_id",
            "acknowledged_by",
            "acknowledged_at",
            "extensions",
        }
        values = _validated_record(payload, expected, "acknowledgement")
        return cls(**values)


def _validated_record(payload: Any, expected: set[str], kind: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise MailboxError(f"{kind} record must be an object")
    unknown = set(payload) - expected
    missing = expected - set(payload)
    if unknown:
        raise MailboxError(f"{kind} record has unknown fields")
    if missing:
        raise MailboxError(f"{kind} record has missing fields")
    version = payload["schema_version"]
    if type(version) is not int or version != SCHEMA_VERSION:
        raise UnsupportedMailboxSchemaVersion(
            f"unsupported mailbox schema version: {version}"
        )
    return {key: payload[key] for key in expected - {"schema_version"}}


RecordT = TypeVar("RecordT", MailboxMessage, Acknowledgement)


@dataclass(frozen=True, slots=True)
class ImmutableWriteResult(Generic[RecordT]):
    record: RecordT
    path: Path
    duplicate: bool


class DeliveryState(StrEnum):
    QUEUED = "queued"
    ACKNOWLEDGED = "acknowledged"


@dataclass(frozen=True, slots=True)
class DeliveryRecord:
    message: MailboxMessage
    acknowledgement: Acknowledgement | None
    state: DeliveryState

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": self.message.to_dict(),
            "acknowledgement": (
                None if self.acknowledgement is None else self.acknowledgement.to_dict()
            ),
            "state": self.state.value,
        }


class MailboxStore:
    def __init__(self, state_root: Path | str):
        self.state_root = Path(state_root)

    def _mailbox_dir(self, fleet_id: str, seat_id: str) -> Path:
        _mailbox_id(fleet_id, "fleet_id")
        _mailbox_id(seat_id, "recipient_seat_id")
        return (
            self.state_root
            / f"v{SCHEMA_VERSION}"
            / "fleets"
            / fleet_id
            / "mailboxes"
            / seat_id
        )

    def message_path(self, fleet_id: str, seat_id: str, message_id: str) -> Path:
        _mailbox_id(message_id, "message_id")
        return self._mailbox_dir(fleet_id, seat_id) / "inbox" / f"{message_id}.json"

    def acknowledgement_path(self, fleet_id: str, seat_id: str, message_id: str) -> Path:
        _mailbox_id(message_id, "message_id")
        return self._mailbox_dir(fleet_id, seat_id) / "ack" / f"{message_id}.json"

    def enqueue(self, message: MailboxMessage) -> ImmutableWriteResult[MailboxMessage]:
        path = self.message_path(
            message.fleet_id,
            message.recipient_seat_id,
            message.message_id,
        )
        duplicate = self._create_immutable(path, message.to_dict(), MailboxMessage.from_dict)
        return ImmutableWriteResult(message, path, duplicate)

    def acknowledge(
        self, acknowledgement: Acknowledgement
    ) -> ImmutableWriteResult[Acknowledgement]:
        try:
            self.read_message(
                acknowledgement.fleet_id,
                acknowledgement.recipient_seat_id,
                acknowledgement.message_id,
            )
        except FileNotFoundError as exc:
            raise MailboxError("message does not exist for acknowledgement") from exc
        path = self.acknowledgement_path(
            acknowledgement.fleet_id,
            acknowledgement.recipient_seat_id,
            acknowledgement.message_id,
        )
        duplicate = self._create_immutable(
            path,
            acknowledgement.to_dict(),
            Acknowledgement.from_dict,
        )
        return ImmutableWriteResult(acknowledgement, path, duplicate)

    def read_message(self, fleet_id: str, seat_id: str, message_id: str) -> MailboxMessage:
        path = self.message_path(fleet_id, seat_id, message_id)
        payload = _read_json_file(path, error_type=MailboxError)
        message = MailboxMessage.from_dict(payload)
        if (
            message.fleet_id != fleet_id
            or message.recipient_seat_id != seat_id
            or message.message_id != message_id
        ):
            raise MailboxError("message identity does not match its path")
        return message

    def read_acknowledgement(
        self, fleet_id: str, seat_id: str, message_id: str
    ) -> Acknowledgement:
        path = self.acknowledgement_path(fleet_id, seat_id, message_id)
        payload = _read_json_file(path, error_type=MailboxError)
        acknowledgement = Acknowledgement.from_dict(payload)
        if (
            acknowledgement.fleet_id != fleet_id
            or acknowledgement.recipient_seat_id != seat_id
            or acknowledgement.message_id != message_id
        ):
            raise MailboxError("acknowledgement identity does not match its path")
        return acknowledgement

    def delivery(self, fleet_id: str, seat_id: str, message_id: str) -> DeliveryRecord:
        message = self.read_message(fleet_id, seat_id, message_id)
        try:
            acknowledgement = self.read_acknowledgement(fleet_id, seat_id, message_id)
        except FileNotFoundError:
            acknowledgement = None
        state = (
            DeliveryState.QUEUED
            if acknowledgement is None
            else DeliveryState.ACKNOWLEDGED
        )
        return DeliveryRecord(message, acknowledgement, state)

    def pending(self, fleet_id: str, seat_id: str) -> list[MailboxMessage]:
        inbox = self._mailbox_dir(fleet_id, seat_id) / "inbox"
        self._validate_existing_directory(inbox, missing_ok=True)
        if not inbox.exists():
            return []
        messages: list[MailboxMessage] = []
        for path in sorted(inbox.glob("*.json"), key=lambda item: item.name):
            message = self.read_message(fleet_id, seat_id, path.stem)
            try:
                self.read_acknowledgement(fleet_id, seat_id, message.message_id)
            except FileNotFoundError:
                messages.append(message)
        return messages

    def _create_immutable(self, path: Path, payload: dict[str, Any], decoder) -> bool:
        self._ensure_private_path(path.parent)
        encoded = (
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            + "\n"
        ).encode("utf-8")
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            handle = os.fdopen(descriptor, "wb", closefd=True)
            descriptor = -1
            with handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            created = False
            try:
                os.link(temporary, path, follow_symlinks=False)
                created = True
            except FileExistsError:
                pass
            if not created:
                file_stat = path.lstat()
                if stat.S_ISLNK(file_stat.st_mode):
                    raise MailboxError(f"refusing symlinked record: {path}")
                if not stat.S_ISREG(file_stat.st_mode):
                    raise MailboxError(f"record is not a regular file: {path}")
                existing = _read_json_file(path, error_type=MailboxError)
                if decoder(existing).to_dict() != payload:
                    raise DuplicateMessageError(
                        "stable mailbox ID already exists with different content"
                    )
                return True
            path.chmod(0o600)
            directory_descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
            return False
        finally:
            if descriptor >= 0:
                with suppress(OSError):
                    os.close(descriptor)
            temporary.unlink(missing_ok=True)

    def _ensure_private_path(self, directory: Path) -> None:
        self.state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        current = self.state_root
        self._validate_existing_directory(current)
        current.chmod(0o700)
        relative = directory.relative_to(self.state_root)
        for part in relative.parts:
            current = current / part
            with suppress(FileExistsError):
                current.mkdir(mode=0o700)
            self._validate_existing_directory(current)
            current.chmod(0o700)

    @staticmethod
    def _validate_existing_directory(directory: Path, *, missing_ok: bool = False) -> None:
        try:
            directory_stat = directory.lstat()
        except FileNotFoundError:
            if missing_ok:
                return
            raise
        if stat.S_ISLNK(directory_stat.st_mode):
            raise MailboxError(f"refusing symlinked mailbox directory: {directory}")
        if not stat.S_ISDIR(directory_stat.st_mode):
            raise MailboxError(f"mailbox path is not a directory: {directory}")
