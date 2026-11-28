"""Scenario 19: the phone path on real Foil, tmux, git, and the Bridge.

The master, lead, and worker are the fake double. Steps 1, 5, and 7 go
over HTTP here. The same three taps are in the Bridge browser test, which
keeps the page on the API.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest
from foil_bridge.push import short_name, update_settings

from foil.bridge_cmd import read_desk_token
from foil.cli import main
from foil.master import load_record as load_master
from foil.project import foil_root, store_root
from tests.e2e.test_scenarios import _close, _git, _logs, _prepare
from tests.test_bridge_http import _origin, _request, _serving
from tests.test_bridge_push import _decrypt, _endpoint, _subscribe

pytestmark = pytest.mark.e2e

MESSAGE = "fix the calc"
ASK = "Ship the fix?"
GOAL = "the calc adds"
RESULT = (
    "---\ncontract: result/v1\ntask: calc\nauthor: reviewer-1\noutcome: pass\n---\n\n"
    "## Changed\nThe calc adds.\n\n## Tests\ncheck_calc.py passed.\n\n## Risks\nNone.\n"
)
QUESTION = (
    "---\nstate: working\nupdated: 2026-01-02T03:04:05Z\nquestions:\n"
    "  - id: ship\n    ask: Ship the fix?\n    options: [\"yes\", \"no\"]\n"
    "    recommended: \"yes\"\n    why: The calc adds.\n"
    "    evidence: The worker commit.\n    owner: human\ndecisions: []\n---\n"
    "## Mandate\nDone when: the calc adds.\n"
)
WORKING = (
    "---\nstate: working\nupdated: 2026-01-02T03:04:06Z\nquestions: []\n"
    "decisions: []\n---\n## Mandate\nDone when: the calc adds.\n"
)
DONE = (
    "---\nstate: done\nupdated: 2026-01-02T03:04:07Z\nquestions: []\n"
    "decisions: []\n---\n## Mandate\nDone when: the calc adds.\n"
)
FINISH = """\
import json
import subprocess
from pathlib import Path

files = sorted(Path("run/fake/master.turns").glob("*.json"))
prompt = json.loads(files[-1].read_text(encoding="utf-8"))["prompt"]
lines = prompt.splitlines()
start = max(index for index, line in enumerate(lines) if line == "finish:")
block = lines[start + 1:start + 9]
if len(block) != 8 or block[0] != "card: merge":
    raise SystemExit("finish block missing")
