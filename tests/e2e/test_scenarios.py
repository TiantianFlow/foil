"""Scenarios 1–7: real Foil, tmux, and git. Only the agent is foil-fake."""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from foil.cli import main
from foil.project import foil_root
from foil.store import load_registry

pytestmark = pytest.mark.e2e

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("FOIL_SEAT_ID", None)
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


def _prepare(
    tmp_path: Path, fixture: str, monkeypatch: pytest.MonkeyPatch, *, fake: bool = True
) -> Path:
    repo = tmp_path / "project"
    repo.mkdir()
    source = FIXTURES / fixture
    for path in source.rglob("*"):
        if not path.is_file() or "scripts" in path.relative_to(source).parts:
            continue
        target = repo / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    assert _git(repo, "init", "--quiet").returncode == 0
    assert _git(repo, "config", "user.name", "Foil Test").returncode == 0
    assert _git(repo, "config", "user.email", "foil-test@localhost").returncode == 0
    assert _git(repo, "add", "-A").returncode == 0
    assert _git(repo, "commit", "--allow-empty", "-m", "base").returncode == 0
    monkeypatch.chdir(repo)
    assert main(["init"]) == 0
    if not fake:
        chosen = os.environ.get("FOIL_E2E_HARNESS")
        if chosen:
            _set_harness(repo, chosen)
        return repo
    templates = foil_root(repo) / "templates"
    for role in ("lead", "implementer", "reviewer"):
        path = templates / f"{role}.toml"
        text = re.sub(
            r'(?m)^harness = ".*"$', 'harness = "fake"', path.read_text(encoding="utf-8"), count=1
        )
        path.write_text(text, encoding="utf-8")
    return repo


def _scripts(repo: Path, fixture: str, seats: dict[str, str]) -> None:
    source = FIXTURES / fixture / "scripts"
    dest = foil_root(repo) / "run" / "fake"
    dest.mkdir(parents=True, exist_ok=True)
    for seat, name in seats.items():
        shutil.copy(source / name, dest / f"{seat}.json")


def _logs(repo: Path) -> str:
    directory = foil_root(repo) / "run" / "fake"
    if not directory.is_dir():
        return ""
    chunks = []
    for path in sorted(directory.glob("*.log")):
        chunks.append(f"----- {path.name}\n{path.read_text(encoding='utf-8')}")
    return "\n".join(chunks)


def _wait(repo: Path, predicate, timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.2)
    raise AssertionError(f"timed out\n{_logs(repo)}")


def _set_auto(repo: Path) -> None:
    """Live tier only: every default template runs unattended."""

    templates = foil_root(repo) / "templates"
    for role in ("lead", "implementer", "reviewer"):
        path = templates / f"{role}.toml"
        text = re.sub(
            r'(?m)^permission = ".*"$',
            'permission = "auto"',
            path.read_text(encoding="utf-8"),
            count=1,
        )
        path.write_text(text, encoding="utf-8")


def _set_harness(repo: Path, harness: str) -> None:
    """Rewrite the harness line of the three default templates."""

    templates = foil_root(repo) / "templates"
    for role in ("lead", "implementer", "reviewer"):
        path = templates / f"{role}.toml"
        text = re.sub(
            r'(?m)^harness = ".*"$',
            f'harness = "{harness}"',
            path.read_text(encoding="utf-8"),
            count=1,
        )
        path.write_text(text, encoding="utf-8")


def _peek_report(repo: Path) -> str:
    """Pane text for the lead and every other living seat."""

    previous = Path.cwd()
    os.chdir(repo)
    try:
        listed = io.StringIO()
        with contextlib.redirect_stdout(listed):
            main(["seat", "list"])
        names = ["lead"]
        for line in listed.getvalue().splitlines():
            parts = line.split("\t")
            if len(parts) >= 3 and parts[2] == "alive" and parts[0] not in names:
                names.append(parts[0])
        chunks = []
        for name in names:
            out = io.StringIO()
            err = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(["seat", "peek", name])
            pane = f"foil seat peek {name} ({code})\n{out.getvalue()}{err.getvalue()}"
            chunks.append(pane)
        return "\n".join(chunks)
    finally:
        os.chdir(previous)


