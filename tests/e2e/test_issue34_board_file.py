"""A board file alone does not tell the lead that a worker is done.

Foil wakes a seat by storing mail and typing a nudge. A result file or a
status file wakes no one. The fake harness handles mail present at startup,
then reads nudge lines. It does not poll the board.
"""

from __future__ import annotations

import contextlib
import io
import json
import time
from pathlib import Path

import pytest

from foil.cli import main
from foil.project import foil_root
from tests.e2e.test_scenarios import _close, _prepare, _wait

pytestmark = pytest.mark.e2e

_RESULT = """---
contract: result/v1
task: write the result
author: implementer-1
branch: foil/implementer-1
outcome: pass
---

Done.
"""

_STATUS = """---
contract: status/v1
state: done
updated: 2026-10-16T00:00:00Z
questions: []
---

## Checklist

- [x] write the result (implementer-1)
"""


def _state(name: str) -> str:
    listed = io.StringIO()
    with contextlib.redirect_stdout(listed):
        assert main(["seat", "list"]) == 0
    for line in listed.getvalue().splitlines():
        parts = line.split("\t")
        if parts and parts[0] == name and len(parts) >= 3:
            return parts[2]
    return ""


def _scripts(repo: Path, relative: str, text: str, *, send: bool) -> None:
    fake = foil_root(repo) / "run" / "fake"
    fake.mkdir(parents=True, exist_ok=True)
    lead = {
        "steps": [
            {
                "when": {"mail_contains": "start the work"},
                "do": [
                    {"sh": "foil seat spawn implementer --task 'write the result'"},
                    {"write": "board/notes/lead-waiting.txt", "text": "waiting"},
                ],
            },
            {
                "when": {"mail_contains": "result is ready"},
                "do": [{"write": "board/notes/lead-saw-mail.txt", "text": "saw"}],
            },
        ]
    }
    actions: list[dict[str, str]] = [{"write": f"board/{relative}", "text": text}]
    if send:
        actions.append({"send": "lead", "text": "result is ready"})
    worker = {
        "steps": [
            {
                "when": {"mail_contains": "write the result"},
                "do": actions,
            }
        ]
    }
    (fake / "lead.json").write_text(json.dumps(lead), encoding="utf-8")
    (fake / "implementer-1.json").write_text(json.dumps(worker), encoding="utf-8")


def _run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    relative: str,
    text: str,
    snippet: str,
    send: bool,
) -> None:
    repo = _prepare(tmp_path, "scenario-2", monkeypatch)
    try:
        _scripts(repo, relative, text, send=send)
        assert main(["seat", "spawn", "lead", "--task", "start the work"]) == 0
        board = foil_root(repo) / "board"
        target = board / relative
        waiting = board / "notes" / "lead-waiting.txt"
        saw = board / "notes" / "lead-saw-mail.txt"
        _wait(repo, lambda: target.is_file() and waiting.is_file())
        if send:
            _wait(repo, saw.is_file)
            assert saw.read_text(encoding="utf-8") == "saw"
            mailbox = board / "mail" / "lead"
            bodies = [path.read_text(encoding="utf-8") for path in mailbox.glob("*.md")]
            assert any("result is ready" in body for body in bodies)
        else:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                assert not saw.is_file()
                time.sleep(0.2)
            mails = list((board / "mail" / "lead").glob("*.md"))
            assert len(mails) == 1
            body = mails[0].read_text(encoding="utf-8")
            assert "start the work" in body
            assert "result is ready" not in body
        assert snippet in target.read_text(encoding="utf-8")
        assert _state("lead") == "alive"
        assert _state("implementer-1") == "alive"
    finally:
        _close(repo)


def test_result_file_without_send_leaves_the_lead_unaware(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(
        tmp_path,
        monkeypatch,
        relative="results/implementer-1.md",
        text=_RESULT,
        snippet="outcome: pass",
        send=False,
    )


def test_status_file_without_send_leaves_the_lead_unaware(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(
        tmp_path,
        monkeypatch,
        relative="status.md",
        text=_STATUS,
        snippet="state: done",
        send=False,
    )


def test_send_wakes_the_lead_after_the_result_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(
        tmp_path,
        monkeypatch,
        relative="results/implementer-1.md",
        text=_RESULT,
        snippet="outcome: pass",
        send=True,
    )
