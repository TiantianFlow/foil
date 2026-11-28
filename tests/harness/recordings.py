import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from .argv import parse

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "live"
SCENARIO = "preset-round-trip"
MAX_CAPTURE = 2 * 1024 * 1024
TURNS = ("start", "resume")
UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
ID_PATHS = {
    "agy": {("conversation_id",), ("step_update", "conversation_id")},
    "codex": {("thread_id",)},
    "opencode": {("sessionID",), ("part", "sessionID")},
}
TEXT_PATHS = {
    "agy": {("step_update", "text")},
    "codex": {("item", "text"), ("item", "command"), ("item", "aggregated_output")},
    "opencode": {("part", "text")},
}
GENERATED_IDS = {
    "codex": {("item", "id")},
    "opencode": {("part", "id"), ("part", "messageID"), ("part", "callID")},
}
TIMESTAMPS = {
    "agy": {("timestamp",)}, "codex": {("timestamp",)}, "opencode": {("timestamp",)},
}
PATHS = {("cwd",), ("init", "cwd"), ("home",)}
GENERATED_ID = re.compile(r"(item|msg|prt|call)_[A-Za-z0-9]+")


def require(condition, at, message):
    if not condition:
        raise AssertionError(f"{at}: {message}")


def bounded(path: Path) -> str:
    require(path.is_file() and not path.is_symlink(), path, "expected a regular file")
    with path.open("rb") as handle:
        data = handle.read(MAX_CAPTURE + 1)
    require(len(data) <= MAX_CAPTURE, path, "capture exceeded size bound")
    try:
        return data.decode("utf-8")
    except UnicodeError as error:
        raise AssertionError(f"{path}: invalid UTF-8") from error


def json_at(text, at):
    try:
        return json.loads(text)
    except ValueError as error:
        raise AssertionError(f"{at}: invalid JSON") from error


def stdout(text, at):
    events, plain = [], []
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if line.startswith(("{", "[")):
            obj = json_at(line, f"{at}:{number}")
            require(isinstance(obj, dict), f"{at}:{number}", "expected a JSON event object")
            events.append(obj)
        elif line:
            plain.append(line)
    return events, plain


def id_class(value):
    if UUID.fullmatch(value):
        return "uuid"
    if re.fullmatch(r"ses_[A-Za-z0-9]+", value):
        return "ses"
    return "literal"


def _typed(value, transform, path=()):
    if isinstance(value, dict):
        return "object", tuple((key, _typed(item, transform, (*path, key)))
                               for key, item in sorted(value.items()))
    if isinstance(value, list):
        return "array", tuple(_typed(item, transform, (*path, index))
                              for index, item in enumerate(value))
    return type(value).__name__, transform(path, value)


def _timestamp(value):
    if type(value) in (int, float):
        seconds = value / 1_000 if value >= 946_684_800_000 else value
        return 946_684_800 <= seconds <= 4_102_444_800
    if isinstance(value, str) and "T" in value:
        try:
            return datetime.fromisoformat(value).tzinfo is not None
        except ValueError:
            return False
    return False


class Normalizer:
    def __init__(self, harness, *, cwd="", home=""):
        self.harness = harness
        self.prefixes = ((cwd, "<cwd>"), (home, "<home>"))
        self.ids = {}

    def event(self, obj):
        def stable(path, value):
            if path in TIMESTAMPS.get(self.harness, ()) and _timestamp(value):
                return ("timestamp",)
            if not isinstance(value, str):
                return value
            if path in ID_PATHS.get(self.harness, ()):
                kind = id_class(value)
                if kind != "literal":
                    return (kind, self.ids.setdefault(value, len(self.ids)))
            if path in TEXT_PATHS.get(self.harness, ()):
                return ("text",)
            if path in GENERATED_IDS.get(self.harness, ()) and GENERATED_ID.fullmatch(value):
                return ("generated-id", value.partition("_")[0])
            if path in PATHS:
                for prefix, placeholder in self.prefixes:
                    if prefix and (value == prefix or value.startswith(prefix + "/")):
                        return placeholder + value[len(prefix):]
            return value

        return _typed(obj, stable)


def compare_events(expected, actual, expected_run, actual_run, at):
    require(len(expected) == len(actual), at, "different stdout event count")
    for number, (expected_event, actual_event) in enumerate(zip(expected, actual, strict=True), 1):
        require(expected_run.event(expected_event) == actual_run.event(actual_event),
                f"{at}:{number}", "different stdout event shape or stable value")


def argv_shape(harness, argv):
    require(isinstance(argv, list) and argv and argv[0] == harness
            and all(isinstance(arg, str) for arg in argv), harness, "invalid recorded argv")
    try:
        _, _, first = parse(harness, argv[1:])
    except SystemExit as error:
        raise AssertionError(f"{harness}: unrecognized preset argv") from error
    slots = {
        "--model": "model", "-p": "prompt", "--session-id": "id", "--resume": "id",
        "--session": "id", "--conversation": "id", "--append-system-prompt-file": "instruction",
        "-c": "instruction",
    }
    result = [harness]
    args = iter(argv[1:])
    for index, arg in enumerate(args):
        if arg in slots:
            next(args)
            value = f"<{slots[arg]}>"
            result.extend((arg, f"developer_instructions={value}" if arg == "-c" else value))
        elif index == 0 and arg in {"exec", "run"}:
            result.append(arg)
        elif arg in {"--format", "--output-format", "--permission-mode", "--disallowedTools"}:
            result.extend((arg, next(args)))
        elif arg.startswith("-"):
            result.append(arg)
        elif harness == "codex" and arg == "resume" and not first and "resume" not in result:
            next(args)
            result.extend((arg, "<id>"))
        else:
            result.append("<prompt>")
    return result