@contextlib.contextmanager
def _live_failure_panes(repo: Path) -> Iterator[None]:
    try:
        yield
    except Exception as exc:
        raise AssertionError(f"{exc}\n{_peek_report(repo)}") from exc


def _close(repo: Path) -> None:
    session = load_registry(repo).get("tmux_session") or ""
    if session:
        subprocess.run(["tmux", "kill-session", "-t", session], check=False, capture_output=True)


def _status(repo: Path) -> str:
    path = foil_root(repo) / "board" / "status.md"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _no_windows(session: str) -> None:
    listed = subprocess.run(
        ["tmux", "list-windows", "-t", session],
        check=False,
        capture_output=True,
        text=True,
    )
    assert listed.returncode != 0


def _finish_scenario_1(repo: Path, timeout: float = 90) -> None:
    _wait(repo, lambda: "state: done" in _status(repo), timeout)
    assert "done" in _status(repo)
    merged = _git(repo, "merge-base", "--is-ancestor", "foil/implementer-1", "HEAD")
    assert merged.returncode == 0
    checked = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--noconftest", "check_calc.py"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr
    session = str(load_registry(repo)["tmux_session"])
    assert main(["seat", "kill", "--all"]) == 0
    _no_windows(session)


def test_scenario_1_merges_a_reviewed_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _prepare(tmp_path, "scenario-1", monkeypatch)
    try:
        _scripts(
            repo,
            "scenario-1",
            {
                "lead": "lead.json",
                "implementer-1": "implementer.json",
                "reviewer-1": "reviewer.json",
            },
        )
        assert main(["seat", "spawn", "lead", "--task", "make the tests pass"]) == 0
        _finish_scenario_1(repo)
    finally:
        _close(repo)


def test_scenario_2_worker_cannot_change_the_fleet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-2", monkeypatch)
    try:
        assert main(["seat", "spawn", "lead"]) == 0
        assert main(["seat", "spawn", "implementer"]) == 0
        before = (foil_root(repo) / "run" / "registry.json").read_text(encoding="utf-8")
        monkeypatch.setenv("FOIL_SEAT_ID", "implementer-1")
        assert main(["seat", "spawn", "reviewer"]) == 1
        assert capsys.readouterr().err == "foil: not allowed\n"
        assert main(["seat", "kill", "lead"]) == 1
        assert capsys.readouterr().err == "foil: not allowed\n"
        monkeypatch.delenv("FOIL_SEAT_ID")
        after = (foil_root(repo) / "run" / "registry.json").read_text(encoding="utf-8")
        assert after == before
    finally:
        _close(repo)


def test_scenario_3_resumes_after_the_session_dies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _prepare(tmp_path, "scenario-1", monkeypatch)
    try:
        _scripts(
            repo,
            "scenario-1",
            {
                "lead": "lead.json",
                "implementer-1": "implementer.json",
                "reviewer-1": "reviewer.json",
            },
        )
        assert main(["seat", "spawn", "lead", "--task", "make the tests pass"]) == 0
        _wait(repo, lambda: "implementer-1" in load_registry(repo)["seats"])
        session = str(load_registry(repo)["tmux_session"])
        killed = subprocess.run(
            ["tmux", "kill-session", "-t", session],
            check=False,
            capture_output=True,
            text=True,
        )
        assert killed.returncode == 0
        assert main(["seat", "resume"]) == 0
        _finish_scenario_1(repo)
    finally:
        _close(repo)


