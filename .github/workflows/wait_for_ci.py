"""Wait until the CI workflow has succeeded for one commit.

The release workflow runs this before it builds. A missing run, a run still
in progress, and a newest run that did not succeed all keep the tag off PyPI.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Any


def verdict(runs: list[dict[str, Any]]) -> str:
    """Return ``wait``, ``ok``, or ``fail`` for the CI runs of one commit.

    A run that is not completed means CI is still going, so an older failure
    does not decide yet. When every run is finished, the newest one decides.
    Newest is ``createdAt``, then ``databaseId``.
    """

    if not runs or any(run.get("status") != "completed" for run in runs):
        return "wait"
    newest = _newest(runs)
    if newest.get("conclusion") == "success":
        return "ok"
    return "fail"


def _newest(runs: list[dict[str, Any]]) -> dict[str, Any]:
    return max(
        runs,
        key=lambda run: (str(run.get("createdAt") or ""), int(run.get("databaseId") or 0)),
    )


def _runs(sha: str, workflow: str) -> list[dict[str, Any]]:
    result = subprocess.run(
        [
            "gh",
            "run",
            "list",
            "--commit",
            sha,
            "--workflow",
            workflow,
            "--limit",
            "30",
            "--json",
            "databaseId,status,conclusion,createdAt,url,headSha",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "gh run list failed"
        lowered = message.lower()
        if (
            "forbidden" in lowered
            or "authentication" in lowered
            or "http 401" in lowered
            or "http 403" in lowered
        ):
            raise PermissionError(message)
        raise RuntimeError(message)
    payload = json.loads(result.stdout or "[]")
    if not isinstance(payload, list):
        raise RuntimeError("gh run list did not return a list")
    return payload


def main() -> int:
    sha = os.environ["SHA"]
    workflow = os.environ.get("CI_WORKFLOW", "ci.yml")
    deadline = time.monotonic() + float(os.environ.get("WAIT_SECONDS", "1800"))
    interval = float(os.environ.get("WAIT_INTERVAL", "15"))
    last_error = ""
    while True:
        try:
            runs = _runs(sha, workflow)
        except PermissionError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        except (RuntimeError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            runs = None
        if runs is not None:
            decision = verdict(runs)
            if decision == "ok":
                newest = _newest(runs)
                url = newest.get("url") or newest.get("databaseId")
                print(f"CI succeeded for {sha}: {url}")
                return 0
            if decision == "fail":
                newest = _newest(runs)
                print(
                    f"CI {newest.get('conclusion')} for {sha}: {newest.get('url') or ''}",
                    file=sys.stderr,
                )
                return 1
        if time.monotonic() >= deadline:
            detail = f" {last_error}" if last_error else ""
            print(f"CI did not succeed for {sha}.{detail}", file=sys.stderr)
            return 1
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