def _shape(value):
    return _typed(value, lambda _path, _value: None)


def session_rows(path):
    rows = []
    for number, line in enumerate(bounded(path).splitlines(), 1):
        assert line.strip(), f"{path}:{number}: empty session row"
        row = json_at(line, f"{path}:{number}")
        require(isinstance(row, dict), f"{path}:{number}", "expected a session object")
        rows.append(row)
    require(rows, path, "empty session")
    return rows


def grok_kind(row, at):
    try:
        kind = row["params"]["update"]["sessionUpdate"]
    except (KeyError, TypeError) as error:
        raise AssertionError(f"{at}: unrecognized session update") from error
    require(isinstance(kind, str), at, "invalid session update kind")
    return kind


def record_shapes(harness, path, *, omit_grok_usage=False):
    rows = session_rows(path)
    shapes = []
    for row in rows:
        kind = grok_kind(row, path) if harness == "grok" else row.get("type")
        require(isinstance(kind, str), path, "missing session record type")
        if kind == "usage_update" and omit_grok_usage:
            continue
        payload = row.get("payload")
        embedded = None
        if isinstance(payload, dict) and payload.get("type") == "function_call":
            arguments = payload.get("arguments")
            require(isinstance(arguments, str), path, "expected encoded tool arguments")
            embedded = _shape(json_at(arguments, path))
        shapes.append((kind, _shape(row), embedded))
    return tuple(dict.fromkeys(shapes))


def opencode_projection(path, session):
    ids = {"session": "id", "message": "session_id", "part": "session_id"}
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only = ON")
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_CAPTURE)
        result = {}
        remaining = MAX_CAPTURE
        for table, key in ids.items():
            columns = [[row[1], row[2]] for row in db.execute(f'PRAGMA table_info("{table}")')]
            order = "time_created, id" if any(col[0] == "time_created" for col in columns) else "id"
            rows = db.execute(
                f'SELECT * FROM "{table}" WHERE "{key}" = ? ORDER BY {order} LIMIT 1001',
                (session,),
            )
            entries = []
            for row in rows:
                remaining -= sum(len(str(value).encode()) for value in row)
                require(remaining >= 0 and len(entries) < 1000, path,
                        "oversized session projection")
                values = dict(zip((column[0] for column in columns), row, strict=True))
                if "data" in values:
                    values["data"] = json_at(values["data"], path)
                    require(isinstance(values["data"], dict), path, "expected an object in data")
                entries.append(values)
            require(columns and entries, path, f"missing {table} rows")
            result[table] = {"columns": columns, "rows": entries}
    return result


def sqlite_shapes(projection, at):
    require(isinstance(projection, dict) and set(projection) == {"session", "message", "part"},
            at, "invalid SQLite tables")
    result = {}
    for name, table in projection.items():
        require(isinstance(table, dict) and set(table) == {"columns", "rows"},
                at, f"invalid {name} projection")
        columns, rows = table["columns"], table["rows"]
        require(isinstance(columns, list) and columns and all(
            isinstance(pair, list) and len(pair) == 2
            and all(isinstance(field, str) for field in pair)
            for pair in columns), at, f"invalid {name} column declarations")
        names = {pair[0] for pair in columns}
        require(len(names) == len(columns) and isinstance(rows, list) and rows and all(
            isinstance(row, dict) and set(row) == names for row in rows),
            at, f"invalid {name} projected rows")
        result[name] = columns, tuple(dict.fromkeys(_shape(row) for row in rows))
    return result


def latest(root, harness):
    if not root.exists():
        return None
    choices = []
    for path in sorted(root.iterdir()):
        require(path.is_dir() and not path.is_symlink(), path, "expected a scenario directory")
        manifest_path = path / "manifest.json"
        manifest = json_at(bounded(manifest_path), manifest_path)
        require(isinstance(manifest, dict) and manifest.get("schema_version") == 1
                and manifest.get("harness") == harness and manifest.get("cli_version")
                and isinstance(manifest.get("captured_at"), str), manifest_path, "invalid manifest")
        try:
            captured = datetime.fromisoformat(manifest["captured_at"])
            require(captured.tzinfo is not None, manifest_path, "capture date needs a timezone")
        except ValueError as error:
            raise AssertionError(f"{manifest_path}: invalid date") from error
        for turn in TURNS:
            argv_shape(harness, json_at(bounded(path / f"{turn}.argv.json"), path))
            stdout(bounded(path / f"{turn}.stdout"), path)
        require((path / "session").is_dir() and not (path / "session").is_symlink(),
                path, "missing session capture")
        choices.append((captured, path))
    require(choices, root, "no complete recording in an existing capture directory")
    return max(choices)[1]
