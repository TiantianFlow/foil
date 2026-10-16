"""Issue 34: losing FOIL_HOME and FOIL_SEAT_ID leaves the live lead unaware.

A killed fleet for the same repository already sits in the default store
($XDG_DATA_HOME/foil). Foil's launch plan keeps FOIL_HOME, so a report from
that environment reaches the live lead. The same script then drops FOIL_HOME
and FOIL_SEAT_ID. Foil still has XDG_DATA_HOME, attaches to the killed fleet,
and the live lead does not handle that report.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from pathlib import Path

import pytest

from foil.cli import main
from foil.project import foil_root

pytestmark = pytest.mark.e2e


def _git(repo: Path, *args: str) -> None:
    environment = os.environ.copy()
    environment.pop("FOIL_SEAT_ID", None)
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _fake(root: Path) -> None:
    templates = root / "templates"
    for role in ("lead", "implementer", "reviewer"):
        path = templates / f"{role}.toml"
        text = re.sub(
            r'(?m)^harness = ".*"$',
            'harness = "fake"',
            path.read_text(encoding="utf-8"),
            count=1,
        )
        path.write_text(text, encoding="utf-8")


def _session(root: Path) -> str:
    registry = root / "run" / "registry.json"
    if not registry.is_file():
        return ""
    return str(json.loads(registry.read_text(encoding="utf-8")).get("tmux_session") or "")


def _close(root: Path) -> None:
    session = _session(root)
    if session:
        subprocess.run(
            ["tmux", "kill-session", "-t", session],
            check=False,
            capture_output=True,
        )


def _logs(root: Path) -> str:
    directory = root / "run" / "fake"
    if not directory.is_dir():
        return ""
    chunks = []
    for path in sorted(directory.glob("*.log")):
        chunks.append(f"----- {path.name}\n{path.read_text(encoding='utf-8')}")
    return "\n".join(chunks)


def _wait(root: Path, predicate, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.2)
    raise AssertionError(f"timed out\n{_logs(root)}")


def _foil(*args: str) -> None:
    assert main(list(args)) == 0


def _blocks(text: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    name = ""
    chunk: list[str] = []

    def store() -> None:
        if name:
            blocks[name] = "\n".join(chunk).strip()

    for line in text.splitlines():
        stripped = line.strip()
        equals = stripped.startswith("===== ") and stripped.endswith(" =====")
        dashes = stripped.startswith("----- ") and stripped.endswith(" -----")
        if equals or dashes:
            store()
            name = stripped[6:-6]
            chunk = []
            continue
        chunk.append(line)
    store()
    return blocks


def _line(text: str, key: str) -> str:
    prefix = f"{key}="
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :]
    raise AssertionError(f"missing {key}")


def _mails(directory: Path) -> list[str]:
    return [path.read_text(encoding="utf-8") for path in sorted(directory.glob("*.md"))]


def _probe_script(mailbox: Path, report: Path) -> str:
    script = r"""#!/bin/sh
