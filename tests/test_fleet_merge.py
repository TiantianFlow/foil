"""foil fleet merge: the tap's command, and the done-goal finish block."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from foil.cli import main
from foil.inbox import collect
from foil.master import deliver, directory, seed
from foil.merge import finish_block
from foil.project import foil_root
from foil.store import update_registry
from tests.test_init import _identity_environ
from tests.test_mail_board_ops import _init_git_repository
from tests.test_spawn import _repo


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        capture_output=True,
        text=True,
        env=_identity_environ(),
    )


def _branch(repo: Path) -> str:
    return _git(repo, "branch", "--show-current").stdout.strip()


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _commit(repo: Path, name: str, text: str, message: str) -> None:
    (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-m", message)


def _ahead(repo: Path, branch: str, name: str, text: str) -> str:
    """Commit ``text`` on ``branch`` and return to the branch that was current."""

    current = _branch(repo)
    _git(repo, "checkout", "-b", branch)
    _commit(repo, name, text, branch)
    _git(repo, "checkout", current)
    return current


def _items(repo: Path) -> list[dict]:
    return collect(repo, ["main"], foil_root(repo).name)


def _both(repo: Path) -> list[dict]:
    return collect(repo, ["main", "side"], foil_root(repo).name)


def _side(repo: Path) -> None:
    side = foil_root(repo) / "fleets" / "side" / "run"
    side.mkdir(parents=True)
    (side / "registry.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "fleet_id": "side",
                "tmux_session": "",
                "lead": "",
                "seats": {},
                "rotation": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _merge_item(repo: Path) -> dict | None:
    found = [item for item in _items(repo) if item["kind"] == "merge"]
    return found[0] if found else None


def test_merge_lands_the_goal_and_a_second_run_stays_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo, "base.txt", "base\n", "base")
    current = _ahead(repo, "foil/goal-main", "from-goal.txt", "from the goal\n")
    assert not (repo / "from-goal.txt").exists()
    capsys.readouterr()

    assert main(["fleet", "merge"]) == 0
    assert capsys.readouterr().out == f"foil: merged foil/goal-main into {current}\n"
    assert (repo / "from-goal.txt").read_text(encoding="utf-8") == "from the goal\n"
    assert _branch(repo) == current
    assert _git(repo, "status", "--porcelain").stdout == ""

    assert main(["fleet", "merge"]) == 0
    assert capsys.readouterr().out == f"foil: merged foil/goal-main into {current}\n"
    assert _merge_item(repo) is None


def test_into_checks_out_that_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo, "base.txt", "base\n", "base")
    _git(repo, "branch", "topic")
    current = _ahead(repo, "foil/goal-main", "from-goal.txt", "from the goal\n")
    capsys.readouterr()
    assert main(["fleet", "merge", "--into", "topic"]) == 0
    assert capsys.readouterr().out == "foil: merged foil/goal-main into topic\n"
    assert _branch(repo) == "topic"
    assert current != "topic"
    assert (repo / "from-goal.txt").is_file()


def test_a_dirty_checkout_is_one_line_and_an_inbox_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo, "base.txt", "base\n", "base")
    _ahead(repo, "foil/goal-main", "from-goal.txt", "from the goal\n")
    (repo / "scratch.txt").write_text("nope\n", encoding="utf-8")
    head = _head(repo)

    assert main(["fleet", "merge"]) == 1
    assert capsys.readouterr().err == "foil: checkout is dirty\n"
    assert _head(repo) == head
    assert (repo / "scratch.txt").read_text(encoding="utf-8") == "nope\n"
    assert not (repo / "from-goal.txt").exists()
    assert _git(repo, "rev-parse", "-q", "--verify", "MERGE_HEAD", check=False).returncode != 0
    item = _merge_item(repo)
    assert item is not None
    assert item["title"] == "checkout is dirty"
    assert item["subject"] == "merge"
    assert item["id"] == "merge"
    assert item["rank"] == 4

    (repo / "scratch.txt").unlink()
    assert main(["fleet", "merge"]) == 0
    capsys.readouterr()
    assert (repo / "from-goal.txt").is_file()
    assert _merge_item(repo) is None


def test_a_conflict_is_one_line_and_leaves_the_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo, "file.txt", "base\n", "base")
    current = _branch(repo)
    _git(repo, "checkout", "-b", "topic")
    _commit(repo, "file.txt", "topic\n", "topic")
    _git(repo, "checkout", current)
    _git(repo, "checkout", "-b", "foil/goal-main")
    _commit(repo, "file.txt", "goal\n", "goal")
    _git(repo, "checkout", current)
    head = _head(repo)
    # A runner with no committer identity makes `git merge` exit before MERGE_HEAD.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in (
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
    ):
        monkeypatch.delenv(name, raising=False)

    assert main(["fleet", "merge", "--into", "topic"]) == 1
    assert capsys.readouterr().err == "foil: merge conflict\n"
    assert _branch(repo) == current
    assert _head(repo) == head
    assert (repo / "file.txt").read_text(encoding="utf-8") == "base\n"
    assert _git(repo, "status", "--porcelain").stdout == ""
    assert _git(repo, "rev-parse", "-q", "--verify", "MERGE_HEAD", check=False).returncode != 0
    topic = _git(repo, "show", "topic:file.txt").stdout
    assert topic == "topic\n"
    item = _merge_item(repo)
    assert item is not None
    assert item["title"] == "merge conflict"


def test_a_seat_cannot_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo, "base.txt", "base\n", "base")
    _ahead(repo, "foil/goal-main", "from-goal.txt", "from the goal\n")
    monkeypatch.setenv("FOIL_SEAT_ID", "implementer-1")
    head = _head(repo)
    assert main(["fleet", "merge"]) == 1
    assert capsys.readouterr().err == "foil: not allowed\n"
    assert _head(repo) == head
    assert not (foil_root(repo) / "fleets" / "main" / "run" / "merge.json").exists()


def test_the_lead_branch_wins_when_the_name_was_made_unique(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo, "base.txt", "base\n", "base")
    _ahead(repo, "foil/goal-main", "from-goal.txt", "literal\n")
    _ahead(repo, "foil/goal-main-2", "from-goal.txt", "recorded\n")

    def _lead(registry: dict) -> None:
        registry["seats"]["lead"] = {
            "name": "lead",
            "template": "lead",
            "state": "",  # a launched lead stores "", not a shown state
            "branch": "foil/goal-main-2",
        }

    update_registry(repo, _lead)
    assert main(["fleet", "merge"]) == 0
    capsys.readouterr()
    assert (repo / "from-goal.txt").read_text(encoding="utf-8") == "recorded\n"


def test_a_symlink_refusal_merges_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo, "base.txt", "base\n", "base")
    _ahead(repo, "foil/goal-main", "from-goal.txt", "from the goal\n")
    run = foil_root(repo) / "fleets" / "main" / "run"
    (run / "merge.json").symlink_to(run / "fleet.json")
    head = _head(repo)
    assert main(["fleet", "merge"]) == 1
    assert capsys.readouterr().err == "foil: refusing symlink\n"
    assert _head(repo) == head
    assert not (repo / "from-goal.txt").exists()


def test_the_finish_block_cites_the_reviewer_and_does_not_merge(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A long pytest path can slice to a store key that starts with "-".
    # checkout_for_key refuses that key, and the wake then has no finish block.
    root = Path(tempfile.mkdtemp(prefix="foil"))
    repo = root / "p"
    repo.mkdir()
    try:
        _init_git_repository(repo)
        monkeypatch.chdir(repo)
        assert main(["init"]) == 0
        _finish(repo, capsys)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _finish(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _commit(repo, "base.txt", "base\n", "base")
    _ahead(repo, "foil/goal-main", "from-goal.txt", "from the goal\n")
    board = foil_root(repo) / "fleets" / "main" / "board"
    (board / "status.md").write_text(
        "---\ncontract: status/v1\nstate: done\nupdated: 2026-01-04T00:00:00Z\n---\n"
        "## Mandate\nDone when: the greeting is hello.\n",
        encoding="utf-8",
    )

    def _seats(registry: dict) -> None:
        registry["seats"]["reviewer-1"] = {"name": "reviewer-1", "template": "reviewer"}
        registry["seats"]["implementer-1"] = {"name": "implementer-1", "template": "implementer"}

    update_registry(repo, _seats)
    results = board / "results"
    results.mkdir(exist_ok=True)
    (results / "impl.md").write_text(
        "---\ncontract: result/v1\nauthor: implementer-1\noutcome: pass\n---\n"
        "## Changed\nnot this one\n",
        encoding="utf-8",
    )
    older = results / "old.md"
    newer = results / "review.md"
    body = (
        "---\ncontract: result/v1\ntask: goal\nauthor: reviewer-1\noutcome: pass\n---\n"
        "\n## Changed\nThe greeting is hello.\n\n## Tests\npytest passed.\n"
        "\n## Risks\nNone found.\n"
    )
    older.write_text(body.replace("hello", "stale"), encoding="utf-8")
    newer.write_text(body, encoding="utf-8")
    os.utime(older, (1_000, 1_000))
    os.utime(newer, (2_000, 2_000))
    key = foil_root(repo).name
    assert key[:1].isalnum()
    block = finish_block(repo, "main", key)
    cited = f"projects/{key}/fleets/main/board/results/review.md"
    assert cited in block
    assert "The greeting is hello." in block
    assert "pytest passed." in block
    assert "None found." in block
    assert "stale" not in block
    assert "not this one" not in block
    assert block.startswith("card: merge\n")
    head = _head(repo)
    mailed = deliver(block, None)
    assert cited in mailed.read_text(encoding="utf-8")
    assert _head(repo) == head
    assert not (repo / "from-goal.txt").exists()

    text = seed(directory(), [], [f"{key}\x1fmain\x1fgoal"])
    assert "finish:" in text
    assert cited in text
    quiet = seed(directory(), [], [f"{key}\x1fmain\x1fquestion:one"])
    assert "finish:" not in quiet

    assert any(item["subject"] == "goal" for item in _items(repo))
    assert main(["fleet", "merge"]) == 0
    capsys.readouterr()
    assert (repo / "from-goal.txt").is_file()


def test_different_hunks_of_one_file_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo, "note.txt", "a\nb\nc\n", "base")
    _side(repo)
    current = _branch(repo)
    _git(repo, "checkout", "-b", "foil/goal-main")
    _commit(repo, "note.txt", "A\nb\nc\n", "main line")
    _git(repo, "checkout", current)
    _git(repo, "checkout", "-b", "foil/goal-side")
    _commit(repo, "note.txt", "a\nb\nC\n", "side line")
    _git(repo, "checkout", current)
    head = _head(repo)

    assert main(["fleet", "merge"]) == 1
    assert capsys.readouterr().err == "foil: fleets touch the same files\n"
    assert _head(repo) == head
    assert _branch(repo) == current
    assert (repo / "note.txt").read_text(encoding="utf-8") == "a\nb\nc\n"
    assert _git(repo, "rev-parse", "-q", "--verify", "MERGE_HEAD", check=False).returncode != 0
    conflicts = [item for item in _both(repo) if item["kind"] == "conflict"]
    assert {item["fleet"] for item in conflicts} == {"main", "side"}
    assert conflicts[0]["subject"] == "conflict"
    assert "same files as" in conflicts[0]["title"]
    assert conflicts[0]["evidence"] == "note.txt"
    merge = _merge_item(repo)
    assert merge is not None
    assert merge["title"] == "fleets touch the same files"


def test_a_second_fleet_on_another_file_still_merges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo, "base.txt", "base\n", "base")
    _side(repo)
    current = _ahead(repo, "foil/goal-main", "from-goal.txt", "from the goal\n")
    _ahead(repo, "foil/goal-side", "from-side.txt", "from the side\n")
    assert not any(item["kind"] == "conflict" for item in _both(repo))
    capsys.readouterr()
    assert main(["fleet", "merge"]) == 0
    assert capsys.readouterr().out == f"foil: merged foil/goal-main into {current}\n"
    assert (repo / "from-goal.txt").is_file()
    assert not (repo / "from-side.txt").exists()