def test_restart_peek_and_nudge_stay_on_the_same_seat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-2", monkeypatch)

    def pane(name: str) -> str:
        if main(["seat", "peek", name]) != 0:
            capsys.readouterr()
            return ""
        return capsys.readouterr().out

    try:
        assert main(["seat", "spawn", "lead"]) == 0
        assert main(["seat", "spawn", "implementer"]) == 0
        session = str(load_registry(repo)["tmux_session"])
        killed = subprocess.run(
            ["tmux", "kill-session", "-t", session],
            check=False,
            capture_output=True,
            text=True,
        )
        assert killed.returncode == 0
        assert main(["seat", "resume"]) == 0
        _wait(
            repo,
            lambda: (
                "foil-fake lead ready" in pane("lead")
                and "foil-fake implementer-1 ready" in pane("implementer-1")
            ),
        )
        for record in load_registry(repo)["seats"].values():
            subprocess.run(
                ["tmux", "resize-window", "-t", record["window_id"], "-x", "400"],
                check=False,
                capture_output=True,
            )
        assert main(["send", "lead", "ping the lead"]) == 0
        mail = next((foil_root(repo) / "board" / "mail" / "lead").glob("*.md"))
        path = str(mail.resolve())
        _wait(repo, lambda: path in pane("lead"))
        lead = pane("lead")
        worker = pane("implementer-1")
        assert "foil-fake lead ready" in lead
        assert path in lead
        assert "foil-fake implementer-1 ready" not in lead
        assert "foil-fake implementer-1 ready" in worker
        assert "foil-fake lead ready" not in worker
        assert path not in worker
    finally:
        _close(repo)


def test_scenario_4_operator_answer_is_raw_on_the_pane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-4", monkeypatch)
    try:
        _scripts(repo, "scenario-4", {"lead": "lead.json"})
        assert main(["seat", "spawn", "lead", "--task", "ask the operator"]) == 0
        _wait(repo, lambda: "Which color" in _status(repo))
        assert main(["send", "lead", "use blue"]) == 0
        _wait(repo, lambda: "The answer is blue." in _status(repo))
        window = load_registry(repo)["seats"]["lead"]["window_id"]
        captured = subprocess.run(
            ["tmux", "capture-pane", "-p", "-t", window, "-S", "-40"],
            check=True,
            capture_output=True,
            text=True,
        )
        capsys.readouterr()
        assert main(["seat", "peek", "lead"]) == 0
        assert capsys.readouterr().out == captured.stdout
        assert "foil-fake lead ready" in captured.stdout
    finally:
        _close(repo)


def test_scenario_5_accepted_lesson_reaches_the_next_seat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-5", monkeypatch)
    try:
        _scripts(
            repo,
            "scenario-5",
            {"lead": "lead.json", "implementer-1": "implementer.json"},
        )
        assert main(["seat", "spawn", "lead", "--task", "staff a worker"]) == 0
        note = foil_root(repo) / "board" / "notes" / "accept.txt"
        _wait(repo, note.is_file)
        assert note.read_text(encoding="utf-8").strip() == "refused"
        instruction = foil_root(repo) / "run" / "instructions" / "reader.md"
        def lesson_landed() -> bool:
            return (
                instruction.is_file()
                and "check the merged tests" in instruction.read_text(encoding="utf-8")
            )

        _wait(repo, lesson_landed)
        assert main(["memory", "list", "--all"]) == 0
        listed = capsys.readouterr().out
        assert "check the merged tests" in listed
        assert "\taccepted\t" in listed
    finally:
        _close(repo)


def _panes(session: str) -> dict[str, str]:
    listed = subprocess.run(
        ["tmux", "list-windows", "-t", session, "-F", "#{window_id}"],
        check=False,
        capture_output=True,
        text=True,
    )
    panes = {}
    for window in listed.stdout.split():
        captured = subprocess.run(
            ["tmux", "capture-pane", "-p", "-t", window, "-S", "-"],
            check=False,
            capture_output=True,
            text=True,
        )
        panes[window] = captured.stdout
    return panes


