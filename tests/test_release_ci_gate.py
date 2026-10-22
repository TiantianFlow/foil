"""The release workflow publishes only after CI has succeeded for that commit."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WAITER = ROOT / ".github" / "workflows" / "wait_for_ci.py"
RELEASE = ROOT / ".github" / "workflows" / "release.yml"


def _verdict():
    spec = importlib.util.spec_from_file_location("wait_for_ci", WAITER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verdict


def test_release_builds_only_after_the_ci_gate() -> None:
    text = RELEASE.read_text(encoding="utf-8")
    assert "python3 .github/workflows/wait_for_ci.py" in text
    assert "needs: ci-green" in text
    assert "needs.ci-green.outputs.sha" in text
    publish = text.split("name: Publish to PyPI", 1)[1]
    assert "needs: build" in publish


def test_a_finished_success_publishes_and_anything_else_waits_or_stops() -> None:
    verdict = _verdict()
    success = {"status": "completed", "conclusion": "success", "createdAt": "t2", "databaseId": 2}
    failure = {"status": "completed", "conclusion": "failure", "createdAt": "t1", "databaseId": 1}
    newer_failure = {
        "status": "completed",
        "conclusion": "failure",
        "createdAt": "t3",
        "databaseId": 3,
    }
    running = {"status": "in_progress", "conclusion": "", "createdAt": "t4", "databaseId": 4}

    assert verdict([]) == "wait"
    assert verdict([running]) == "wait"
    assert verdict([failure, running]) == "wait"
    assert verdict([success]) == "ok"
    assert verdict([failure, success]) == "ok"
    assert verdict([success, newer_failure]) == "fail"
    assert verdict([{**success, "conclusion": "cancelled"}]) == "fail"
    same_time = [
        {**failure, "createdAt": "t", "databaseId": 9},
        {**success, "createdAt": "t", "databaseId": 8},
    ]
    assert verdict(same_time) == "fail"
