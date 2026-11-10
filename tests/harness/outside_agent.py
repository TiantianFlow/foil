#!/usr/bin/env python3
"""Test double for the human's own agent, a caller outside the fleet.

Not part of the foil package. It is its own process, with its own
environment and working directory, and every command it runs is a real
process too. Two of them can work on one fleet at the same time, and it
can leave a command running in the background, wait for it to end, act
on what it printed, and start it again.

    outside_agent.py SCRIPT.json TRANSCRIPT.json

The script is `{"steps": [STEP, ...]}`:

    {"run": ARGV}                     run a command and wait for it
    {"until": ARGV, "contains": TEXT} run it again until it exits 0 and prints TEXT
    {"start": NAME, "run": ARGV}      leave a command running in the background
    {"await": NAME}                   wait until that command ends by itself
    {"stop": NAME}                    end it with SIGTERM
    {"when": TEXT, "do": [STEP, ...]} only if the last output contains TEXT
    {"repeat": [STEP, ...], "times": N, "until": TEXT}
                                      again and again, until the last output contains TEXT

`run`, `await` and `stop` take `"expect": CODE` (0 unless given; null
accepts any) and `until` and `await` take `"timeout": SECONDS`. `run`,
`until` and `await` take `"capture": {NAME: REGEX}`: group 1 of the first
match in the output replaces `{NAME}` in every later ARGV.

The transcript is rewritten after each command: the argv, the exit
code, and what it printed. A step that does not go as the script says
ends the run with exit code 1 and the reason in the transcript.
"""

from __future__ import annotations

import json
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path


class Stopped(Exception):
    """A step did not go as the script says."""


class Agent:
    def __init__(self, transcript: Path) -> None:
        self.transcript = transcript
        self.entries: list[dict] = []
        self.names: dict[str, str] = {}
        self.running: dict[str, tuple[subprocess.Popen[str], list[str], Path, Path]] = {}
        self.last = ""
        self.scratch = Path(tempfile.mkdtemp(prefix="outside-agent-"))

    def save(self, *, finished: bool, error: str = "") -> None:
        payload = {"entries": self.entries, "finished": finished, "error": error}
        partial = self.transcript.with_name(self.transcript.name + ".part")
        partial.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        partial.replace(self.transcript)

    def _argv(self, raw: list) -> list[str]:
        argv = [str(item) for item in raw]
        for name, value in self.names.items():
            argv = [item.replace("{" + name + "}", value) for item in argv]
        return argv

    def _record(
        self, step: dict, kind: str, argv: list[str], code: int, out: str, err: str
    ) -> None:
        self.entries.append({"kind": kind, "argv": argv, "code": code, "out": out, "err": err})
        self.last = out
        for name, pattern in (step.get("capture") or {}).items():
            match = re.search(pattern, out)
            if match is None:
                self.save(finished=False)
                raise Stopped(f"{argv} printed nothing that matches {pattern!r}")
            self.names[name] = match.group(1)
        self.save(finished=False)
        expected = step.get("expect", 0)
        if expected is not None and code != expected:
            raise Stopped(f"{argv} exited {code}, not {expected}: {err.strip()}")

    def _once(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(argv, check=False, capture_output=True, text=True)

    def _end(self, step: dict, kind: str, name: str, timeout: float | None) -> None:
        if name not in self.running:
            raise Stopped(f"nothing named {name!r} is running")
        process, argv, out, err = self.running.pop(name)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise Stopped(f"{argv} was still running after {timeout:g}s") from None
        self._record(step, kind, argv, code, out.read_text("utf-8"), err.read_text("utf-8"))

    def step(self, step: dict) -> None:
        if "start" in step:
            name = str(step["start"])
            argv = self._argv(step["run"])
            out = self.scratch / f"{len(self.entries)}-{name}.out"
            err = self.scratch / f"{len(self.entries)}-{name}.err"
            with out.open("w") as stdout, err.open("w") as stderr:
                process = subprocess.Popen(argv, stdout=stdout, stderr=stderr, text=True)
            self.running[name] = (process, argv, out, err)
        elif "run" in step:
            argv = self._argv(step["run"])
            done = self._once(argv)
            self._record(step, "run", argv, done.returncode, done.stdout, done.stderr)
        elif "until" in step:
            argv = self._argv(step["until"])
            wanted = str(step.get("contains", ""))
            deadline = time.monotonic() + float(step.get("timeout", 60))
            while True:
                done = self._once(argv)
                if done.returncode == 0 and wanted in done.stdout:
                    break
                if time.monotonic() >= deadline:
                    self._record({}, "until", argv, done.returncode, done.stdout, done.stderr)
                    raise Stopped(f"{argv} never printed {wanted!r}")
                time.sleep(0.2)
            self._record({**step, "expect": 0}, "until", argv, 0, done.stdout, done.stderr)
        elif "await" in step:
            self._end(step, "await", str(step["await"]), float(step.get("timeout", 60)))
        elif "stop" in step:
            name = str(step["stop"])
            if name in self.running:
                self.running[name][0].send_signal(signal.SIGTERM)
            self._end(step, "stop", name, 10)
        elif "when" in step:
            if str(step["when"]) in self.last:
                self.steps(step.get("do") or [])
        elif "repeat" in step:
            wanted = str(step["until"])
            for _round in range(int(step.get("times", 10))):
                self.steps(step["repeat"])
                if wanted in self.last:
                    return
            raise Stopped(f"the output never contained {wanted!r}")
        else:
            raise Stopped(f"unknown step {step}")

    def steps(self, steps: list) -> None:
        for step in steps:
            self.step(step)

    def close(self) -> None:
        for process, _argv, _out, _err in self.running.values():
            process.kill()
            process.wait()


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: outside_agent.py SCRIPT.json TRANSCRIPT.json", file=sys.stderr)
        return 2
    script = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    agent = Agent(Path(sys.argv[2]))
    try:
        agent.steps(script["steps"])
    except Stopped as exc:
        agent.save(finished=False, error=str(exc))
        print(f"outside-agent: {exc}", file=sys.stderr)
        return 1
    finally:
        agent.close()
    agent.save(finished=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
