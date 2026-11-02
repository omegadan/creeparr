"""Version and changelog steps of a release, for the Release workflow.

    release.py bump patch|minor|major   set the new version, date the Unreleased
                                        changelog section; prints the version
    release.py notes 1.2.3              print that version's changelog section
    release.py note "text"              add a line under Unreleased
    release.py version                  print the current version

The version lives in backend/creeparr/__init__.py; CHANGELOG.md keeps an
"## Unreleased" section at the top that each change adds a line to.
"""

from __future__ import annotations

import re
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERSION_FILE = ROOT / "backend" / "creeparr" / "__init__.py"
CHANGELOG = ROOT / "CHANGELOG.md"
VERSION_RE = re.compile(r'__version__ = "(\d+)\.(\d+)\.(\d+)"')
UNRELEASED = "## Unreleased"


def current() -> tuple[int, int, int]:
    match = VERSION_RE.search(VERSION_FILE.read_text())
    if match is None:
        sys.exit(f"no __version__ in {VERSION_FILE}")
    major, minor, patch = (int(n) for n in match.groups())
    return major, minor, patch


def unreleased_body(text: str) -> str:
    start = text.index(UNRELEASED) + len(UNRELEASED)
    nxt = text.find("\n## ", start)
    return text[start : nxt if nxt != -1 else len(text)].strip()


def bump(part: str) -> str:
    major, minor, patch = current()
    if part == "major":
        new = f"{major + 1}.0.0"
    elif part == "minor":
        new = f"{major}.{minor + 1}.0"
    elif part == "patch":
        new = f"{major}.{minor}.{patch + 1}"
    else:
        sys.exit(f"unknown bump {part!r}: use patch, minor or major")
    log = CHANGELOG.read_text()
    if not unreleased_body(log):
        sys.exit("CHANGELOG.md has nothing under '## Unreleased'; nothing to release")
    today = datetime.now(UTC).date().isoformat()
    CHANGELOG.write_text(log.replace(UNRELEASED, f"{UNRELEASED}\n\n## {new} ({today})", 1))
    VERSION_FILE.write_text(VERSION_RE.sub(f'__version__ = "{new}"', VERSION_FILE.read_text()))
    return new


def notes(version: str) -> str:
    log = CHANGELOG.read_text()
    match = re.search(rf"^## {re.escape(version)} \(.*?\)$", log, re.MULTILINE)
    if match is None:
        sys.exit(f"no '## {version}' section in CHANGELOG.md")
    nxt = log.find("\n## ", match.end())
    return log[match.end() : nxt if nxt != -1 else len(log)].strip()


def note(line: str) -> None:
    log = CHANGELOG.read_text()
    body = unreleased_body(log)
    entry = f"- {line}"
    head = log.index(UNRELEASED) + len(UNRELEASED)
    new_body = f"{body}\n{entry}" if body else entry
    rest = log[head:].lstrip("\n")
    rest = rest[len(body) :].lstrip("\n") if body else rest
    CHANGELOG.write_text(f"{log[:head]}\n\n{new_body}\n\n{rest}".rstrip("\n") + "\n")


def main(argv: list[str]) -> None:
    match argv:
        case ["bump", part]:
            print(bump(part))
        case ["notes", version]:
            print(notes(version))
        case ["note", line]:
            note(line)
        case ["version"]:
            print(".".join(str(n) for n in current()))
        case _:
            sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
