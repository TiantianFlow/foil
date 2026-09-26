"""Public documentation hygiene contracts."""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).parents[1]
APPROVED_AUTHOR_NAME = "TiantianFlow"
APPROVED_AUTHOR_EMAIL = "177855728+TiantianFlow@users.noreply.github.com"
PUBLIC_URL_HOSTS = frozenset(
    {
        "github.com",
        "img.shields.io",
        "json-schema.org",
        "www.apache.org",
        "apache.org",
        "foil.local",
    }
)
USER_HOME = re.compile(
    "(?:"
    + re.escape("/" + "Users" + "/")
    + "|"
    + re.escape("/" + "home" + "/")
    + "|"
    + r"[A-Za-z]:[\\/]"
    + re.escape("Users")
    + r"[\\/]"
    + ")[^/\\\\\\s\"'`]+"
)
EMAIL = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.(?:com|org|net|edu|io)\b",
    re.IGNORECASE,
)
MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
BACKTICK_REPO_PATH = re.compile(
    r"`((?:docs|skills|schemas|adapters|profiles|tests|src|README)[^`\n]{0,200})`"
)
URL = re.compile(r"(?:https?|git\+https)://[^\s\"'`<>)\]}]+", re.IGNORECASE)
SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[-_]?key|secret[-_]?key|private[-_]?key|access[-_]?token|"
    r"auth[-_]?token|password)\s*[:=]\s*['\"][^'\"]+['\"]"
)
PRIVATE_KEY_BLOCK = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
ENV_DUMP = re.compile(r"(?i)\bprintenv\b|\benv\s*\|\s*(?:sort|cat)\b")
RESTRICTED_STEM_TOKENS = frozenset({"report", "audit", "inventory", "leak"})


def _tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _read_text(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if "\0" in text:
        return None
    return text


def _tracked_text() -> list[tuple[str, Path, str]]:
    rows: list[tuple[str, Path, str]] = []
    for relative in _tracked_files():
        path = ROOT / relative
        text = _read_text(path)
        if text is None:
            continue
        rows.append((relative, path, text))
    return rows


def _url_host(raw: str) -> str | None:
    parsed = urlparse(raw.rstrip(".,;:"))
    host = parsed.hostname
    if host is None:
        return None
    return host.lower()


def _is_restricted_artifact(relative: str) -> bool:
    posix = relative.replace("\\", "/")
    parts = [part.lower() for part in Path(posix).parts]
    stem_tokens = set(re.split(r"[-_.]", Path(posix).stem.lower()))
    return bool(parts and parts[0] == "docs" and stem_tokens & RESTRICTED_STEM_TOKENS)


def test_package_author_metadata_is_approved_identity() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["authors"] == [
        {"name": APPROVED_AUTHOR_NAME, "email": APPROVED_AUTHOR_EMAIL}
    ]


def test_tracked_text_has_no_absolute_user_home_paths() -> None:
    offenders: list[str] = []

    for relative, _path, text in _tracked_text():
        for line_number, line in enumerate(text.splitlines(), 1):
            if USER_HOME.search(line):
                offenders.append(f"{relative}:{line_number}")

    assert offenders == []


def test_tracked_text_has_no_unapproved_email() -> None:
    offenders: list[str] = []
    approved = APPROVED_AUTHOR_EMAIL.lower()

    for relative, _path, text in _tracked_text():
        for line_number, line in enumerate(text.splitlines(), 1):
            for match in EMAIL.finditer(line):
                if match.group(0).lower() != approved:
                    offenders.append(f"{relative}:{line_number}")

    assert offenders == []


def _should_scan_urls(relative: str, path: Path) -> bool:
    if Path(relative).name == "uv.lock":
        return False
    return path.name == "LICENSE" or path.suffix.lower() in {
        ".md",
        ".toml",
        ".json",
        ".yml",
        ".yaml",
    }


def test_tracked_docs_urls_use_public_host_allowlist() -> None:
    offenders: list[str] = []

    for relative, path, text in _tracked_text():
        if not _should_scan_urls(relative, path):
            continue
        for line_number, line in enumerate(text.splitlines(), 1):
            for match in URL.finditer(line):
                host = _url_host(match.group(0))
                if host not in PUBLIC_URL_HOSTS:
                    offenders.append(f"{relative}:{line_number}")

    assert offenders == []


def test_tracked_docs_have_no_credential_material() -> None:
    offenders: list[str] = []

    for relative, path, text in _tracked_text():
        if path.suffix.lower() != ".md":
            continue
        for line_number, line in enumerate(text.splitlines(), 1):
            if (
                PRIVATE_KEY_BLOCK.search(line)
                or SECRET_ASSIGNMENT.search(line)
                or ENV_DUMP.search(line)
            ):
                offenders.append(f"{relative}:{line_number}")

    assert offenders == []


def test_tracked_tree_has_no_report_or_inventory_artifacts() -> None:
    leftovers = {
        relative for relative in _tracked_files() if _is_restricted_artifact(relative)
    }
    docs = ROOT / "docs"
    if docs.is_dir():
        leftovers.update(
            path.relative_to(ROOT).as_posix()
            for path in docs.rglob("*")
            if path.is_file() and _is_restricted_artifact(path.relative_to(ROOT).as_posix())
        )
    assert sorted(leftovers) == []


def test_tracked_markdown_paths_and_links_exist() -> None:
    missing: list[str] = []
    tracked = {relative for relative, path, _text in _tracked_text() if path.suffix == ".md"}

    for relative in tracked:
        path = ROOT / relative
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), 1):
            for match in MARKDOWN_LINK.finditer(line):
                target = unquote(match.group(1).split("#", 1)[0])
                if not target or target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                parsed = urlparse(target)
                if parsed.scheme:
                    continue
                resolved = (path.parent / target).resolve()
                try:
                    resolved.relative_to(ROOT)
                except ValueError:
                    missing.append(f"{relative}:{line_number}:{target}")
                    continue
                if not resolved.exists():
                    missing.append(f"{relative}:{line_number}:{target}")
            for match in BACKTICK_REPO_PATH.finditer(line):
                raw = match.group(1).strip()
                candidate = raw.split()[0].rstrip(".,);:")
                if not candidate or "{" in candidate or "example" in candidate:
                    continue
                repo_path = ROOT / candidate
                if not repo_path.exists():
                    missing.append(f"{relative}:{line_number}:`{candidate}`")

    assert missing == []


def test_docs_index_lists_every_document() -> None:
    docs = ROOT / "docs"
    index = (docs / "README.md").read_text(encoding="utf-8")
    linked = {
        unquote(match.group(1).split("#", 1)[0])
        for match in MARKDOWN_LINK.finditer(index)
    }
    documents = {
        path.relative_to(docs).as_posix()
        for path in docs.rglob("*.md")
        if path.relative_to(docs).as_posix() != "README.md"
    }
    assert sorted(documents - linked) == []
