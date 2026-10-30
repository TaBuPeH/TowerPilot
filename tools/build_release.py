"""Build a source beta from reviewed source paths, never the workspace wholesale."""
import argparse
import hashlib
import importlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from collections.abc import Collection, Iterable, Mapping
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
TOP = {'VERSION', 'LICENSE', 'README.md', 'requirements.txt', 'requirements-dev.txt', 'requirements-portable.lock', 'requirements-scrcpy.txt',
       'pytest.ini', 'pyproject.toml', 'Start Tower Pilot.cmd', 'Start-TowerPilot.ps1', '.gitignore', '.gitattributes',
       # agent rules and skills ship with the source (0.2-beta): CLAUDE.md is
       # the rule book, AGENTS.md points Codex and others at it
       'CLAUDE.md', 'AGENTS.md', 'DESIGN.md'}
DOCS = {'README.md', 'ACCOUNT_SETUP.md', 'BEHAVIORS.md', 'BLUESTACKS.md', 'GLOBAL_REWARDS.md',
        'RUN_TEMPLATES.md', 'RELEASE_0.1_BETA.md', 'RELEASE_0.2_BETA.md', 'RELEASE_0.3_BETA.md', 'RELEASE_0.4_BETA.md', 'RELEASE_0.5_BETA.md', 'RELEASE_0.6_BETA.md', 'RUNTIME_GEOMETRY.md',
        'PORTABLE_BUILD.md', 'SETTINGS.md', 'LEARNING_FLOWS.md', 'OBSERVABILITY.md',
        'WORKFLOW_ARCHITECTURE_PROPOSAL.md', 'RUN_LIFECYCLE_PLAN.md',
        'RUN_WORKSPACE_REVIEW.md', 'VIDEO_PREVIEW.md'}
# Whole folders that ship as they are: the Claude Code skills, agents, hooks
# and workflow helpers, the project-local Codex config, the release workflow.
# Personal files never do: .claude/settings.local.json is a per-machine
# permission list and is excluded by name.
AGENT_DIRS = {'.claude', '.codex', '.github'}
AGENT_EXCLUDE = {'.claude/settings.local.json'}
# The dashboard app (ADR-001). Its sources ship so a release can be rebuilt
# and reviewed (decision 2); npm and test state never do.
APP_SOURCE_SUFFIXES = {'.ts', '.tsx', '.mjs', '.cjs', '.js', '.json', '.css', '.html', '.md'}
APP_SOURCE_NAMES = {'.npmrc', '.prettierignore', '.gitignore'}
APP_SOURCE_EXCLUDE = {'node_modules', 'test-results', 'playwright-report', 'coverage'}
# Its build is git-ignored, so it is added from the filesystem: index.html,
# hashed .js and .css, the npm licence notices and the build's own inventory.
# Nothing else may ship from it - no sourcemaps, fonts or images.
APP_BUILD = 'frontend/webui/app'
APP_NOTICES = 'THIRD_PARTY_NOTICES.txt'
# Written by the build (frontend/app/scripts/build-inventory.ts) after Vite has
# checked its chunk graph: '<sha256>  <path>' for every file of the build.
APP_INVENTORY = 'BUILD_INVENTORY.txt'
APP_BUILD_SUFFIXES = {'.html', '.js', '.css'}
APP_BUILD_HINT = 'cd frontend/app; npm ci; npm run build'
# Written at the archive root by both builders (release_identity below); the reader
# and the field list are backend/runtime/build_info.py, which the server runs.
IDENTITY = 'release_identity.json'
IDENTITY_SCHEMA = 1
LOCK = 'requirements-portable.lock'
# Outside the app, the frontend is the static page that redirects old
# /ui/index.html bookmarks onto the app (plan R17, option A); the Flask server
# that serves it is backend/dashboard.py, shipped by the backend rule. The
# previous dashboard's pages, modules and node tests are retired and never ship.
FRONTEND_FILES = {'frontend/webui/index.html'}


