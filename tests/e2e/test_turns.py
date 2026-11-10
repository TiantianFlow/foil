"""Scenarios 8-16: the turn loop with real Foil, tmux, and git. Only the agent is foil-fake."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from foil.board_ops import split_front_matter
from foil.cli import main
from foil.project import foil_root, store_root
from foil.runner import RESTARTED
from foil.store import load_registry
from tests.e2e.test_scenarios import _close, _git, _prepare, _scripts, _wait

pytestmark = pytest.mark.e2e


def _fleet(repo: Path, fleet: str = "main") -> Path:
    return foil_root(repo) / "fleets" / fleet


def _board(repo: Path, fleet: str = "main") -> Path:
    return (_fleet(repo, fleet) / "board").resolve()


def _script(repo: Path, seat: str, script: dict, fleet: str = "main") -> None:
    fake = _fleet(repo, fleet) / "run" / "fake"
    fake.mkdir(parents=True, exist_ok=True)
    (fake / f"{seat}.json").write_text(json.dumps(script), encoding="utf-8")


def _turns(repo: Path, seat: str, fleet: str = "main") -> list[dict]:
    directory = _fleet(repo, fleet) / "run" / "fake" / f"{seat}.turns"
    if not directory.is_dir():
        return []
    return [json.loads(path.read_text()) for path in sorted(directory.glob("*.json"))]


def _out(capsys: pytest.CaptureFixture[str], *argv: str) -> str:
    capsys.readouterr()
    assert main(list(argv)) == 0, capsys.readouterr().err
    return capsys.readouterr().out


def _rows(capsys: pytest.CaptureFixture[str]) -> dict[str, dict]:
    return {row["name"]: row for row in json.loads(_out(capsys, "seat", "list", "--json"))}


def _settled(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """No launched seat working for three checks in a row, a second apart."""

    def quiet() -> bool:
        for _ in range(3):
            if any(row["state"] == "working" for row in _rows(capsys).values()):
                return False
            time.sleep(1)
        return True

    _wait(repo, quiet)


def _mails(repo: Path, mailbox: str, needle: str) -> list[Path]:
    directory = _board(repo) / "mail" / mailbox
    if not directory.is_dir():
        return []
    return [path for path in sorted(directory.glob("*.md")) if needle in path.read_text()]


def _short_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("foil.lifecycle.RETRY", (0.2, 0.2, 0.2))


def _turn_timeout(role: str, seconds: int) -> None:
    roles = store_root() / "roles.toml"
    text = roles.read_text(encoding="utf-8")
    text = text.replace(f'id = "{role}"\n', f'id = "{role}"\nturn_timeout = {seconds}\n', 1)
    roles.write_text(text, encoding="utf-8")


def _outside(*argv: str, fleet: str = "") -> subprocess.CompletedProcess[str]:
    environment = {k: v for k, v in os.environ.items() if k != "FOIL_SEAT_ID"}
    if fleet:
        environment["FOIL_FLEET"] = fleet
    return subprocess.run(
        ["foil", *argv], check=False, capture_output=True, text=True, env=environment
    )


def test_scenario_8_joined_lead_merges_real_code_from_background_waits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for variable in ("CODEX_THREAD_ID", "CLAUDE_CODE_SESSION_ID", "GROK_SESSION_ID"):
        monkeypatch.delenv(variable, raising=False)
    repo = _prepare(tmp_path, "scenario-1", monkeypatch)
    try:
        _scripts(
            repo,
            "scenario-1",
            {"implementer-1": "implementer.json", "reviewer-1": "reviewer.json"},
        )
        assert main(["seat", "join", "lead"]) == 0
        assert main(["seat", "spawn", "implementer", "--task", "fix the failing test"]) == 0
        wakes = 0
        merged = False
        deadline = time.monotonic() + 120
        while not merged and time.monotonic() < deadline:
            # The outside agent's background wait: it returns when something is new.
            waiting = subprocess.Popen(
                ["foil", "mail", "wait", "--timeout", "60"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                env={k: v for k, v in os.environ.items() if k != "FOIL_SEAT_ID"},
            )
            woke, _ = waiting.communicate(timeout=90)
            assert waiting.returncode == 0
            if woke.strip() == "no new mail":
                continue
            wakes += 1
            read = _outside("mail", "wait", "--timeout", "0")
            assert read.returncode == 0 and read.stdout == woke
            for line in woke.splitlines():
                text = Path(line.split()[-1]).read_text(encoding="utf-8")
                if "fixed on foil/implementer-1" in text:
                    spawned = _outside("seat", "spawn", "reviewer", "--task", "review the fix")
                    assert spawned.returncode == 0, spawned.stderr
                elif "approved" in text:
                    result = _git(repo, "merge", "--no-edit", "-m", "merge", "foil/implementer-1")
                    assert result.returncode == 0, result.stderr
                    merged = True
        assert merged and wakes >= 2
        checked = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--noconftest", "check_calc.py"],
            cwd=repo, check=False, capture_output=True, text=True,
        )
        assert checked.returncode == 0, checked.stdout + checked.stderr
        seats = load_registry(repo)["seats"]
        assert seats["lead"]["state"] == "joined" and not seats["lead"].get("window_id")
    finally:
        _close(repo)


def test_scenario_9_turns_batch_mail_and_inline_short_bodies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-2", monkeypatch)
    try:
        _script(repo, "lead", {"faults": [{}, {"delay": 4}]})
        assert main(["seat", "spawn", "lead"]) == 0
        _wait(repo, lambda: len(_turns(repo, "lead")) == 1)
        _settled(repo, capsys)
        assert main(["send", "lead", "a" * 599]) == 0
        assert main(["send", "lead", "b" * 600]) == 0
        _wait(repo, lambda: len(_turns(repo, "lead")) == 2)
        assert main(["send", "lead", "third"]) == 0
        _wait(repo, lambda: len(_turns(repo, "lead")) == 3)
        _settled(repo, capsys)
        turns = _turns(repo, "lead")
        assert len(turns) == 3
        short, long, third = (
            _mails(repo, "lead", text)[0] for text in ("a" * 599, "b" * 600, "third")
        )
        sizes = [len(split_front_matter(path.read_text())[1].encode()) for path in (short, long)]
        assert sizes == [600, 601]
        second = turns[1]["prompt"]
        assert "New for lead (2):" in second
        assert str(short) in second and str(long) in second
        assert f"--- {short}" in second and f"--- {long}" not in second
        assert str(third) not in second
        assert "New for lead (1):" in turns[2]["prompt"] and str(third) in turns[2]["prompt"]
        assert str(short) not in turns[2]["prompt"]
        assert all(turn["stdin"] == "" for turn in turns)
        assert [turn["first"] for turn in turns] == [True, False, False]
        assert len({turn["session"] for turn in turns}) == 1
    finally:
        _close(repo)


def test_scenario_10_faults_retry_pause_timeout_leftovers_and_silence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-2", monkeypatch)
    _short_retries(monkeypatch)
    _turn_timeout("implementer", 3)
    ok = {"send": "lead", "text": "ok"}
    report = {"steps": [{"when": {"mail_contains": "task"}, "do": [ok]}]}
    try:
        _script(repo, "implementer-1", {**report, "faults": [{"exit": 3}, {"exit": 3}]})
        _script(repo, "reviewer-1", {**report, "faults": [{"exit": 5}] * 4})
        _script(repo, "implementer-2", {**report, "faults": [{"hang": True}]})
        _script(repo, "implementer-3", {"faults": [{"leave_child": True}]})
        assert main(["seat", "spawn", "lead"]) == 0
        for role in ("implementer", "reviewer", "implementer", "implementer"):
            assert main(["seat", "spawn", role, "--task", f"task for {role}"]) == 0

        # Exits non-zero twice, then passes: the same batch each time, then delivered.
        _wait(repo, lambda: len(_mails(repo, "lead", "\nok\n")) >= 1, timeout=60)
        retried = _turns(repo, "implementer-1")
        task = next((_board(repo) / "mail" / "implementer-1").glob("*.md"))
        assert len(retried) == 3 and all(str(task) in turn["prompt"] for turn in retried)

        # Fails every turn: paused, one mail to the lead, and resume runs it again.
        _wait(repo, lambda: _rows(capsys)["reviewer-1"]["state"] == "paused", timeout=60)
        paused = _mails(repo, "lead", "reviewer-1 paused after 4 failed turns (exit 5).")
        assert len(paused) == 1 and "-foil-" in paused[0].name
        assert len(_turns(repo, "reviewer-1")) == 4
        assert main(["seat", "resume", "reviewer-1"]) == 0
        _wait(repo, lambda: len(_turns(repo, "reviewer-1")) == 5)
        _wait(repo, lambda: _rows(capsys)["reviewer-1"]["state"] == "idle")
        assert str(next((_board(repo) / "mail" / "reviewer-1").glob("*.md"))) in _turns(
            repo, "reviewer-1"
        )[4]["prompt"]

        # Hangs past turn_timeout: ended, mailed, not retried.
        timed = "implementer-2's turn passed turn_timeout and was stopped."
        _wait(repo, lambda: len(_mails(repo, "lead", timed)) == 1, timeout=30)
        _settled(repo, capsys)
        assert len(_turns(repo, "implementer-2")) == 1
        assert _rows(capsys)["implementer-2"]["last_exit"] == -1

        # Leaves a child and reports nothing: the child is ended, and one mail says so.
        silent = "implementer-3 ended a turn without mail, a post, or a result."
        _wait(repo, lambda: len(_mails(repo, "lead", silent)) == 1)
        child = int((_fleet(repo) / "run" / "fake" / "implementer-3.child").read_text())

        def gone() -> bool:
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                return True
            status = Path(f"/proc/{child}/status")
            return status.is_file() and "State:\tZ" in status.read_text()

        _wait(repo, gone, timeout=10)
        _settled(repo, capsys)
        assert len(_mails(repo, "lead", silent)) == 1
        assert len(_mails(repo, "lead", "paused after")) == 1
    finally:
        _close(repo)


def test_scenario_11_a_restart_delivers_the_in_flight_items_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-2", monkeypatch)
    try:
        _script(repo, "lead", {"faults": [{}, {"delay": 60}]})
        assert main(["seat", "spawn", "lead"]) == 0
        _wait(repo, lambda: len(_turns(repo, "lead")) == 1)
        _settled(repo, capsys)
        assert main(["send", "lead", "in flight"]) == 0
        mail = _mails(repo, "lead", "in flight")[0]
        _wait(repo, lambda: len(_turns(repo, "lead")) == 2)
        window = load_registry(repo)["seats"]["lead"]["window_id"]
        subprocess.run(["tmux", "kill-window", "-t", window], check=True)
        _wait(repo, lambda: _rows(capsys)["lead"]["state"] == "dead")
        assert main(["seat", "resume", "lead"]) == 0
        _wait(repo, lambda: len(_turns(repo, "lead")) == 3)
        first, _, again = _turns(repo, "lead")
        assert again["first"] is False and again["session"] == first["session"]
        assert str(mail) in again["prompt"] and RESTARTED in again["prompt"]
    finally:
        _close(repo)


def test_scenario_12_a_post_wakes_members_paused_seats_and_names_not_decliners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-2", monkeypatch)
    _short_retries(monkeypatch)
    decline = {
        "steps": [
            {
                "when": {"mail_contains": "room-marker"},
                "do": [{"sh": 'foil post --re "$FOIL_FAKE_ITEM" --decline'}],
            }
        ]
    }
    try:
        _script(repo, "implementer-1", decline)
        _script(repo, "implementer-2", {"faults": [{"exit": 1}] * 4})
        assert main(["seat", "spawn", "lead"]) == 0
        for role in ("implementer", "implementer", "reviewer"):
            assert main(["seat", "spawn", role]) == 0
        _wait(repo, lambda: _rows(capsys)["implementer-2"]["state"] == "paused", timeout=60)
        _settled(repo, capsys)
        counts = {name: len(_turns(repo, name)) for name in ("lead", "implementer-1", "reviewer-1")}
        assert main(["post", "--members", "lead,implementer-1,implementer-2",
                     "room-marker for @reviewer-1"]) == 0
        root = next((_board(repo) / "room").rglob("*.md"))
        fields, _ = split_front_matter(root.read_text())
        assert fields["wakes"] == ["implementer-1", "implementer-2", "lead", "reviewer-1"]
        for name, count in counts.items():
            _wait(repo, lambda name=name, count=count: len(_turns(repo, name)) > count)
            assert str(root) in _turns(repo, name)[count]["prompt"]
        assert main(["seat", "resume", "implementer-2"]) == 0
        _wait(repo, lambda: any(str(root) in t["prompt"] for t in _turns(repo, "implementer-2")))

        _wait(repo, lambda: len(list(root.parent.glob("*.md"))) == 2)
        declined = next(path for path in root.parent.glob("*.md") if path != root)
        assert split_front_matter(declined.read_text())[0]["wakes"] == []
        _settled(repo, capsys)
        before = len(_turns(repo, "implementer-1"))
        assert main(["post", "--re", str(root), "a reply"]) == 0
        reply = max(root.parent.glob("*.md"), key=lambda path: path.stat().st_mtime_ns)
        assert "implementer-1" not in split_front_matter(reply.read_text())[0]["wakes"]
        _wait(repo, lambda: any(str(reply) in t["prompt"] for t in _turns(repo, "lead")))
        _settled(repo, capsys)
        assert len(_turns(repo, "implementer-1")) == before
    finally:
        _close(repo)


def test_scenario_13_renew_starts_a_fresh_session_on_the_same_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-2", monkeypatch)
    handoff = {
        "steps": [
            {
                "when": {"mail_contains": "hand off"},
                "do": [
                    {"write": "board/handoffs/implementer-1.md", "text": "t1 is half done.\n"},
                    {"send": "lead", "text": "handoff ready"},
                ],
            }
        ]
    }
    try:
        _script(repo, "implementer-1", handoff)
        assert main(["seat", "spawn", "lead"]) == 0
        assert main(["seat", "spawn", "implementer", "--task", "hand off"]) == 0
        _wait(repo, lambda: len(_mails(repo, "lead", "handoff ready")) == 1)
        _settled(repo, capsys)
        before = dict(load_registry(repo)["seats"]["implementer-1"])
        task = next((_board(repo) / "mail" / "implementer-1").glob("*.md"))
        assert main(["seat", "renew", "implementer-1"]) == 0
        _wait(repo, lambda: len(_turns(repo, "implementer-1")) == 2)
        old, new = _turns(repo, "implementer-1")
        assert new["first"] is True and new["session"] != old["session"]
        assert "board/handoffs/implementer-1.md" in new["prompt"]
        assert str(task) not in new["prompt"]
        assert new["cwd"] == old["cwd"] == before["worktree"]
        after = load_registry(repo)["seats"]["implementer-1"]
        assert after["branch"] == before["branch"] and after["worktree"] == before["worktree"]
        assert after["session_id"] == new["session"] == _rows(capsys)["implementer-1"]["session_id"]
        _settled(repo, capsys)
        assert len(_turns(repo, "implementer-1")) == 2
    finally:
        _close(repo)


def test_scenario_14_two_fleets_integrate_on_their_own_goal_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-2", monkeypatch)
    sessions = []
    try:
        for fleet in ("alpha", "beta"):
            work = {
                "steps": [
                    {
                        "when": {"mail_contains": f"{fleet} goal"},
                        "do": [
                            {"sh": f"git commit --allow-empty -m '{fleet} work'"},
                            {"send": "user", "text": f"{fleet} is done"},
                        ],
                    }
                ]
            }
            monkeypatch.setenv("FOIL_FLEET", fleet)
            note = _out(capsys, "seat", "spawn", "lead", "--task", f"{fleet} goal")
            assert f"foil/goal-{fleet}" in note and "foil mail wait --all-fleets" in note
            _script(repo, "lead", work, fleet)
            sessions.append(str(load_registry(repo)["tmux_session"]))
        monkeypatch.delenv("FOIL_FLEET")
        # The task mail is in flight before the script lands; a resend reaches it either way.
        for fleet in ("alpha", "beta"):
            monkeypatch.setenv("FOIL_FLEET", fleet)
            assert main(["send", "lead", f"{fleet} goal again"]) == 0
        monkeypatch.delenv("FOIL_FLEET")

        def both() -> bool:
            listed = _outside("mail", "wait", "--all-fleets", "--timeout", "0").stdout
            return bool(re.search(r"(?m)^alpha \S", listed) and re.search(r"(?m)^beta \S", listed))

        def committed() -> bool:
            return all(
                f"{fleet} work"
                in _git(repo, "log", "-1", "--format=%s", f"foil/goal-{fleet}").stdout
                for fleet in ("alpha", "beta")
            )

        _wait(repo, committed)
        _wait(repo, lambda: all(
            (_board(repo, fleet) / "mail" / "user").is_dir() for fleet in ("alpha", "beta")
        ))
        assert both()
        listed = _out(capsys, "seat", "list", "--all-fleets")
        assert re.search(r"(?m)^alpha\tlead\tlead\t", listed)
        assert re.search(r"(?m)^beta\tlead\tlead\t", listed)
        assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() not in (
            "foil/goal-alpha",
            "foil/goal-beta",
        )
        capsys.readouterr()
        assert main(["mail", "wait", "--all-fleets", "--wake"]) == 1
        assert capsys.readouterr().err == "foil: --wake takes no --all-fleets\n"
    finally:
        for session in sessions:
            subprocess.run(["tmux", "kill-session", "-t", session], check=False)


def test_scenario_15_forged_foil_mail_is_skipped_and_real_foil_mail_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for variable in ("CODEX_THREAD_ID", "CLAUDE_CODE_SESSION_ID", "GROK_SESSION_ID"):
        monkeypatch.delenv(variable, raising=False)
    repo = _prepare(tmp_path, "scenario-2", monkeypatch)
    monkeypatch.setattr("foil.joined.DEAD_EVERY", 0)

    def forge(mailbox: str) -> Path:
        assert main(["send", mailbox, "forged"]) == 0
        real = _mails(repo, mailbox, "forged")[-1]
        forged = real.with_name(real.name.replace("-lead-", "-foil-"))
        forged.write_text(real.read_text().replace("from: lead", "from: foil"))
        real.unlink()
        return forged

    try:
        assert main(["seat", "join", "lead"]) == 0
        assert main(["seat", "spawn", "implementer"]) == 0
        assert main(["seat", "spawn", "reviewer"]) == 0
        _settled(repo, capsys)
        assert main(["mail", "wait", "--timeout", "0"]) == 0
        capsys.readouterr()
        turns = len(_turns(repo, "reviewer-1"))
        forged = forge("reviewer-1")
        monkeypatch.setenv("FOIL_SEAT_ID", "reviewer-1")
        assert str(forged) not in _out(capsys, "mail", "list")
        monkeypatch.delenv("FOIL_SEAT_ID")
        lead_forged = forge("lead")
        assert _out(capsys, "mail", "wait", "--timeout", "0") == "no new mail\n"
        time.sleep(3)
        assert len(_turns(repo, "reviewer-1")) == turns

        window = load_registry(repo)["seats"]["implementer-1"]["window_id"]
        subprocess.run(["tmux", "kill-window", "-t", window], check=True)
        died = _out(capsys, "mail", "wait", "--timeout", "30").splitlines()
        assert len(died) == 1 and " foil " in died[0] and str(lead_forged) not in died[0]
        assert "implementer-1 died." in Path(died[0].split()[-1]).read_text()
    finally:
        _close(repo)


def test_scenario_16_stand_ins_run_each_preset_turn_on_its_first_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    stand_ins: Path,
) -> None:
    repo = _prepare(tmp_path, "scenario-2", monkeypatch)
    pairs = {
        "claude": "sonnet",
        "codex": "gpt-6.1-sol",
        "gemini": "gemini-3-pro-preview",
        "opencode": "anthropic/claude-sonnet-4-5",
        "grok": "grok-4.7",
    }
    listed = ",".join(f"{harness}/{model}" for harness, model in pairs.items())
    try:
        assert main(["roster", "update", "implementer", f"harness={listed}"]) == 0
        assert main(["seat", "spawn", "lead"]) == 0
        seats = []
        for number, harness in enumerate(pairs, start=1):
            seat = f"implementer-{number}"
            _script(repo, seat, {"steps": [
                {"when": {"mail_contains": "turn"}, "do": [{"send": "lead", "text": "ok"}]}
            ]})
            assert main(["seat", "spawn", "implementer", "--task", "first turn"]) == 0
            assert load_registry(repo)["seats"][seat]["harness"] == harness
            seats.append((seat, harness))
        for seat, _harness in seats:
            _wait(repo, lambda seat=seat: len(_turns(repo, seat)) == 1)
            assert main(["send", seat, "second turn"]) == 0
        for seat, harness in seats:
            _wait(repo, lambda seat=seat: len(_turns(repo, seat)) == 2)
            first, second = _turns(repo, seat)
            assert first["first"] and not second["first"], harness
            assert second["session"] == first["session"], harness
            argv = second["argv"]
            assert argv[0] == harness
            assert "--model" in argv and argv[argv.index("--model") + 1] == pairs[harness]
            if harness == "codex":
                assert argv[argv.index("resume") + 1] == first["session"]
            elif harness == "opencode":
                assert argv[argv.index("--session") + 1] == first["session"]
            else:
                assert argv[argv.index("--resume") + 1] == first["session"]
            recorded = _rows(capsys)[seat]["session_id"]
            assert recorded == first["session"], harness
    finally:
        _close(repo)

