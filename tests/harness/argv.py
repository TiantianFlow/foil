from __future__ import annotations

_SHAPES = {
    "claude": ({"-p", "--disallowedTools", "--session-id", "--resume", "--model",
                "--permission-mode", "--append-system-prompt-file"}, set(), []),
    "agy": ({"-p", "--output-format", "--conversation", "--model"},
            {"--dangerously-skip-permissions"}, []),
    "grok": ({"-p", "--session-id", "--resume", "--model"}, {"--always-approve"}, []),
    "codex": (
        {"--model", "-c"}, {"--json", "--dangerously-bypass-approvals-and-sandbox"}, ["exec"]
    ),
    "opencode": ({"--format", "--session", "--model"}, {"--auto"}, ["run"]),
}


def _flags(name: str, argv: list[str]) -> tuple[dict[str, str], set[str], list[str]]:
    valued, bare, words = _SHAPES[name]
    if argv[: len(words)] != words:
        raise SystemExit(f"{name} stand-in: expected {' '.join(words)}")
    values: dict[str, str] = {}
    flags: set[str] = set()
    positional: list[str] = []
    rest = argv[len(words):]
    index = 0
    while index < len(rest):
        token = rest[index]
        if (token in valued and index + 1 < len(rest) and token not in values
                and rest[index + 1] and not rest[index + 1].startswith("-")):
            values[token] = rest[index + 1]
            index += 2
            continue
        if token in bare and token not in flags:
            flags.add(token)
        elif token.startswith("-"):
            raise SystemExit(f"{name} stand-in: unknown flag {token}")
        else:
            positional.append(token)
        index += 1
    return values, flags, positional


def parse(name: str, argv: list[str]) -> tuple[str, str, bool]:
    """Return the prompt, session id (empty for an output-id start), and start flag."""

    if name == "foil-fake":
        if (len(argv) != 3 or argv[0] not in ("--session", "--resume")
                or not argv[1] or argv[1].startswith("-")):
            raise SystemExit("foil-fake: usage: --session|--resume ID PROMPT")
        return argv[2], argv[1], argv[0] == "--session"
    values, flags, positional = _flags(name, argv)
    if name == "agy":
        if positional or "-p" not in values:
            raise SystemExit("agy stand-in: the prompt goes in -p")
        if values.get("--output-format") != "stream-json":
            raise SystemExit("agy stand-in: --output-format stream-json")
        session = values.get("--conversation", "")
        return values["-p"], session, not session
    if name in ("claude", "grok"):
        if positional or "-p" not in values:
            raise SystemExit(f"{name} stand-in: the prompt goes in -p")
        if ("--session-id" in values) == ("--resume" in values):
            raise SystemExit(f"{name} stand-in: one of --session-id and --resume")
        if name == "claude" and values.get("--disallowedTools") != "AskUserQuestion":
            raise SystemExit("claude stand-in: --disallowedTools AskUserQuestion")
        if "--permission-mode" in values and values["--permission-mode"] != "auto":
            raise SystemExit("claude stand-in: --permission-mode auto")
        session = values.get("--session-id") or values["--resume"]
        return values["-p"], session, "--session-id" in values
    if name == "codex":
        if "--json" not in flags:
            raise SystemExit("codex stand-in: --json")
        if "-c" in values and not values["-c"].startswith("developer_instructions="):
            raise SystemExit("codex stand-in: expected developer_instructions")
        if len(positional) == 1:
            return positional[0], "", True
        if len(positional) == 3 and positional[0] == "resume" and positional[1]:
            return positional[2], positional[1], False
        raise SystemExit("codex stand-in: PROMPT or resume ID PROMPT")
    if values.get("--format") != "json" or len(positional) != 1:
        raise SystemExit("opencode stand-in: --format json and one PROMPT")
    session = values.get("--session", "")
    return positional[0], session, not session


def queue(argv: list[str]) -> dict[str, str]:
    if (len(argv) != 6 or argv[1::2] != ["--thread", "--message", "--resume"]
            or any(not value or value.startswith("-") for value in argv[2:5:2])):
        raise SystemExit("codex stand-in: queue --thread ID --message TEXT --resume")
    return {"thread": argv[2], "message": argv[4]}