def allowed(name):
    p = Path(name)
    if name in TOP:
        return True
    if p.parts[0] in AGENT_DIRS:
        return (name not in AGENT_EXCLUDE and len(p.parts) > 1
                and p.suffix in {'.md', '.json', '.js', '.cjs', '.ps1', '.toml', '.yml', '.yaml', '.txt'})
    if p.parts[0] == 'docs':
        return len(p.parts) == 2 and p.name in DOCS
    if name in {'backend/config.example.yaml', 'backend/profiles/default.yaml',
                'backend/tests/fixtures/golden_profile.yaml', 'backend/tools/winocr.ps1',
                'tools/build_release.py', 'tools/build_portable.py', 'tools/check_release_zips.py',
                'tools/portable_launcher.py', 'tools/portable_pins.json',
                'tools/release_archive_policy.py', 'tools/dependency_images.json', 'tools/release_marker_allowlist.json',
                'tools/generate_dependency_images.py'}:
        return True
    if p.parts[0] == 'backend':
        return (len(p.parts) > 1 and p.parts[1] not in {
            'accounts', 'captures', 'logs', 'runs', 'recordings', 'profiles', 'templates',
            'tools', '__pycache__'} and p.suffix in {'.py', '.json', '.md'})
    if p.parts[:2] == ('frontend', 'app'):
        return (len(p.parts) > 2 and not APP_SOURCE_EXCLUDE.intersection(p.parts)
                and (p.suffix in APP_SOURCE_SUFFIXES or p.name in APP_SOURCE_NAMES))
    if p.parts[:3] == ('frontend', 'webui', 'app'):
        return False                # the build product: app_build_files() adds it
    return name in FRONTEND_FILES


class _AppIndexParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.references: list[str] = []
        self.module_scripts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.references.extend(value for key, value in attrs
                               if key in {'src', 'href'} and value is not None)
        attributes = dict(attrs)
        if tag == 'script' and (attributes.get('type') or '').strip().lower() == 'module':
            src = attributes.get('src')
            if src is not None:
                self.module_scripts.append(src)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)


def _app_reference(value: str) -> str | None:
    if not value.startswith('/ui/app/'):
        return None
    return unquote(value[len('/ui/app/'):].split('#', 1)[0].split('?', 1)[0])


def _inventory_problems(files: Mapping[str, bytes]) -> list[str]:
    """The build must match the inventory the build wrote: every listed file
    present with its hash, and nothing unlisted. This is what catches a chunk,
    stylesheet or asset that index.html does not name directly."""
    if APP_INVENTORY not in files:
        return [f'missing {APP_INVENTORY} (written by the build; rebuild the app)']
    try:
        lines = files[APP_INVENTORY].decode('utf-8').splitlines()
    except UnicodeDecodeError:
        return [f'{APP_INVENTORY} is not UTF-8']
    problems = []
    listed: set[str] = set()
    for number, line in enumerate(lines, 1):
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'([0-9a-f]{64})  (\S.*)', line)
        if match is None:
            problems.append(f'{APP_INVENTORY} malformed line {number}')
            continue
        digest, name = match.groups()
        if name in listed or name == APP_INVENTORY:
            problems.append(f'{APP_INVENTORY} lists {name} twice or lists itself')
        listed.add(name)
        if name not in files:
            problems.append(f'missing build output listed in {APP_INVENTORY}: {name}')
        elif hashlib.sha256(files[name]).hexdigest() != digest:
            problems.append(f'build output differs from {APP_INVENTORY}: {name}')
    if not listed:
        problems.append(f'{APP_INVENTORY} lists no files')
    for name in sorted(set(files) - listed - {APP_INVENTORY}):
        problems.append(f'file not in {APP_INVENTORY}: {name}')
    return problems

