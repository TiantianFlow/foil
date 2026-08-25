"""Reviewed memory store invariants (CAP-021)."""

from __future__ import annotations

from pathlib import Path

import pytest

from foil.memory import LessonState, MemoryError, MemoryLesson, MemoryStore


def test_supersede_refuses_to_overwrite_an_existing_replacement(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    original = store.propose(
        MemoryLesson(
            fleet_id="fleet-1",
            lesson_id="lesson-a",
            body="Ack mail first.",
            proposed_by="curator",
            source_task_id="task-1",
        )
    )
    store.accept("fleet-1", original.lesson_id, "manager")
    store.propose(
        MemoryLesson(
            fleet_id="fleet-1",
            lesson_id="lesson-b",
            body="Unrelated accepted lesson.",
            proposed_by="curator",
            source_task_id="task-2",
        )
    )

    with pytest.raises(MemoryError, match="already exists"):
        store.supersede(
            "fleet-1",
            "lesson-a",
            author="curator",
            replacement_id="lesson-b",
            body="This must not replace lesson-b.",
            task_id="task-1",
        )

    kept = store.read("fleet-1", "lesson-b")
    assert kept.body == "Unrelated accepted lesson."
    assert kept.state is LessonState.PROPOSED
    assert store.read("fleet-1", "lesson-a").state is LessonState.ACCEPTED


def test_supersede_is_idempotent_for_the_same_replacement(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.propose(
        MemoryLesson(
            fleet_id="fleet-1",
            lesson_id="lesson-a",
            body="Ack mail first.",
            proposed_by="curator",
            source_task_id="task-1",
        )
    )
    first = store.supersede(
        "fleet-1",
        "lesson-a",
        author="curator",
        replacement_id="lesson-b",
        body="Ack mail, then poll-status.",
        task_id="task-1",
    )
    second = store.supersede(
        "fleet-1",
        "lesson-a",
        author="curator",
        replacement_id="lesson-b",
        body="Ack mail, then poll-status.",
        task_id="task-1",
    )
    assert first.state is LessonState.SUPERSEDED
    assert second.replacement_lesson_id == "lesson-b"
    assert store.read("fleet-1", "lesson-b").body == "Ack mail, then poll-status."