text = "\\n".join(block) + "\\n"
completed = subprocess.run(["foil", "send", "master", text], check=False)
raise SystemExit(completed.returncode)
"""


def _script(repo: Path, seat: str, script: dict) -> None:
    fake = foil_root(repo) / "fleets" / "main" / "run" / "fake"
    fake.mkdir(parents=True, exist_ok=True)
    (fake / f"{seat}.json").write_text(json.dumps(script), encoding="utf-8")


def _status(repo: Path) -> str:
    path = foil_root(repo) / "fleets" / "main" / "board" / "status.md"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _wait(repo: Path, predicate, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.2)
    master_log = store_root() / "master" / "run" / "fake" / "master.log"
    extra = master_log.read_text(encoding="utf-8") if master_log.is_file() else ""
    raise AssertionError(f"timed out\n{_logs(repo)}\n{extra}")


def _ancestor(repo: Path, commit: str, ref: str) -> bool:
    return _git(repo, "merge-base", "--is-ancestor", commit, ref).returncode == 0


def _answer_mail(repo: Path) -> bool:
    folder = foil_root(repo) / "fleets" / "main" / "board" / "mail" / "lead"
    if not folder.is_dir():
        return False
    for path in folder.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        body = text.split("---", 2)[-1].lstrip("\n")
        if "from: user\n" in text and body.startswith("question: ship\n"):
            return True
    return False


def _addressable(tmp_path: Path) -> Path:
    """A checkout whose store key starts with a letter.

    The key is the last 48 slug characters of the ``.git`` path. A cut that
    lands on a hyphen is a key ``--project`` refuses and the home does not link.
    """

    for pad in range(48):
        candidate = tmp_path / f"p{'x' * pad}"
        slug = re.sub(
            r"[^A-Za-z0-9]+", "-", str((candidate / ".git").resolve())
        ).strip("-")
        window = slug[-48:] if len(slug) > 48 else slug
        if window[:1].isalnum():
            return candidate
    raise AssertionError("no addressable project path")


def test_scenario_19_the_phone_merges_a_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _prepare(tmp_path, "scenario-1", monkeypatch, root=_addressable(tmp_path))
    key = foil_root(repo).name
    goal = f"projects/{key}/fleets/main"
    _script(repo, "lead", {"steps": [
        {"when": {"mail_contains": "fix the failing test"}, "do": [
            {"sh": "foil seat list | grep -q implementer-1 || "
             "foil seat spawn implementer --task 'fix the failing test'"},
        ]},
        {"when": {"mail_contains": "fixed on foil/implementer-1"}, "do": [
            {"write": "board/status.md", "text": QUESTION},
        ]},
        {"when": {"mail_contains": "question: ship"}, "do": [
            {"write": "board/status.md", "text": WORKING},
            {"sh": "foil seat list | grep -q reviewer-1 || "
             "foil seat spawn reviewer --task 'review the fix'"},
        ]},
        {"when": {"mail_contains": "approved"}, "do": [
            {"sh": "git merge --no-edit -m 'merge the fix' foil/implementer-1"},
            {"write": "board/status.md", "text": DONE},
        ]},
    ]})
    _script(repo, "implementer-1", {"steps": [
        {"when": {"mail_contains": "fix the failing test"}, "do": [{"sh": "sh fix.sh"}]},
    ]})
    _script(repo, "reviewer-1", {"steps": [
        {"when": {"mail_contains": "review the fix"}, "do": [
            {"write": "board/results/review.md", "text": RESULT},
            {"sh": "sh review.sh"},
        ]},
    ]})
    master = store_root() / "master"
    (master / "run" / "fake").mkdir(parents=True)
    (master / "run" / "fake" / "send-finish.py").write_text(FINISH, encoding="utf-8")
    (master / "run" / "fake.json").write_text(json.dumps({"steps": [
        {"when": {"mail_contains": MESSAGE}, "do": [
            {"sh": f"foil seat spawn lead --project={key} --task 'fix the failing test'"},
            {"send": "master", "text": f"Started the lead in {goal}."},
        ]},
        {"when": {"question": "ship"}, "do": [
            {"write": "run/fake/question-ship", "text": "noted"},
        ]},
        {"when": {"inbox": "goal"}, "do": [{"sh": "python3 run/fake/send-finish.py"}]},
    ]}), encoding="utf-8")
    assert main(["master", "start", "--harness", "fake"]) == 0
    try:
        _wait(repo, lambda: load_master(master)["baselined"], 20)
        with _endpoint() as capture, _serving(monkeypatch) as server:
            port = server.server_address[1]
            push_port = capture.server_address[1]
            private, public, auth = _subscribe(f"http://127.0.0.1:{push_port}/push")
            kinds = update_settings({"question": True})
            assert kinds == {
                "question": True, "seat": False, "delivery": False, "done": False, "down": False,
            }
            desk = read_desk_token()
            assert desk is not None
            status, body = _request(
                port, "POST", "/v1/pairing", token=desk, headers=_origin(port)
            )
            assert status == 200, body
            code = json.loads(body)["code"]
            status, body = _request(
                port, "POST", "/v1/pair", body={"code": code}, headers=_origin(port)
            )
            assert status == 200, body
            token = json.loads(body)["token"]
            status, _body = _request(
                port, "POST", "/v1/pair", body={"code": code}, headers=_origin(port)
            )
            assert status == 401
            status, body = _request(
                port, "POST", "/v1/send", token=token, headers=_origin(port),
                body={"text": MESSAGE},
            )
            assert status == 200, body

            def home() -> dict:
                status_code, payload = _request(port, "GET", "/v1/home", token=token)
                assert status_code == 200, payload
                return json.loads(payload)

            def replied() -> bool:
                return any(
                    item["from"] == "master" and goal in item["body"]
                    for item in home()["messages"]
                )

            _wait(repo, replied, 45)
            messages = home()["messages"]
            assert messages[0]["from"] == "user"
            assert messages[0]["body"].strip() == MESSAGE
            assert any(goal in item["target"] for item in messages[1]["links"])

            def asked() -> bool:
                marker = master / "run" / "fake" / "question-ship"
                cards = [item for item in home()["questions"] if item["id"] == "ship"]
                return marker.is_file() and bool(cards) and cards[0]["state"] == "open"

            _wait(repo, asked, 45)
            assert (master / "run" / "fake" / "question-ship").read_text() == "noted"
            wake = f"inbox: {key} main question:ship"
            turns = (master / "run" / "fake" / "master.turns").glob("*.json")
            assert any(wake in json.loads(path.read_text())["prompt"] for path in turns)
            commit = _git(repo, "rev-parse", "foil/implementer-1").stdout.strip()
            assert commit
            assert not _ancestor(repo, commit, "HEAD")
            _wait(repo, lambda: len(capture.rows) >= 1, 20)
            assert len(capture.rows) == 1
            sealed = capture.rows[0][1]
            plain = _decrypt(sealed, private, public, auth)
            parsed = json.loads(plain)
            assert set(parsed) == {"label", "card"}
            assert parsed["card"] == f"q/{key}/main/ship"
            assert parsed["label"] == f"{short_name(key)}: 1 decision"
            for phrase in (ASK, MESSAGE, "projects/"):
                assert phrase.encode() not in sealed
                assert phrase not in plain.decode()

            status, body = _request(
                port, "POST", "/v1/answer", token=token, headers=_origin(port),
                body={"project": key, "fleet": "main", "id": "ship", "text": "yes"},
            )
            assert status == 200, body
            _wait(repo, lambda: _answer_mail(repo) and "id: ship" not in _status(repo), 30)

            def finished() -> bool:
                cards = [
                    item for item in home()["messages"]
                    if item["card"] and item["card"]["action"] == "merge"
                ]
                return "state: done" in _status(repo) and bool(cards)

            _wait(repo, finished, 45)
            merge_message = next(
                item for item in home()["messages"]
                if item["card"] and item["card"]["action"] == "merge"
            )
            result = f"{goal}/board/results/review.md"
            assert result in merge_message["body"]
            assert any(item["target"] == result for item in merge_message["links"])
            assert _ancestor(repo, commit, "foil/goal-main")
            assert not _ancestor(repo, commit, "HEAD")
            assert "left - right" in (repo / "calc.py").read_text(encoding="utf-8")
            status, body = _request(
                port, "POST", "/v1/act", token=token, headers=_origin(port),
                body={"id": merge_message["id"]},
            )
            assert status == 200, body
            assert _ancestor(repo, commit, "HEAD")
            assert "left + right" in (repo / "calc.py").read_text(encoding="utf-8")
            checked = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "--noconftest", "check_calc.py"],
                cwd=repo, check=False, capture_output=True, text=True,
            )
            assert checked.returncode == 0, checked.stdout + checked.stderr
            for target, needle in ((goal, GOAL.encode()), (result, b"The calc adds.")):
                status_code, payload = _request(
                    port, "GET", "/evidence?target=" + target, token=token
                )
                assert status_code == 200, payload
                assert needle in payload
            assert len(capture.rows) == 1
    finally:
        main(["master", "stop"])
        _close(repo)