set +e
sleep 1
mailbox=__MAILBOX__
report=__REPORT__
real=$(ls -1 "$mailbox"/*.md 2>/dev/null | head -1)
{
echo "REAL_MAIL=$real"
echo "----- seat env -----"
env | sort | grep -E '^(FOIL_|XDG_|HOME=)'
echo "===== own: mail read absolute ====="
foil mail read "$real"
echo "exit:$?"
echo "===== own: mail read basename ====="
foil mail read "$(basename "$real")"
echo "exit:$?"
echo "===== own: mail list --json ====="
foil mail list --json
echo "exit:$?"
echo "===== own: seat list ====="
foil seat list
echo "exit:$?"
echo "===== own: seat peek reviewer-1 ====="
foil seat peek reviewer-1 --lines 20
echo "exit:$?"
echo "===== own: send ====="
foil send lead "own-env-report"
echo "exit:$?"
echo "----- stripped env -----"
env -u FOIL_HOME -u FOIL_SEAT_ID env | sort | grep -E '^(FOIL_|XDG_|HOME=)'
echo "===== strip: mail read absolute ====="
env -u FOIL_HOME -u FOIL_SEAT_ID foil mail read "$real"
echo "exit:$?"
echo "===== strip: mail read basename ====="
env -u FOIL_HOME -u FOIL_SEAT_ID foil mail read "$(basename "$real")"
echo "exit:$?"
echo "===== strip: mail list --json ====="
env -u FOIL_HOME -u FOIL_SEAT_ID foil mail list --json
echo "exit:$?"
echo "===== strip: seat list ====="
env -u FOIL_HOME -u FOIL_SEAT_ID foil seat list
echo "exit:$?"
echo "===== strip: seat peek reviewer-1 ====="
env -u FOIL_HOME -u FOIL_SEAT_ID foil seat peek reviewer-1 --lines 20
echo "exit:$?"
echo "----- strip: other path -----"
worktree=$(env -u FOIL_HOME -u FOIL_SEAT_ID foil seat list 2>/dev/null \
  | awk -F '\t' '$1 == "tracer" { print $4 }')
echo "TRACER_WORKTREE=$worktree"
old=$(dirname "$(dirname "$worktree")")
other="$old/board/mail/reviewer-1/$(basename "$real")"
echo "===== strip: mail read other ====="
env -u FOIL_HOME -u FOIL_SEAT_ID foil mail read "$other"
echo "exit:$?"
echo "===== strip: send ====="
env -u FOIL_HOME -u FOIL_SEAT_ID foil send lead "Blocked before review"
echo "exit:$?"
echo "----- done -----"
echo PROBE_DONE
} > "$report" 2>&1
exit 0
"""
    return script.replace("__MAILBOX__", shlex.quote(str(mailbox))).replace(
        "__REPORT__", shlex.quote(str(report))
    )


def test_stripped_env_leaves_the_live_lead_unaware(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    xdg = tmp_path / "xdg"
    fleet = tmp_path / "fleet"
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / "README.md").write_text("issue 34\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    monkeypatch.delenv("FOIL_HOME", raising=False)
    monkeypatch.delenv("FOIL_SEAT_ID", raising=False)
    monkeypatch.delenv("FOIL_PROJECT", raising=False)
    _git(repo, "init", "--quiet")
    _git(repo, "config", "user.name", "Foil Test")
    _git(repo, "config", "user.email", "foil-test@localhost")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--allow-empty", "-m", "base")
    monkeypatch.chdir(repo)
    old: Path | None = None
    live: Path | None = None
    try:
        _foil("init")
        old = foil_root(repo)
        _fake(old)
        _foil("seat", "spawn", "lead")
        _foil("seat", "spawn", "reviewer")
        _foil("seat", "spawn", "implementer", "--name", "tracer")
        _foil("seat", "kill", "--all")
        _close(old)

        monkeypatch.setenv("FOIL_HOME", str(fleet))
        _foil("init")
        live = foil_root(repo)
        _fake(live)
        assert old.name == live.name
        assert old != live
        mailbox = live / "board" / "mail" / "reviewer-1"
        report = live / "board" / "notes" / "command-report.txt"
        fake = live / "run" / "fake"
        fake.mkdir(parents=True, exist_ok=True)
        (fake / "probe.sh").write_text(_probe_script(mailbox, report), encoding="utf-8")
        (fake / "lead.json").write_text(
            json.dumps(
                {
                    "steps": [
                        {
                            "when": {"mail_contains": "make the report"},
                            "do": [
                                {
                                    "sh": "foil seat spawn reviewer --task 'review this change'",
                                }
                            ],
                        },
                        {
                            "when": {"mail_contains": "own-env-report"},
                            "do": [
                                {
                                    "write": "board/status.md",
                                    "text": "state: saw-own-env\n",
                                }
                            ],
                        },
                        {
                            "when": {"mail_contains": "Blocked before review"},
                            "do": [
                                {
                                    "write": "board/notes/saw-blocked.md",
                                    "text": "state: saw-blocked\n",
                                }
                            ],
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        (fake / "reviewer-1.json").write_text(
            json.dumps(
                {
                    "steps": [
                        {
                            "when": {"mail_contains": "review this change"},
                            "do": [{"sh": f"sh {fake / 'probe.sh'}"}],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        _foil("seat", "spawn", "lead", "--task", "make the report")
        status = live / "board" / "status.md"
        blocked = live / "board" / "notes" / "saw-blocked.md"
        _wait(
            live,
            lambda: (
                report.is_file()
                and "PROBE_DONE" in report.read_text(encoding="utf-8")
                and status.is_file()
                and "state: saw-own-env" in status.read_text(encoding="utf-8")
            ),
        )
        time.sleep(3)
        text = report.read_text(encoding="utf-8")
        blocks = _blocks(text)
        real = _line(text, "REAL_MAIL")
        assert real.startswith(str(mailbox) + "/")

        seat_env = blocks["seat env"]
        assert f"FOIL_HOME={fleet}" in seat_env
        assert f"XDG_DATA_HOME={xdg}" in seat_env
        assert "FOIL_SEAT_ID=reviewer-1" in seat_env
        assert f"FOIL_PROJECT={live.name}" in seat_env
        assert blocks["own: mail read absolute"] == "review this change\nexit:0"
        assert blocks["own: mail read basename"] == "foil: path must be absolute\nexit:1"
        assert real in blocks["own: mail list --json"]
        assert blocks["own: mail list --json"].endswith("exit:0")
        assert "foil: not allowed" not in blocks["own: mail list --json"]
        assert "lead\tlead\talive\t" in blocks["own: seat list"]
        assert "reviewer-1\treviewer\talive\t" in blocks["own: seat list"]
        assert "tracer" not in blocks["own: seat list"]
        assert str(old) not in blocks["own: seat list"]
        assert blocks["own: seat list"].endswith("exit:0")
        own_peek = blocks["own: seat peek reviewer-1"]
        assert "foil-fake reviewer-1 ready" in own_peek
        assert "killed" not in own_peek
        assert own_peek.endswith("exit:0")
        assert blocks["own: send"] == "exit:0"

        stripped = blocks["stripped env"]
        assert "FOIL_HOME=" not in stripped
        assert "FOIL_SEAT_ID=" not in stripped
        assert f"XDG_DATA_HOME={xdg}" in stripped
        assert f"FOIL_PROJECT={live.name}" in stripped
        assert blocks["strip: mail read absolute"] == ("foil: path not inside board/mail\nexit:1")
        assert blocks["strip: mail read basename"] == "foil: path must be absolute\nexit:1"
        assert blocks["strip: mail list --json"] == "foil: not allowed\nexit:1"
        listed = blocks["strip: seat list"]
        worktree = old / "worktrees" / "tracer"
        assert "lead\tlead\tkilled\t" in listed
        assert "reviewer-1\treviewer\tkilled\t" in listed
        assert f"tracer\timplementer\tkilled\t{worktree}" in listed
        assert "\talive\t" not in listed
        assert listed.endswith("exit:0")
        assert _line(text, "TRACER_WORKTREE") == str(worktree)
        killed = "foil: seat 'reviewer-1' is killed\nexit:1"
        assert blocks["strip: seat peek reviewer-1"] == killed
        missing = blocks["strip: mail read other"]
        other = old / "board" / "mail" / "reviewer-1" / Path(real).name
        assert missing == f"foil: file not found: {other}\nexit:1"
        assert blocks["strip: send"] == "exit:0"

        assert status.read_text(encoding="utf-8") == "state: saw-own-env\n"
        assert not blocked.exists()
        live_mail = _mails(live / "board" / "mail" / "lead")
        assert len(live_mail) == 2
        assert any("own-env-report" in body and "from: reviewer-1\n" in body for body in live_mail)
        assert all("Blocked before review" not in body for body in live_mail)
        old_mail = _mails(old / "board" / "mail" / "lead")
        assert len(old_mail) == 1
        assert "from: user\n" in old_mail[0]
        assert "Blocked before review\n" in old_mail[0]
    finally:
        if live is not None:
            _close(live)
        if old is not None:
            _close(old)
