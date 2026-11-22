"""The one-level front matter subset. A refusal is the whole document."""

from __future__ import annotations

import pytest

from foil.front import parse_front

_ACCEPTED = """---
contract: status/v1
state: blocked
updated: 2026-01-02T03:04:05Z
questions:
  - id: reading
    ask: Which reading?
    options: ["a, b", second]
    recommended: first
    why: The tests match the first.
    evidence: board/results/t1.md
    owner: human
  - Ship the notes too?

decisions: []
---
body
"""


def test_a_flat_mapping_list_is_accepted() -> None:
    front = parse_front(_ACCEPTED)
    assert front.raw is None
    assert front.body == "body\n"
    assert front.fields is not None
    assert front.fields["state"] == "blocked"
    assert front.fields["decisions"] == []
    assert front.fields["questions"] == [
        {
            "id": "reading",
            "ask": "Which reading?",
            "options": ["a, b", "second"],
            "recommended": "first",
            "why": "The tests match the first.",
            "evidence": "board/results/t1.md",
            "owner": "human",
        },
        "Ship the notes too?",
    ]


def test_a_plain_string_and_a_block_list_of_scalars_are_accepted() -> None:
    text = "---\nquestions: Ship it?\nnotes:\n  - one\n  - two\n---\n"
    front = parse_front(text)
    assert front.raw is None
    assert front.fields == {"questions": "Ship it?", "notes": ["one", "two"]}


def test_blank_lines_and_crlf_are_accepted() -> None:
    text = "---\r\nstate: working\r\n\r\nquestions: []\r\n---\r\nbody\r\n"
    front = parse_front(text)
    assert front.raw is None
    assert front.fields == {"state": "working", "questions": []}
    assert front.body == "body\n"


def test_an_empty_document_has_no_fields() -> None:
    front = parse_front("---\n---\n")
    assert front.raw is None
    assert front.fields == {}


def test_no_fence_and_an_unclosed_fence_are_not_front_matter() -> None:
    assert parse_front("hello\n").raw is None
    assert parse_front("hello\n").fields is None
    assert parse_front("---\nstate: working\n").raw is None
    assert parse_front("---\nstate: working\n").fields is None


@pytest.mark.parametrize(
    "body",
    [
        "state: blocked\nnested:\n  inner: yes\n",
        "state: blocked\nquestions:\n  - id: reading\n    options:\n      - first\n",
        "state: blocked\npoint: {x: 1}\n",
        "state: blocked\nstory: |\n  more\n",
        "state: blocked\nname: !!str hello\n",
        "state: blocked\nname: &a hello\n",
        "state: blocked\n# note\nquestions: []\n",
        "state: blocked\n\tquestions: []\n",
        "state: blocked\nstate: done\n",
        'ask: "unterminated\n',
        "options: [first,]\n",
        "options: [[first]]\n",
        "options: [first, {id: a}]\n",
        "contract:status/v1\n",
    ],
)
def test_anything_outside_the_subset_is_refused_whole(body: str) -> None:
    text = f"---\n{body}---\nkept\n"
    front = parse_front(text)
    assert front.fields is None
    assert front.raw == "---\n" + body + "---"
    assert front.body == "kept\n"
    assert "state" not in (front.fields or {})
