"""Unit verification for immutable mailbox records (CAP-020, CAP-032, CAP-034)."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from capstan.mailbox import (
    Acknowledgement,
    DeliveryState,
    DuplicateMessageError,
    MailboxError,
    MailboxMessage,
    MailboxStore,
)


def message(**changes) -> MailboxMessage:
    values = {
        "fleet_id": "fleet-1",
        "message_id": "message-1",
        "recipient_seat_id": "seat-1",
        "sender_id": "controller-1",
        "created_at": "2026-08-23T20:00:00Z",
        "body": "Review task-1 and acknowledge this message.",
        "causal_task_id": "task-1",
        "extensions": {},
    }
    values.update(changes)
    return MailboxMessage(**values)


def acknowledgement(**changes) -> Acknowledgement:
    values = {
        "fleet_id": "fleet-1",
        "message_id": "message-1",
        "acknowledgement_id": "ack-1",
        "recipient_seat_id": "seat-1",
        "acknowledged_by": "seat-1",
        "acknowledged_at": "2026-08-23T20:01:00Z",
        "extensions": {},
    }
    values.update(changes)
    return Acknowledgement(**values)


def test_message_and_acknowledgement_are_immutable_private_records(tmp_path: Path) -> None:
    store = MailboxStore(tmp_path)

    written = store.enqueue(message())
    acknowledged = store.acknowledge(acknowledgement())

    assert written.duplicate is False
    assert acknowledged.duplicate is False
    assert stat.S_IMODE(written.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(acknowledged.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(written.path.parent.stat().st_mode) == 0o700
    assert list(written.path.parent.glob("*.tmp")) == []
    assert store.delivery("fleet-1", "seat-1", "message-1").state is DeliveryState.ACKNOWLEDGED


def test_identical_retry_is_reported_as_duplicate_without_rewriting(tmp_path: Path) -> None:
    store = MailboxStore(tmp_path)
    first = store.enqueue(message())
    original = first.path.read_bytes()
    original_inode = first.path.stat().st_ino

    retried = store.enqueue(message())

    assert retried.duplicate is True
    assert first.path.read_bytes() == original
    assert first.path.stat().st_ino == original_inode


def test_conflicting_duplicate_message_id_is_rejected(tmp_path: Path) -> None:
    store = MailboxStore(tmp_path)
    store.enqueue(message())

    with pytest.raises(DuplicateMessageError, match="different content"):
        store.enqueue(message(body="Different work under the same stable ID."))

    assert store.read_message("fleet-1", "seat-1", "message-1") == message()


def test_acknowledgement_requires_an_existing_matching_message(tmp_path: Path) -> None:
    store = MailboxStore(tmp_path)

    with pytest.raises(MailboxError, match="message does not exist"):
        store.acknowledge(acknowledgement())

    store.enqueue(message())
    with pytest.raises(MailboxError, match="message does not exist"):
        store.acknowledge(acknowledgement(recipient_seat_id="seat-2"))


def test_pending_messages_are_sorted_and_exclude_acknowledged(tmp_path: Path) -> None:
    store = MailboxStore(tmp_path)
    store.enqueue(message(message_id="message-b"))
    store.enqueue(message(message_id="message-a"))
    store.acknowledge(acknowledgement(message_id="message-a"))

    assert [item.message_id for item in store.pending("fleet-1", "seat-1")] == ["message-b"]


@pytest.mark.parametrize(
    ("field", "unsafe"),
    [
        ("fleet_id", "../fleet"),
        ("recipient_seat_id", "seat/other"),
        ("message_id", ".."),
        ("sender_id", ""),
        ("causal_task_id", "../../task"),
    ],
)
def test_mailbox_rejects_traversal_and_unsafe_stable_ids(field: str, unsafe: str) -> None:
    with pytest.raises(MailboxError, match="stable ID"):
        message(**{field: unsafe})


def test_symlinked_mailbox_directory_is_rejected(tmp_path: Path) -> None:
    store = MailboxStore(tmp_path)
    mailbox = tmp_path / "v1" / "fleets" / "fleet-1" / "mailboxes" / "seat-1"
    mailbox.parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    mailbox.symlink_to(outside)

    with pytest.raises(MailboxError, match="symlink"):
        store.enqueue(message())


def test_symlinked_message_record_is_neither_followed_nor_replaced(tmp_path: Path) -> None:
    store = MailboxStore(tmp_path)
    inbox = tmp_path / "v1" / "fleets" / "fleet-1" / "mailboxes" / "seat-1" / "inbox"
    inbox.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(message().to_dict()))
    (inbox / "message-1.json").symlink_to(outside)

    with pytest.raises(MailboxError, match="symlink"):
        store.enqueue(message())

    assert outside.read_text() == json.dumps(message().to_dict())
