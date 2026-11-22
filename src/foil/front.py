"""One-level front matter.

A document is a mapping. A value is a scalar, a flow list of scalars, or a
block list whose items are scalars or flat mappings. A flat mapping's values
are scalars or flow lists. Anything else is refused whole: the caller shows
the front matter raw and uses none of its fields.
"""

from __future__ import annotations

import re
from typing import Any

_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")


class _Refuse(Exception):
    """The document is outside the subset. No partial fields are kept."""


class Front:
    """``fields`` when the subset matches. ``raw`` is the fenced text when it does not."""

    def __init__(self, fields: dict[str, Any] | None, body: str, raw: str | None) -> None:
        self.fields = fields
        self.body = body
        self.raw = raw


def parse_front(text: str) -> Front:
    """Parse one front matter block. A document with no fence has no fields and no raw text."""

    lines = text.split("\n")
    if lines and lines[0].endswith("\r"):
        lines = [line[:-1] if line.endswith("\r") else line for line in lines]
    if not lines or lines[0] != "---":
        return Front(None, text, None)
    try:
        end = lines.index("---", 1)
    except ValueError:
        return Front(None, text, None)
    raw = "\n".join(lines[: end + 1])
    body = "\n".join(lines[end + 1 :])
    if body.startswith("\n"):
        body = body[1:]
    try:
        fields, index = _mapping(lines[1:end], 0, 0)
    except _Refuse:
        return Front(None, body, raw)
    if index != len(lines[1:end]):
        return Front(None, body, raw)
    return Front(fields, body, None)


def _indent(line: str) -> int:
    if "\t" in line:
        raise _Refuse()
    return len(line) - len(line.lstrip(" "))


def _mapping(lines: list[str], index: int, indent: int) -> tuple[dict[str, Any], int]:
    fields: dict[str, Any] = {}
    while index < len(lines):
        line = lines[index]
        if line.strip() == "":
            index += 1
            continue
        if line.lstrip(" ").startswith("#"):
            raise _Refuse()
        current = _indent(line)
        if current < indent:
            break
        if current != indent:
            raise _Refuse()
        name, value = _pair(line.strip())
        if name in fields:
            raise _Refuse()
        index += 1
        if value is None:
            block, index = _block(lines, index, indent)
            fields[name] = "" if block is None else block
        else:
            fields[name] = value
    return fields, index


def _block(lines: list[str], index: int, parent: int) -> tuple[list[Any] | None, int]:
    """A block list, or None when the empty value is a scalar."""

    peek = index
    while peek < len(lines) and lines[peek].strip() == "":
        peek += 1
    if peek >= len(lines):
        return None, index
    line = lines[peek]
    if line.lstrip(" ").startswith("#"):
        raise _Refuse()
    current = _indent(line)
    if current <= parent:
        return None, index
    if not line.strip().startswith("- "):
        raise _Refuse()
    items: list[Any] = []
    index = peek
    item_indent = current
    while index < len(lines):
        line = lines[index]
        if line.strip() == "":
            index += 1
            continue
        if line.lstrip(" ").startswith("#"):
            raise _Refuse()
        current = _indent(line)
        if current < item_indent:
            break
        if current != item_indent or not line.strip().startswith("- "):
            raise _Refuse()
        rest = line.strip()[2:]
        pair = _maybe_pair(rest)
        if pair is None:
            items.append(_scalar(rest))
            index += 1
            continue
        mapping, index = _flat(lines, index + 1, item_indent + 2, pair)
        items.append(mapping)
    return items, index


def _flat(
    lines: list[str], index: int, indent: int, first: tuple[str, Any]
) -> tuple[dict[str, Any], int]:
    name, value = first
    if value is None:
        raise _Refuse()
    fields = {name: value}
    while index < len(lines):
        line = lines[index]
        if line.strip() == "":
            index += 1
            continue
        if line.lstrip(" ").startswith("#"):
            raise _Refuse()
        current = _indent(line)
        if current < indent:
            break
        if current != indent or line.strip().startswith("-"):
            raise _Refuse()
        key, item = _pair(line.strip())
        if item is None or key in fields:
            raise _Refuse()
        fields[key] = item
        index += 1
    return fields, index