def app_build_problems(files: Mapping[str, bytes]) -> list[str]:
    """Validate a build snapshot, with paths relative to frontend/webui/app/."""
    problems = []
    if 'index.html' not in files:
        problems.append('missing index.html')
    if APP_NOTICES not in files:
        problems.append(f'missing {APP_NOTICES}')
    elif not files[APP_NOTICES].strip():
        problems.append(f'empty {APP_NOTICES}')
    for name in sorted(files):
        suffix = PurePosixPath(name).suffix.lower()
        if suffix == '.map':
            problems.append(f'sourcemap may not ship: {name}')
        elif suffix not in APP_BUILD_SUFFIXES and name not in {APP_NOTICES, APP_INVENTORY}:
            problems.append(f'file may not ship: {name}')
    problems.extend(_inventory_problems(files))
    if 'index.html' in files:
        parser = _AppIndexParser()
        try:
            parser.feed(files['index.html'].decode('utf-8'))
            parser.close()
        except UnicodeDecodeError:
            problems.append('index.html is not UTF-8')
        for value in parser.references:
            relative = _app_reference(value)
            if relative is None:
                continue
            if '..' in relative.split('/') or '\\' in relative:
                problems.append(f'invalid app reference (path traversal): {value}')
            elif relative not in files:
                problems.append(f'missing referenced app file: {relative} ({value})')
        if not any((relative := _app_reference(value)) is not None
                   and '..' not in relative.split('/') and '\\' not in relative
                   and PurePosixPath(relative).suffix == '.js'
                   for value in parser.module_scripts):
            problems.append('index.html has no <script type="module"> referencing a /ui/app/ .js file')
    return problems


def app_build_files(root: Path = ROOT, *, build_dir: Path | None = None) -> list[str]:
    """The built dashboard app, from the filesystem. Refuses a release without
    a build, or with a file the build may not contain."""
    build_dir = root / (APP_BUILD if build_dir is None else build_dir)
    files = {path.relative_to(build_dir).as_posix(): path.read_bytes()
             for path in sorted(build_dir.rglob('*')) if path.is_file()}
    problems = app_build_problems(files)
    if problems:
        raise RuntimeError(f'{APP_BUILD}/ is not built or holds files a release may not ship:\n'
                           + '\n'.join(problems) + f'\nBuild the dashboard app first: {APP_BUILD_HINT}')
    return [f'{APP_BUILD}/{name}' for name in files]


