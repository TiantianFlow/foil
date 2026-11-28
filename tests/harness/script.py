import json
import os
import re
import subprocess
import time
from pathlib import Path

_PATH = re.compile(r"(/[^\s`'\"]+\.md)")


def fleet_root(named: str) -> Path:
    marker = "/run/instructions/"
    index = named.find(marker)
    if index >= 0:
        left = index
        while left > 0 and named[left - 1] not in " \n\t`\"'":
            left -= 1
        return Path(named[left:index])
    raise SystemExit("foil-fake: the prompt names no instruction file")


def log(path: Path, message: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(message.rstrip() + "\n")


def load(path: Path) -> dict:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if text.startswith("---\n"):
        _, delimiter, body = text[4:].partition("\n---\n")
        if delimiter:
            return body
    return text


def master_wake(prompt: str) -> tuple[list[str], list[str]]:
    wake = list(prompt.splitlines())
    headings = [index for index, line in enumerate(wake) if line == "# Wake"]
    lines = iter(wake[headings[-1] + 1:] if headings else [])
    subjects, names = [], []
    for line in lines:
        if line == "finish:":
            for _ in range(8):
                next(lines, None)
        elif line.startswith("inbox: "):
            fields = line.split(" ", 3)
            if len(fields) == 4 and all(fields[1:]):
                subjects.append(fields[3])
        elif line.startswith("mail: "):
            name = line.removeprefix("mail: ")
            if Path(name).name == name and name.endswith(".md"):
                names.append(name)
    return subjects, names


def matches(when: dict, *, text: str = "", subjects: list[str] | None = None) -> bool:
    def match(key: str, value: str) -> bool:
        if not isinstance(value, str) or not value:
            return False
        if key == "mail_contains":
            return value in text
        if subjects is not None:
            if key == "inbox":
                return value in subjects
            if key == "question":
                return "question:" + value in subjects
        return False

    return bool(when) and all(match(key, value) for key, value in when.items())


def _actions(steps, root, log_path, tools, *, matches_step, master, wrong_seat):
    for step in steps:
        if not matches_step(step.get("when") or {}):
            continue
        for action in step.get("do") or []:
            if "sh" in action:
                command = action["sh"]
                tools.append(("sh", {"command": command}))
                result = subprocess.run(command, shell=True, check=False, capture_output=True,
                                        text=True)
                log(log_path, f"sh {result.returncode} {command}\n{result.stdout}{result.stderr}")
                if result.returncode:
                    return False
            elif "write" in action:
                relative = str(action["write"])
                tools.append(("write", {"path": relative}))
                base = root if master or relative.startswith("board/") else Path.cwd()
                target = base / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(str(action.get("text", "")), encoding="utf-8")
                log(log_path, f"write {target}")
            elif "send" in action:
                recipient = str(wrong_seat or action["send"])
                tools.append(("send", {"to": recipient}))
                result = subprocess.run(
                    ["foil", "send", recipient, str(action.get("text", ""))], check=False,
                    stdout=subprocess.DEVNULL,
                )
                log(log_path, f"send {result.returncode} {recipient}")
                if result.returncode:
                    return False
            elif "wait" in action:
                tools.append(("wait", {"seconds": action["wait"]}))
                time.sleep(float(action["wait"]))
            else:
                log(log_path, f"unknown action {action}")
                return False
    return True


def run(root, actor, fake, steps, prompt, fault, *, master, diagnostic) -> list[tuple]:
    log_path = fake / f"{actor}.log"
    tools: list[tuple] = []
    if master:
        subjects, names = master_wake(prompt)
        paths = [root / "mail" / name for name in names]
        texts = [_body(path) for path in paths if path.is_file()]
        _actions(steps, root, log_path, tools, master=True, wrong_seat=fault.get("wrong_seat"),
                 matches_step=lambda when: any(matches(when, text=text, subjects=subjects)
                                               for text in texts or [""]))
        return tools

    handled_file = fake / f"{actor}.handled"
    handled = set(handled_file.read_text().splitlines()) if handled_file.is_file() else set()
    board = root / "board"
    for raw in dict.fromkeys(_PATH.findall(prompt)):
        path = Path(raw)
        try:
            relative = path.relative_to(board).as_posix()
        except ValueError:
            continue
        if not relative.startswith((f"mail/{actor}/", "room/")) or not path.is_file():
            continue
        key = str(path.resolve())
        if key in handled or fault.get("skip") in (raw, relative, path.name):
            continue
        text = _body(path)
        if diagnostic:
            print(f"foil-fake {actor} mail {key}", flush=True)
        os.environ["FOIL_FAKE_ITEM"] = key
        if _actions(steps, root, log_path, tools, master=False, wrong_seat=fault.get("wrong_seat"),
                    matches_step=lambda when, text=text: matches(when, text=text)):
            with handled_file.open("a", encoding="utf-8") as handle:
                handle.write(key + "\n")
            handled.add(key)
    return tools