def test_busy_fake_handles_both_nudges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-2", monkeypatch)

    def pane(name: str) -> str:
        if main(["seat", "peek", name]) != 0:
            capsys.readouterr()
            return ""
        return capsys.readouterr().out

    try:
        fake = foil_root(repo) / "run" / "fake"
        fake.mkdir(parents=True, exist_ok=True)
        (fake / "lead.json").write_text(
            """{
  "steps": [
    {"when": {"mail_contains": "nudge-one"}, "do": [
      {"write": "board/notes/busy.txt", "text": "started"},
      {"wait": 2},
      {"write": "board/notes/one.txt", "text": "handled"}
    ]},
    {"when": {"mail_contains": "nudge-two"}, "do": [
      {"write": "board/notes/two.txt", "text": "handled"}
    ]}
  ]
}
""",
            encoding="utf-8",
        )
        assert main(["seat", "spawn", "lead"]) == 0
        _wait(repo, lambda: "foil-fake lead ready" in pane("lead"))
        window = load_registry(repo)["seats"]["lead"]["window_id"]
        subprocess.run(
            ["tmux", "resize-window", "-t", window, "-x", "400"],
            check=False,
            capture_output=True,
        )
        assert main(["send", "lead", "nudge-one"]) == 0
        notes = foil_root(repo) / "board" / "notes"
        _wait(repo, (notes / "busy.txt").is_file)
        assert not (notes / "one.txt").exists()
        assert main(["send", "lead", "nudge-two"]) == 0
        second = next(
            path
            for path in (foil_root(repo) / "board" / "mail" / "lead").glob("*.md")
            if "nudge-two" in path.read_text(encoding="utf-8")
        )
        assert str(second.resolve()) in pane("lead")
        assert not (notes / "one.txt").exists()
        _wait(repo, lambda: (notes / "one.txt").is_file() and (notes / "two.txt").is_file())
        assert (notes / "one.txt").read_text(encoding="utf-8") == "handled"
        assert (notes / "two.txt").read_text(encoding="utf-8") == "handled"
    finally:
        _close(repo)


def test_send_to_a_killed_seat_types_into_no_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-2", monkeypatch)
    try:
        assert main(["seat", "spawn", "lead"]) == 0
        assert main(["seat", "spawn", "implementer"]) == 0
        session = str(load_registry(repo)["tmux_session"])
        _wait(repo, lambda: len(_panes(session)) == 2)
        assert main(["seat", "kill", "implementer-1"]) == 0
        assert capsys.readouterr().err == ""
        before = _panes(session)
        assert len(before) == 1
        assert main(["send", "implementer-1", "do not type this"]) == 0
        after = _panes(session)
        assert after == before
        assert all("do not type this" not in text for text in after.values())
        mail = foil_root(repo) / "board" / "mail" / "implementer-1"
        written = next(mail.glob("*.md"))
        assert all(str(written.resolve()) not in text for text in after.values())
    finally:
        _close(repo)