def git_listing(root: Path) -> list[str]:
    """Git boundary of release_files: every path git knows under `root`, tracked or untracked and not ignored."""
    return subprocess.check_output(
        ['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=root).decode().split('\0')


def git_status(root: Path) -> bytes:
    """Git boundary of working_tree: `git status` in porcelain form, empty when nothing differs from the commit.

    Untracked files git does not ignore are listed: release_files() would ship them.
    """
    return subprocess.run(['git', '--no-optional-locks', 'status', '--porcelain', '--untracked-files=all'],
                          cwd=root, capture_output=True, check=True, timeout=120).stdout


def release_files(root: Path = ROOT, *, build_dir: Path | None = None) -> list[str]:
    """Every file a release ships: the reviewed source paths git knows about,
    plus the dashboard app build."""
    names = git_listing(root)
    sources = {n for n in names if n and allowed(n) and (root / n).is_file()}
    return sorted(sources | set(app_build_files(root, build_dir=build_dir)))


def stage_files(stage: Path, root: Path, files: Iterable[str], skip: Collection[str] = ()) -> None:
    """Copy release files into `stage` under their relative paths; the staged tree is what ships."""
    for name in files:
        if name in skip:
            continue
        dest = stage / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, dest)


def working_tree(root: Path) -> str:
    """`clean`, `dirty` or `unknown` (not a git checkout, or git cannot say) from `git status` now.

    Untracked files git does not ignore make the tree dirty: release_files() ships them.
    """
    if not (root / '.git').exists():
        return 'unknown'
    try:
        status = git_status(root)
    except (OSError, subprocess.SubprocessError):
        return 'unknown'
    return 'dirty' if status.strip() else 'clean'


def require_clean_tree(root: Path, allow_dirty: bool = False) -> str:
    """The working-tree state to record; refuses anything but `clean` unless this is a rehearsal."""
    state = working_tree(root)
    if state != 'clean' and not allow_dirty:
        raise RuntimeError(f'the working tree is {state}: build a release from a clean, committed tree '
                           f'(--allow-dirty is for rehearsals; it records {state} in {IDENTITY})')
    return state


def _build_info():
    """backend/runtime/build_info.py, the module the server fingerprints itself with.

    Loaded by name when an identity is written, not imported at load: the archive checker and the
    installed-ZIP qualifier import this module and must stay free of application code.
    """
    backend = str(ROOT / 'backend')
    if backend not in sys.path:
        sys.path.insert(0, backend)
    return importlib.import_module('runtime.build_info')


def write_identity(stage: Path, root: Path, kind: str, tree: str, *, python: Mapping[str, str] | None = None) -> dict:
    """Write release_identity.json at the root of `stage`, the tree about to be zipped.

    The fingerprint, the app inventory hash and the lock hash are taken from the staged files, so the
    running install can compare itself with them. Nothing here names a path, a user or a machine.
    """
    info = _build_info()
    identity = dict(
        schema_version=IDENTITY_SCHEMA, version=(stage / 'VERSION').read_text().strip(), kind=kind,
        source_fingerprint=info.source_fingerprint(stage),
        app_build_sha256=hashlib.sha256((stage / APP_BUILD / APP_INVENTORY).read_bytes()).hexdigest(),
        lock_sha256=hashlib.sha256((stage / LOCK).read_bytes()).hexdigest(),
        built_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        source_revision=info.revision(root), working_tree=tree)
    if python is not None:
        identity['python'] = dict(python)
    unknown = identity.keys() - set(info.IDENTITY_FIELDS)
    if unknown:
        raise RuntimeError(f'{IDENTITY} fields the server does not read: {sorted(unknown)}')
    if kind not in info.RELEASE_KINDS:
        raise RuntimeError(f'{IDENTITY} kind the server does not read: {kind}')
    (stage / IDENTITY).write_bytes((json.dumps(identity, indent=2, sort_keys=True) + '\n').encode('utf-8'))
    return identity


def command_line(description: str | None, argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('--allow-dirty', action='store_true',
                        help=f'rehearsal only: build from a dirty or unknown working tree; {IDENTITY} records it')
    return parser.parse_args(argv)


def build(root: Path = ROOT, dist: Path | None = None, *, allow_dirty: bool = False) -> Path:
    tree = require_clean_tree(root, allow_dirty)    # first: nothing is read, copied or zipped from a tree that may change
    files = release_files(root)          # refuses early when the dashboard app is not built
    version = (root / 'VERSION').read_text().strip()
    prefix = f'TowerPilot-{version}'
    output = (root / 'dist' if dist is None else dist) / f'{prefix}-source.zip'
    with tempfile.TemporaryDirectory(prefix='source-build-') as work:
        stage = Path(work) / prefix
        stage_files(stage, root, files)
        write_identity(stage, root, 'source', tree)
        output.parent.mkdir(exist_ok=True)
        inventory = []
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name in sorted([*files, IDENTITY]):
                content = (stage / name).read_bytes()
                archive.writestr(prefix + '/' + name, content)
                inventory.append(hashlib.sha256(content).hexdigest() + '  ' + name)
            archive.writestr(prefix + '/SHA256SUMS.txt', '\n'.join(inventory) + '\n')
    output.with_suffix('.zip.sha256').write_text(
        hashlib.sha256(output.read_bytes()).hexdigest() + '  ' + output.name + '\n')
    print(f'{output} ({len(files) + 1} files)')
    return output


def main(argv: list[str] | None = None) -> None:
    build(allow_dirty=command_line(__doc__, argv).allow_dirty)


if __name__ == '__main__':
    main()
