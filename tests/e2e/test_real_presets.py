"""Scenario 10: each packaged preset launches and resumes a stand-in for its CLI.

The other scenarios point every role at the fake. Here the roster keeps a
real harness id, so the launch runs that preset's argv, its permission
flags, its resume form and its `set_env` through the plan and the runner,
and a stand-in that reads the command line as that CLI does takes it.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from foil.cli import main
from foil.presets import load_preset
from foil.store import load_registry
from tests.e2e.support import board, close, launches, scripts, states, wait
from tests.e2e.test_scenarios import _no_windows, _prepare, _status
from tests.harness import standin
from tests.test_standins import PERMISSION

pytestmark = pytest.mark.e2e

# The launch each seat gets from `seat resume`: the lead has no worktree, the worker has one.
AFTER_RESUME = {
    "claude": ("resume", "resume"),
    "grok": ("resume", "resume"),
    "codex": ("fresh", "resume"),
    "opencode": ("fresh", "resume"),
    "gemini": ("fresh", "fresh"),
}


@pytest.mark.parametrize("harness", standin.NAMES)
def test_scenario_10_a_real_preset_launches_and_resumes_its_cli(
    harness: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clis = tmp_path / "clis"
    standin.install(clis)
    monkeypatch.setenv("PATH", os.pathsep.join((str(clis), os.environ["PATH"])))
    monkeypatch.delenv("FOIL_E2E_HARNESS", raising=False)
    repo = _prepare(tmp_path, "scenario-10", monkeypatch, fake=False)
    model = load_preset(repo, harness)["models"][0]
    try:
        for role in ("lead", "implementer"):
            assert main(["roster", "update", role, f"harness={harness}/{model}"]) == 0
        assert main(["roster", "update", "implementer", "permission=ask"]) == 0
        scripts(repo, "scenario-10", {"lead": "lead.json", "implementer-1": "implementer.json"})
        assert main(["seat", "spawn", "lead", "--task", "staff one worker"]) == 0
        wait(repo, lambda: "state: done" in _status(repo), what="the worker's report")

        seats = load_registry(repo)["seats"]
        first = {name: launches(repo, name) for name in ("lead", "implementer-1")}
        for name, permission, cwd in (
            ("lead", "auto", str(repo.resolve())),
            ("implementer-1", "ask", seats["implementer-1"]["worktree"]),
        ):
            assert len(first[name]) == 1, name
            launch = first[name][0]
            assert (launch["cli"], launch["launch"]) == (harness, "first"), name
            assert launch["model"] == model, name
            assert launch["permission"] == PERMISSION[harness][permission], name
            assert launch["cwd"] == cwd, name
            if seats[name]["session_id"]:
                assert launch["session"] == seats[name]["session_id"], name
        if harness == "claude":
            assert first["lead"][0]["disallowed"] == "AskUserQuestion"
        if harness == "opencode":
            assert first["lead"][0]["question"] == "deny"

        session = str(load_registry(repo)["tmux_session"])
        subprocess.run(["tmux", "kill-session", "-t", session], check=True, capture_output=True)
        assert main(["seat", "resume"]) == 0
        for name, kind in zip(("lead", "implementer-1"), AFTER_RESUME[harness], strict=True):
            wait(repo, lambda name=name: len(launches(repo, name)) == 2, what=f"{name} to restart")
            again = launches(repo, name)[1]
            assert again["launch"] == kind, name
            assert (again["session"] == first[name][0]["session"]) is (kind == "resume"), name
            assert again["permission"] == first[name][0]["permission"], name
            assert main(["send", name, "still there"]) == 0
            note = board(repo) / "notes" / f"{name}-after.txt"
            wait(repo, lambda note=note: note.is_file() and note.read_text(), what=note.name)
            assert note.read_text(encoding="utf-8") == f"{kind}\n"
        assert states(repo) == {"lead": "alive", "implementer-1": "alive"}
        assert main(["seat", "kill", "--all"]) == 0
        _no_windows(session)
    finally:
        close(repo)