def _install_foil(tmp_path: Path) -> Path:
    venv = tmp_path / "operator-venv"
    python = venv / "bin" / "python"
    created = subprocess.run(
        ["uv", "venv", "--no-config", str(venv)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert created.returncode == 0, created.stderr
    root = Path(__file__).resolve().parents[2]
    installed = subprocess.run(
        ["uv", "pip", "install", "--no-config", "--python", str(python), "--no-deps", str(root)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert installed.returncode == 0, installed.stderr
    return venv / "bin"


def test_operator_uses_the_installed_foil_command(tmp_path: Path) -> None:
    bindir = _install_foil(tmp_path)
    repo = tmp_path / "project"
    repo.mkdir()
    assert _git(repo, "init", "--quiet").returncode == 0
    assert _git(repo, "config", "user.name", "Foil Test").returncode == 0
    assert _git(repo, "config", "user.email", "foil-test@localhost").returncode == 0
    assert _git(repo, "commit", "--allow-empty", "-m", "base").returncode == 0
    environment = os.environ.copy()
    environment.pop("FOIL_SEAT_ID", None)
    environment["PATH"] = os.pathsep.join((str(bindir), environment.get("PATH", "")))

    def foil(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["foil", *args],
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )

    try:
        version = foil("--version")
        assert version.stdout.strip() == "foil 0.3.0", version.stderr
        assert Path(shutil.which("foil", path=environment["PATH"]) or "") == bindir / "foil"
        assert foil("init").returncode == 0
        templates = foil_root(repo) / "templates"
        for role in ("lead", "implementer", "reviewer"):
            path = templates / f"{role}.toml"
            text = re.sub(
                r'(?m)^harness = ".*"$',
                'harness = "fake"',
                path.read_text(encoding="utf-8"),
                count=1,
            )
            path.write_text(text, encoding="utf-8")
        fake = foil_root(repo) / "run" / "fake"
        fake.mkdir(parents=True, exist_ok=True)
        (fake / "lead.json").write_text(
            """{
  "steps": [
    {"when": {"mail_contains": "operator goal"}, "do": [
      {"write": "board/status.md", "text": "state: working\\n"}
    ]},
    {"when": {"mail_contains": "operator ping"}, "do": [
      {"write": "board/notes/ping.txt", "text": "pong\\n"}
    ]}
  ]
}
""",
            encoding="utf-8",
        )
        spawned = foil("seat", "spawn", "lead", "--task", "operator goal")
        assert spawned.returncode == 0, spawned.stderr
        _wait(repo, lambda: "state: working" in _status(repo))
        sent = foil("send", "lead", "operator ping")
        assert sent.returncode == 0, sent.stderr
        note = foil_root(repo) / "board" / "notes" / "ping.txt"
        _wait(repo, note.is_file)
        assert note.read_text(encoding="utf-8") == "pong\n"
        listed = foil("seat", "list")
        assert listed.returncode == 0, listed.stderr
        assert "lead\tlead\talive\t" in listed.stdout
        session = str(load_registry(repo)["tmux_session"])
        assert foil("seat", "kill", "--all").returncode == 0
        _no_windows(session)
    finally:
        _close(repo)


def test_scenario_6_errors_are_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-6", monkeypatch)
    assert main(["send", "operator", "hello"]) == 1
    captured = capsys.readouterr()
    assert captured.err == "foil: unknown seat 'operator'\n"
    assert "Traceback" not in captured.err

    assert main(["seat", "spawn", "nope"]) == 1
    captured = capsys.readouterr()
    assert captured.err == "foil: unknown template 'nope'\n"
    assert "Traceback" not in captured.err

    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    assert main(["seat", "list"]) == 1
    captured = capsys.readouterr()
    assert captured.err == "foil: not a git repository\n"
    assert captured.err.count("\n") == 1
    assert "Traceback" not in captured.err
    assert repo.is_dir()


def test_scenario_7_ai_native_onboarding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """GitHub issue 8: harness reporting, HOME-by-name, and an inlined persona."""

    repo = tmp_path / "project"
    repo.mkdir()
    source = FIXTURES / "scenario-7"
    for path in source.rglob("*"):
        if not path.is_file() or "scripts" in path.relative_to(source).parts:
            continue
        target = repo / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    assert _git(repo, "init", "--quiet").returncode == 0
    assert _git(repo, "config", "user.name", "Foil Test").returncode == 0
    assert _git(repo, "config", "user.email", "foil-test@localhost").returncode == 0
    assert _git(repo, "add", "-A").returncode == 0
    assert _git(repo, "commit", "--allow-empty", "-m", "base").returncode == 0
    monkeypatch.chdir(repo)

    # A real `foil init`: the report names every installed harness and says
    # which stored id each default template uses.
    assert main(["init"]) == 0
    first = capsys.readouterr().out
    assert (
        "Installed harnesses, in id order (a tiebreak, not a ranking): "
        "claude, codex, gemini, grok, opencode"
    ) in first
    assert "lead: claude (first installed id)" in first
    assert "implementer: claude (first installed id)" in first
    assert "reviewer: codex (second installed id)" in first
    assert (
        "The three default templates use two different harness ids. "
        "Two ids can still run the same program or model; set model on a template to choose one."
    ) in first

    # Store three different harness ids in the templates, then init again.
    # Five harnesses stay installed throughout, so a guarantee computed only
    # from the install count would still say "two". The report must instead
    # read the ids actually stored in the three templates and say "three".
    templates = foil_root(repo) / "templates"
    implementer = templates / "implementer.toml"
    reviewer = templates / "reviewer.toml"
    implementer.write_text(
        implementer.read_text(encoding="utf-8").replace(
            'harness = "claude"', 'harness = "codex"', 1
        ),
        encoding="utf-8",
    )
    reviewer.write_text(
        reviewer.read_text(encoding="utf-8").replace(
            'harness = "codex"', 'harness = "gemini"', 1
        ),
        encoding="utf-8",
    )
    assert main(["init"]) == 0
    second = capsys.readouterr().out
    assert (
        "Installed harnesses, in id order (a tiebreak, not a ranking): "
        "claude, codex, gemini, grok, opencode"
    ) in second
    assert "lead: claude (left alone)" in second
    assert "implementer: codex (left alone)" in second
    assert "reviewer: gemini (left alone)" in second
    assert (
        "The three default templates use three different harness ids. "
        "Two ids can still run the same program or model; set model on a template to choose one."
    ) in second

    for role in ("lead", "implementer", "reviewer"):
        path = templates / f"{role}.toml"
        text = re.sub(
            r'(?m)^harness = ".*"$', 'harness = "fake"', path.read_text(encoding="utf-8"), count=1
        )
        path.write_text(text, encoding="utf-8")

    try:
        _scripts(repo, "scenario-7", {"lead": "lead.json"})
        task = "AI-native onboarding: https://github.com/TiantianFlow/foil/issues/8"
        assert main(["seat", "spawn", "lead", "--task", task]) == 0

        # The launch plan forwards HOME by name; it never stores the value.
        plan_path = foil_root(repo) / "run" / "plans" / "lead.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        assert "HOME" in plan["env_forward"]
        assert "HOME" not in plan["env"]
        assert plan["env"] == {"FOIL_SEAT_ID": "lead"}

        # The instruction file carries the persona's own words, not only a
        # pointer to the persona file.
        instruction = foil_root(repo) / "run" / "instructions" / "lead.md"
        text = instruction.read_text(encoding="utf-8")
        persona_words = (templates / "personas" / "lead.md").read_text(encoding="utf-8").strip()
        assert persona_words in text
        assert "personas/lead.md" not in text

        _wait(repo, lambda: "state: done" in _status(repo))
        assert "state: done" in _status(repo)

        session = str(load_registry(repo)["tmux_session"])
        assert main(["seat", "kill", "--all"]) == 0
        _no_windows(session)
    finally:
        _close(repo)


def test_unattended_templates_and_peek_on_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert os.environ.get("FOIL_E2E_LIVE") != "1"
    repo = _prepare(tmp_path, "scenario-2", monkeypatch)
    _set_auto(repo)
    try:
        for role in ("lead", "implementer", "reviewer"):
            text = (foil_root(repo) / "templates" / f"{role}.toml").read_text(encoding="utf-8")
            assert 'permission = "auto"\n' in text
            assert 'harness = "fake"\n' in text
        assert main(["seat", "spawn", "lead"]) == 0
        deadline = time.monotonic() + 20
        ready = ""
        while time.monotonic() < deadline:
            ready = _peek_report(repo)
            if "foil-fake lead ready" in ready:
                break
            time.sleep(0.2)
        assert "foil-fake lead ready" in ready
        with (
            pytest.raises(AssertionError, match="timed out") as caught,
            _live_failure_panes(repo),
        ):
            _wait(repo, lambda: False, 0)
        message = str(caught.value)
        assert "foil seat peek lead" in message
        assert "foil-fake lead ready" in message
    finally:
        _close(repo)


def test_e2e_harness_override_rewrites_the_three_templates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FOIL_E2E_HARNESS", "codex")
    repo = _prepare(tmp_path, "scenario-6", monkeypatch, fake=False)
    for role in ("lead", "implementer", "reviewer"):
        text = (foil_root(repo) / "templates" / f"{role}.toml").read_text(encoding="utf-8")
        assert 'harness = "codex"\n' in text