def _maybe_pair(text: str) -> tuple[str, Any] | None:
    name, sep, rest = text.partition(":")
    if not sep or not _KEY.fullmatch(name):
        return None
    if rest and not rest[0].isspace() and rest[0] not in "\"'[":
        return None
    return name, _value(rest.strip())


def _pair(text: str) -> tuple[str, Any]:
    found = _maybe_pair(text)
    if found is None:
        raise _Refuse()
    return found


def _value(text: str) -> Any:
    """A scalar, a flow list, or None for an empty value that may open a block."""

    if text == "":
        return None
    if text[0] == "[":
        return _flow(text)
    if text[0] in "{|>*&!":
        raise _Refuse()
    return _scalar(text)


def _scalar(text: str) -> str:
    if text == "":
        return ""
    if text[0] == '"':
        return _double(text)
    if text[0] == "'":
        return _single(text)
    if text[0] in "[]{}|>*&!":
        raise _Refuse()
    if "#" in text:
        raise _Refuse()
    return text


def _double(text: str) -> str:
    if len(text) < 2 or text[0] != '"':
        raise _Refuse()
    out: list[str] = []
    index = 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            if index + 1 >= len(text):
                raise _Refuse()
            nxt = text[index + 1]
            if nxt == "n":
                out.append("\n")
            elif nxt == "t":
                out.append("\t")
            elif nxt in '\\"':
                out.append(nxt)
            else:
                raise _Refuse()
            index += 2
            continue
        if char == '"':
            if index != len(text) - 1:
                raise _Refuse()
            return "".join(out)
        out.append(char)
        index += 1
    raise _Refuse()


def _single(text: str) -> str:
    if len(text) < 2 or text[0] != "'":
        raise _Refuse()
    out: list[str] = []
    index = 1
    while index < len(text):
        if text[index] == "'":
            if index + 1 < len(text) and text[index + 1] == "'":
                out.append("'")
                index += 2
                continue
            if index != len(text) - 1:
                raise _Refuse()
            return "".join(out)
        out.append(text[index])
        index += 1
    raise _Refuse()


def _flow(text: str) -> list[str]:
    if len(text) < 2 or text[0] != "[" or text[-1] != "]":
        raise _Refuse()
    inner = text[1:-1].strip()
    if not inner:
        return []
    items: list[str] = []
    index = 0
    expect = True
    while index < len(inner):
        while index < len(inner) and inner[index] == " ":
            index += 1
        if index >= len(inner):
            break
        if not expect:
            raise _Refuse()
        if inner[index] in "[{":
            raise _Refuse()
        if inner[index] in "\"'":
            end = _quoted_end(inner, index)
            items.append(_scalar(inner[index:end]))
            index = end
        else:
            end = index
            while end < len(inner) and inner[end] != ",":
                end += 1
            items.append(_scalar(inner[index:end].strip()))
            index = end
        expect = False
        while index < len(inner) and inner[index] == " ":
            index += 1
        if index >= len(inner):
            break
        if inner[index] != ",":
            raise _Refuse()
        index += 1
        expect = True
    if not items or expect:
        raise _Refuse()
    return items


def _quoted_end(text: str, start: int) -> int:
    quote = text[start]
    index = start + 1
    while index < len(text):
        if quote == "'" and text[index] == "'":
            if index + 1 < len(text) and text[index + 1] == "'":
                index += 2
                continue
            return index + 1
        if quote == '"' and text[index] == "\\":
            index += 2
            continue
        if text[index] == quote:
            return index + 1
        index += 1
    raise _Refuse()
