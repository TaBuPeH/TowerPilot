"""A git-free stand-in for the two git calls the release tools make (build_release.git_listing and
build_release.working_tree), so release tests build from a plain temporary folder and never run `git`.

The listing is every file under the root that the folder's own `.gitignore` does not ignore, using a small
emulation of the gitignore rules the repository's file uses (comments, `!`, trailing `/`, leading `/`, patterns
with a `/` anchored at the root, `*` `?` `[]` globs that do not cross a `/`). It is NOT git: the git behavior
itself is outside what these tests prove. The working-tree answer is whatever the test sets (default `clean`)."""
from __future__ import annotations

import fnmatch
from pathlib import Path
from types import ModuleType

import pytest


def _rules(root: Path) -> list[tuple[bool, bool, bool, list[str]]]:
    """(negated, directories only, anchored, pattern segments) per rule of root/.gitignore."""
    path = root / '.gitignore'
    rules = []
    for raw in (path.read_text(encoding='utf-8').splitlines() if path.is_file() else []):
        line = raw.rstrip()
        if not line or line.startswith('#'):
            continue
        negated = line.startswith('!')
        line = line[1:] if negated else line
        directory = line.endswith('/')
        line = line.strip('/') if directory else line
        anchored = '/' in line.strip('/') or line.startswith('/')
        rules.append((negated, directory, anchored, line.strip('/').split('/')))
    return rules


def _matches(rule: tuple[bool, bool, bool, list[str]], parts: tuple[str, ...], is_dir: bool) -> bool:
    _, directory_only, anchored, pattern = rule
    if directory_only and not is_dir:
        return False
    if anchored:
        return len(pattern) == len(parts) and all(fnmatch.fnmatchcase(p, q) for p, q in zip(parts, pattern))
    return fnmatch.fnmatchcase(parts[-1], pattern[0])


def is_ignored(rules: list, relative: str) -> bool:
    """True when the file, or any folder above it, is ignored (git does not re-include inside an ignored folder)."""
    parts = tuple(relative.split('/'))
    for depth in range(1, len(parts) + 1):
        ignored = False
        for rule in rules:
            if _matches(rule, parts[:depth], depth < len(parts)):
                ignored = not rule[0]
        if ignored:
            return True
    return False


def listing(root: Path) -> list[str]:
    rules = _rules(root)
    names = [path.relative_to(root).as_posix() for path in sorted(root.rglob('*')) if path.is_file()]
    return [name for name in names if not name.startswith('.git/') and not is_ignored(rules, name)]


def install(monkeypatch: pytest.MonkeyPatch, build_release: ModuleType, tree: str = 'clean') -> None:
    """Replace build_release's git boundary; `build_release.FAKE_TREE` holds the answer working_tree gives."""
    monkeypatch.setattr(build_release, 'REAL_WORKING_TREE', build_release.working_tree, raising=False)    # unreplaced, for its own tests
    monkeypatch.setattr(build_release, 'git_listing', listing)
    monkeypatch.setattr(build_release, 'FAKE_TREE', tree, raising=False)
    monkeypatch.setattr(build_release, 'working_tree', lambda root: build_release.FAKE_TREE)
