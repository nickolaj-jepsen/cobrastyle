"""Keep the workspace's versions in lockstep.

The two Python packages share one PEP 440 version and cobrastyle pins
cobrastyle-lightningcss with ==; the Rust crate (and its lockfile entry)
carries the X.Y.Z base, since Cargo versions cannot express suffixes
like .dev1.

Usage:
    python scripts/versions.py check
    python scripts/versions.py set <version>
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from re import Pattern

ROOT = Path(__file__).resolve().parent.parent

VERSION = re.compile(r"^(\d+\.\d+\.\d+)(?:(?:a|b|rc)\d+|\.dev\d+|\.post\d+)?$")
VERSION_LINE = re.compile(r'(?m)^version = "(?P<version>[^"]+)"$')
PIN = re.compile(r'"cobrastyle-lightningcss==(?P<version>[^"]+)"')
LOCK_ENTRY = re.compile(r'(?m)^name = "cobrastyle-lightningcss"\nversion = "(?P<version>[^"]+)"$')


def base_version(version: str) -> str:
    match = VERSION.match(version)
    if match is None:
        raise SystemExit(f"Invalid version {version!r}: expected X.Y.Z with an optional aN/bN/rcN/.devN/.postN suffix")
    return match.group(1)


def locations(version: str) -> list[tuple[Path, Pattern[str], str]]:
    base = base_version(version)
    return [
        (ROOT / "packages/cobrastyle/pyproject.toml", VERSION_LINE, version),
        (ROOT / "packages/cobrastyle/pyproject.toml", PIN, version),
        (ROOT / "packages/cobrastyle-lightningcss/pyproject.toml", VERSION_LINE, version),
        (ROOT / "packages/cobrastyle-lightningcss/Cargo.toml", VERSION_LINE, base),
        (ROOT / "packages/cobrastyle-lightningcss/Cargo.lock", LOCK_ENTRY, base),
    ]


def current_version() -> str:
    match = VERSION_LINE.search((ROOT / "packages/cobrastyle/pyproject.toml").read_text())
    assert match is not None, "packages/cobrastyle/pyproject.toml has no version line"
    return match.group("version")


def check() -> None:
    version = current_version()
    problems = []
    for path, pattern, expected in locations(version):
        match = pattern.search(path.read_text())
        if match is None:
            problems.append(f"{path.relative_to(ROOT)}: no match for {pattern.pattern!r}")
        elif match.group("version") != expected:
            problems.append(f"{path.relative_to(ROOT)}: expected {expected!r}, found {match.group('version')!r}")
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        raise SystemExit(f"Versions are out of lockstep; run `just bump {version}` (or the intended version) to fix.")
    print(f"Versions are in lockstep at {version}")


def set_version(version: str) -> None:
    for path, pattern, expected in locations(version):
        text = path.read_text()
        replaced, count = pattern.subn(
            lambda match, expected=expected: match.group(0).replace(match.group("version"), expected), text
        )
        if count != 1:
            raise SystemExit(f"{path.relative_to(ROOT)}: expected exactly one match for {pattern.pattern!r}")
        if replaced != text:
            path.write_text(replaced)
    print(f"Set version {version} (crate: {base_version(version)})")


def main(argv: list[str]) -> None:
    match argv:
        case ["check"]:
            check()
        case ["set", version]:
            set_version(version)
        case _:
            raise SystemExit(__doc__.strip())


if __name__ == "__main__":
    main(sys.argv[1:])
