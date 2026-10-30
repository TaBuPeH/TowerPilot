"""Source provenance captured when a process loads, independent of Git executables.

The fingerprint describes source on disk at capture time, not a claim that every
module was already imported. Local account data and generated files are excluded.
The release identity written at build time is read here too (build_info.rules.md).
"""
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid


_EXCLUDED = {'tests', '__pycache__', 'accounts', 'logs', 'runs', 'captures',
             'profiles', 'templates', '.venv', 'node_modules'}


def source_fingerprint(root: Path) -> str:
    """Hash application source only; stable across file timestamps and checkout paths."""
    digest = hashlib.sha256()
    sources = []

    def unreadable(error: OSError) -> None:
        raise error

    for directory in ('backend', 'frontend'):
        base = root / directory
        if not base.exists():
            continue  # source-only fixtures and partial packaged installs
        for current, directories, filenames in os.walk(base, onerror=unreadable):
            directories[:] = sorted(name for name in directories if name not in _EXCLUDED)
            sources.extend(Path(current) / name for name in filenames if name.endswith('.py'))
    if not sources:
        raise FileNotFoundError('No application Python source found')
    for path in sorted(sources):
        digest.update(path.relative_to(root).as_posix().encode('utf-8') + b'\0')
        digest.update(path.read_bytes())
        digest.update(b'\0')
    return digest.hexdigest()


def revision(root: Path) -> str | None:
    """Read loose or packed Git refs, including linked worktrees; archives return None."""
    git = root / '.git'
    if git.is_file():
        git = root / git.read_text(encoding='utf-8').strip().removeprefix('gitdir: ')
    if not git.is_dir():
        return None
    head = (git / 'HEAD').read_text(encoding='utf-8').strip()
    if not head.startswith('ref: '):
        return head
    ref = head.removeprefix('ref: ')
    common = git
    if (git / 'commondir').exists():
        common = git / (git / 'commondir').read_text(encoding='utf-8').strip()
    for directory in (git, common):
        if (directory / ref).exists():
            return (directory / ref).read_text(encoding='utf-8').strip()
    packed = common / 'packed-refs'
    if packed.exists():
        for line in packed.read_text(encoding='utf-8').splitlines():
            if line.endswith(' ' + ref):
                return line.split()[0]
    return None


def snapshot(root: Path, role: str) -> dict:
    """Capture once in each process before its long-running work begins."""
    result = dict(role=role, pid=os.getpid(), session_id=str(uuid.uuid4()),
                  captured_at=time.time(), source_revision=None,
                  source_fingerprint=None, error=None)
    try:
        result.update(source_revision=revision(root), source_fingerprint=source_fingerprint(root))
    except (OSError, ValueError) as exc:
        result['error'] = {'type': type(exc).__name__, 'message': str(exc)}
    return result


def compare(root: Path, started: dict) -> dict:
    """Explicit freshness check; never relabel an old process with the new disk revision."""
    result = dict(started, disk_source_fingerprint=None, source_changed=None)
    try:
        current = source_fingerprint(root)
        result['disk_source_fingerprint'] = current
        if started.get('source_fingerprint') is not None:
            result['source_changed'] = current != started['source_fingerprint']
    except (OSError, ValueError) as exc:
        result['error'] = {'type': type(exc).__name__, 'message': str(exc)}
    return result


# --------------------------------------------------------------- release identity
# Written at build time by tools/build_release.py (both ZIPs). The reader is here because the
# fingerprint it compares against is this module's own; see build_info.rules.md.
RELEASE_IDENTITY = 'release_identity.json'
RELEASE_KINDS = ('source', 'portable-windows-x64')
IDENTITY_FIELDS = ('schema_version', 'version', 'kind', 'source_fingerprint', 'app_build_sha256',
                   'lock_sha256', 'python', 'built_at', 'source_revision', 'working_tree')
APP_BUILD = ('frontend', 'webui', 'app')       # the built dashboard app, relative to the release root
APP_INVENTORY = 'BUILD_INVENTORY.txt'          # '<sha256>  <path>' per file of the build, written by the app build
_INVENTORY_LINE = re.compile(r'([0-9a-f]{64})  (\S.*)')


def _matches_source(root: Path, recorded: object) -> bool | None:
    if not isinstance(recorded, str):
        return False
    try:
        return source_fingerprint(root) == recorded
    except (OSError, ValueError):
        return None             # the files cannot be read: unknown, not "changed"


def _matches_app_build(app: Path, recorded: object) -> bool | None:
    """Every file under the app folder against the inventory's lines, and the inventory against the identity.

    An edited, missing or extra file reads False, whether or not the inventory was edited with it.
    """
    try:
        inventory = (app / APP_INVENTORY).read_bytes()
    except FileNotFoundError:
        return False
    except OSError:
        return None
    if not isinstance(recorded, str) or hashlib.sha256(inventory).hexdigest() != recorded:
        return False
    try:
        lines = inventory.decode('utf-8').splitlines()
    except UnicodeDecodeError:
        return False
    listed: dict[str, str] = {}
    for line in lines:
        if not line or line.startswith('#'):
            continue
        match = _INVENTORY_LINE.fullmatch(line)
        if match is None or match[2] in listed or match[2] == APP_INVENTORY:
            return False
        listed[match[2]] = match[1]
    try:
        present = {path.relative_to(app).as_posix() for path in app.rglob('*') if path.is_file()}
        if not listed or present - {APP_INVENTORY} != listed.keys():
            return False
        return all(hashlib.sha256((app / name).read_bytes()).hexdigest() == digest
                   for name, digest in listed.items())
    except OSError:
        return None


def release_identity(root: Path, *, verify: bool = True) -> dict:
    """What this tree says it is: its build-time release_identity.json, or `checkout` when it has none.

    Always the same keys: the identity fields (null when absent), `matches_source` and
    `matches_app_build` (the record against the files on disk now; null when not
    applicable or not readable) and `error` (why the kind is `unknown`). `verify=False` reads the
    record only, for callers that need the kind and not the hashing.
    """
    root = Path(root)
    result = dict.fromkeys(IDENTITY_FIELDS) | dict(matches_source=None, matches_app_build=None, error=None)
    try:
        recorded = json.loads((root / RELEASE_IDENTITY).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return result | dict(kind='checkout')
    except OSError:
        return result | dict(kind='unknown', error='unreadable')
    except ValueError:
        return result | dict(kind='unknown', error='not valid JSON')
    if not isinstance(recorded, dict):
        return result | dict(kind='unknown', error='not an object')
    if recorded.get('kind') not in RELEASE_KINDS:
        return result | dict(kind='unknown', error='unrecognized kind')
    result.update({field: recorded.get(field) for field in IDENTITY_FIELDS})
    if verify:
        result.update(matches_source=_matches_source(root, recorded.get('source_fingerprint')),
                      matches_app_build=_matches_app_build(root.joinpath(*APP_BUILD),
                                                           recorded.get('app_build_sha256')))
    return result
