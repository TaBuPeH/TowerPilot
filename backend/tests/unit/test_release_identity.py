"""The release identity (roadmap item 10, package p10a): written by both builders, read by build_info,
required by the archive gate. Offline and synthetic: the trees are plain temporary folders, the portable build
runs with its two network steps (the embedded Python download, the pip install) stubbed, and NO test here runs
git: the release tools' two git calls are replaced (support/fake_git_tree.py) and working_tree() is tested over
canned `git status` answers. What git itself reports for ignored, untracked or staged files is therefore not
proved by this file. Nothing here starts the dashboard on a real port or touches a device."""
import getpass
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import zipfile
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

import fake_git_tree
from runtime import build_info, environment
from test_release_app_build import BUILD, REPO, SOURCES, _write, checkout, tools    # noqa: F401 (fixtures)
from test_runtime_environment import local_dashboard                                  # noqa: F401 (fixture)

APP = 'frontend/webui/app/'
REVISION = 'c0ffee' * 6 + 'c0ff'
KINDS = ('source', 'portable-windows-x64')
PORTABLE_HOST = sys.platform == 'win32' and sys.version_info[:2] == (3, 12) and platform.architecture()[0] == '64bit'


def prefix_of(kind: str) -> str:
    return 'TowerPilot-9.9.9' + ('-windows-x64' if kind == 'portable-windows-x64' else '')


def extract(archive: Path, target: Path) -> Path:
    """Unzip like a person would; returns the install folder (the archive's one top folder)."""
    with zipfile.ZipFile(archive) as zipped:
        zipped.extractall(target)
    (folder,) = target.iterdir()
    return folder


def build_portable_zip(tools: tuple[ModuleType, ModuleType], checkout: Path, monkeypatch: pytest.MonkeyPatch,
                       *, allow_dirty: bool = False) -> Path:
    """build_portable.build() over the synthetic checkout, with the Python download and pip install stubbed."""
    _, build_portable = tools
    embedded = checkout / 'dist' / 'downloads' / build_portable.URL.rsplit('/', 1)[1]
    embedded.parent.mkdir(parents=True)
    with zipfile.ZipFile(embedded, 'w') as runtime:
        runtime.writestr('python.exe', b'MZ synthetic interpreter')
        runtime.writestr('LICENSE.txt', b'Synthetic runtime licence\n')
    monkeypatch.setattr(build_portable, 'ROOT', checkout)
    monkeypatch.setattr(build_portable, 'SHA256', hashlib.sha256(embedded.read_bytes()).hexdigest())
    monkeypatch.setattr(build_portable.urllib.request, 'urlretrieve',
                        lambda *args, **kwargs: pytest.fail('portable build downloaded Python'))
    monkeypatch.setattr(build_portable, 'install_dependencies', lambda target, lock=None: target.mkdir(parents=True))
    monkeypatch.setattr(build_portable, 'subprocess', SimpleNamespace(run=lambda *args, **kwargs: None))
    return build_portable.build(allow_dirty=allow_dirty)


@pytest.fixture()
def portable_checkout(checkout: Path) -> Path:
    """The synthetic checkout plus the launcher build() copies, and a detached HEAD file: build_info.revision reads
    it as plain text, the way it reads any checkout (git is never run, and the file is not a repository)."""
    _write(checkout, {'tools/portable_launcher.py': b'# synthetic launcher\n', '.git/HEAD': (REVISION + '\n').encode()})
    return checkout


@pytest.fixture(params=KINDS)
def release(request: pytest.FixtureRequest, tools: tuple[ModuleType, ModuleType], portable_checkout: Path,
            tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """One archive per kind, built by the real builder, with its install folder extracted."""
    kind = request.param
    if kind == 'source':
        archive = tools[0].build(portable_checkout, tmp_path / 'dist')
    elif PORTABLE_HOST:
        archive = build_portable_zip(tools, portable_checkout, monkeypatch)
    else:
        pytest.skip('the portable build needs 64-bit Python 3.12 on Windows')
    install = extract(archive, tmp_path / 'install')
    return SimpleNamespace(kind=kind, archive=archive, install=install, checkout=portable_checkout,
                           identity=json.loads((install / 'release_identity.json').read_text(encoding='utf-8')))


# ---------------------------------------------------------------- both builders write it
def test_identity_round_trips_through_both_builders(release: SimpleNamespace) -> None:
    identity = release.identity
    expected = set(build_info.IDENTITY_FIELDS) - ({'python'} if release.kind == 'source' else set())
    assert identity.keys() == expected
    assert identity['schema_version'] == 1 and identity['version'] == '9.9.9' and identity['kind'] == release.kind
    assert identity['working_tree'] == 'clean' and identity['source_revision'] == REVISION
    assert re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', identity['built_at'])
    assert identity['lock_sha256'] == hashlib.sha256(SOURCES['requirements-portable.lock']).hexdigest()
    assert identity['app_build_sha256'] == hashlib.sha256(BUILD[APP + 'BUILD_INVENTORY.txt']).hexdigest()
    assert identity['source_fingerprint'] == build_info.source_fingerprint(release.install)    # the staged tree, the server's own function
    if release.kind == 'portable-windows-x64':
        import build_portable
        assert identity['python'] == dict(version=build_portable.PYTHON, sha256=build_portable.SHA256)
    with zipfile.ZipFile(release.archive) as archive:
        listed = archive.read(prefix_of(release.kind) + '/SHA256SUMS.txt').decode().splitlines()
    assert [line for line in listed if line.endswith('  release_identity.json')] == [
        hashlib.sha256((release.install / 'release_identity.json').read_bytes()).hexdigest() + '  release_identity.json']
    read = build_info.release_identity(release.install)
    assert {field: read[field] for field in identity} == identity
    assert (read['kind'], read['matches_source'], read['matches_app_build'], read['error']) == (release.kind, True, True, None)


def test_the_archive_gate_accepts_what_the_builders_wrote(release: SimpleNamespace,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(REPO / 'tools'))
    monkeypatch.delitem(sys.modules, 'check_release_zips', raising=False)
    import check_release_zips
    portable = release.kind == 'portable-windows-x64'
    assert check_release_zips.main(['--archive', str(release.archive), '--kind', 'portable' if portable else 'source']) == 0


def test_the_writer_refuses_a_field_the_server_does_not_read(tools: tuple[ModuleType, ModuleType], portable_checkout: Path,
                                                            tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_info, 'IDENTITY_FIELDS', tuple(f for f in build_info.IDENTITY_FIELDS if f != 'lock_sha256'))
    with pytest.raises(RuntimeError, match='lock_sha256'):
        tools[0].build(portable_checkout, tmp_path / 'dist')
    assert not (tmp_path / 'dist').exists()


def test_the_tools_and_the_server_agree_on_the_shared_names(tools: tuple[ModuleType, ModuleType]) -> None:
    build_release = tools[0]
    assert build_release.IDENTITY == build_info.RELEASE_IDENTITY
    assert build_release.APP_BUILD.split('/') == list(build_info.APP_BUILD)
    assert build_release.APP_INVENTORY == build_info.APP_INVENTORY


# ------------------------------------------------------------------- the reader's answers
def test_a_checkout_reports_kind_checkout(portable_checkout: Path) -> None:
    result = build_info.release_identity(portable_checkout)
    assert result['kind'] == 'checkout'
    assert all(value is None for key, value in result.items() if key != 'kind')


def test_edited_install_reads_matches_source_false(release: SimpleNamespace) -> None:
    dashboard = release.install / 'backend' / 'dashboard.py'
    assert build_info.release_identity(release.install)['matches_source'] is True
    dashboard.write_bytes(dashboard.read_bytes() + b'# edited after install\n')
    edited = build_info.release_identity(release.install)
    assert (edited['matches_source'], edited['matches_app_build']) == (False, True)
    assert edited['source_fingerprint'] == release.identity['source_fingerprint']     # the record is what the build said
    dashboard.write_bytes(dashboard.read_bytes().removesuffix(b'# edited after install\n'))
    assert build_info.release_identity(release.install)['matches_source'] is True
    (release.install / 'backend' / 'added.py').write_text('x = 1', encoding='utf-8')
    assert build_info.release_identity(release.install)['matches_source'] is False     # an added source file


def test_an_edited_app_file_reads_matches_app_build_false(release: SimpleNamespace) -> None:
    app = release.install.joinpath(*build_info.APP_BUILD)
    bundle = app / 'assets' / 'index-abc123.js'
    inventory = (app / build_info.APP_INVENTORY).read_bytes()
    bundle.write_bytes(b"console.log('edited')")
    edited = build_info.release_identity(release.install)
    # The inventory and the identity are untouched and still agree with each other: only the bytes of the file differ.
    assert hashlib.sha256(inventory).hexdigest() == release.identity['app_build_sha256']
    assert (app / build_info.APP_INVENTORY).read_bytes() == inventory
    assert (edited['matches_app_build'], edited['matches_source']) == (False, True)
    bundle.write_bytes(BUILD[APP + 'assets/index-abc123.js'])
    assert build_info.release_identity(release.install)['matches_app_build'] is True
    (app / 'assets' / 'unlisted.js').write_bytes(b'x')
    assert build_info.release_identity(release.install)['matches_app_build'] is False
    (app / 'assets' / 'unlisted.js').unlink()
    bundle.unlink()
    assert build_info.release_identity(release.install)['matches_app_build'] is False


MUTATIONS = ['none', 'edit', 'delete', 'extra', 'malformed-line', 'duplicate-line', 'lists-itself', 'empty', 'comment-only']


@pytest.mark.parametrize('mutation', MUTATIONS)
def test_the_runtime_check_and_the_build_check_judge_an_inventory_alike(
        tools: tuple[ModuleType, ModuleType], tmp_path: Path, mutation: str) -> None:
    """The inventory rules exist once in the build (build_release) and once on the install (build_info), because
    neither side may import the other. This keeps them from drifting: the same app folder passes both or fails both."""
    files = {name.removeprefix(APP): data for name, data in BUILD.items()}
    inventory = files['BUILD_INVENTORY.txt'].decode().splitlines()
    first = next(line for line in inventory if line.endswith('  index.html'))
    changed = {
        'edit': lambda: files.__setitem__('assets/index-abc123.js', b'edited'),
        'delete': lambda: files.pop('assets/index-abc123.css'),
        'extra': lambda: files.__setitem__('assets/unlisted.js', b'x'),
        'malformed-line': lambda: inventory.append('not an inventory line'),
        'duplicate-line': lambda: inventory.append(first),
        'lists-itself': lambda: inventory.append('0' * 64 + '  BUILD_INVENTORY.txt'),
        'empty': lambda: inventory.clear(),
        'comment-only': lambda: (inventory.clear(), inventory.append('# nothing listed')),
        'none': lambda: None,
    }[mutation]
    changed()
    files['BUILD_INVENTORY.txt'] = ('\n'.join(inventory) + '\n').encode()
    app = tmp_path / 'app'
    for name, data in files.items():
        (app / name).parent.mkdir(parents=True, exist_ok=True)
        (app / name).write_bytes(data)
    record = hashlib.sha256(files['BUILD_INVENTORY.txt']).hexdigest()
    by_the_build = tools[0]._inventory_problems(files) == []
    assert build_info._matches_app_build(app, record) is by_the_build is (mutation == 'none')


# ------------------------------------------------------------------ a dirty tree is refused
def test_dirty_tree_is_refused_by_both_builders(tools: tuple[ModuleType, ModuleType], portable_checkout: Path,
                                               tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    build_release, build_portable = tools
    build_release.FAKE_TREE = 'dirty'
    with pytest.raises(RuntimeError, match='working tree is dirty'):
        build_release.build(portable_checkout, tmp_path / 'dist')
    assert not (tmp_path / 'dist').exists()
    if PORTABLE_HOST:
        monkeypatch.setattr(build_portable, 'ROOT', portable_checkout)
        monkeypatch.setattr(build_portable.urllib.request, 'urlretrieve',
                            lambda *args, **kwargs: pytest.fail('portable build downloaded Python for a dirty tree'))
        with pytest.raises(RuntimeError, match='working tree is dirty'):
            build_portable.build()
        assert not (portable_checkout / 'dist').exists()


@pytest.mark.parametrize('porcelain, expected', [
    (b'', 'clean'), (b'\n', 'clean'),
    (b'?? notes.txt\n', 'dirty'), (b' D README.md\n', 'dirty'), (b'M  README.md\n', 'dirty'), (b' M README.md\n?? a/b.txt\n', 'dirty')])
def test_working_tree_reads_the_porcelain_answer(tools: tuple[ModuleType, ModuleType], tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch, porcelain: bytes, expected: str) -> None:
    """Any line `git status --porcelain --untracked-files=all` prints is a difference (an untracked file counts:
    release_files() would ship it); nothing printed is clean. The answers are canned: git is not run."""
    build_release = tools[0]
    (tmp_path / '.git').mkdir()       # an empty folder: only its presence matters before git is asked
    asked = []
    monkeypatch.setattr(build_release, 'git_status', lambda root: asked.append(root) or porcelain)
    assert build_release.REAL_WORKING_TREE(tmp_path) == expected and asked == [tmp_path]


def test_the_git_status_call_asks_for_untracked_files_and_cannot_hang(tools: tuple[ModuleType, ModuleType], tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(tools[0].subprocess, 'run', lambda args, **kwargs: calls.append((args, kwargs)) or SimpleNamespace(stdout=b''))
    assert tools[0].git_status(tmp_path) == b''
    ((args, kwargs),) = calls
    assert args[0] == 'git' and 'status' in args and '--porcelain' in args and '--untracked-files=all' in args
    assert kwargs['cwd'] == tmp_path and kwargs['check'] is True and kwargs['timeout'] > 0


@pytest.mark.parametrize('failure', [OSError('git is not installed'), subprocess.CalledProcessError(128, 'git'),
                                     subprocess.TimeoutExpired('git', 120)])
def test_a_tree_git_cannot_describe_is_unknown_and_refused(tools: tuple[ModuleType, ModuleType], tmp_path: Path,
                                                          monkeypatch: pytest.MonkeyPatch, failure: Exception) -> None:
    build_release = tools[0]
    (tmp_path / '.git').mkdir()

    def broken(root: Path) -> bytes:
        raise failure

    monkeypatch.setattr(build_release, 'git_status', broken)
    assert build_release.REAL_WORKING_TREE(tmp_path) == 'unknown'
    monkeypatch.setattr(build_release, 'working_tree', build_release.REAL_WORKING_TREE)
    with pytest.raises(RuntimeError, match='working tree is unknown'):
        build_release.require_clean_tree(tmp_path)
    assert build_release.require_clean_tree(tmp_path, allow_dirty=True) == 'unknown'


def test_a_folder_without_its_own_git_is_unknown_and_git_is_not_asked(tools: tuple[ModuleType, ModuleType], tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """An unpacked archive, or a folder inside someone else's repository, is not a checkout of its own."""
    build_release = tools[0]
    monkeypatch.setattr(build_release, 'git_status', lambda root: pytest.fail('git was asked about a folder with no .git'))
    assert build_release.REAL_WORKING_TREE(tmp_path) == 'unknown'
    nested = tmp_path / 'checkout' / 'nested'
    nested.mkdir(parents=True)
    (tmp_path / 'checkout' / '.git').mkdir()
    assert build_release.REAL_WORKING_TREE(nested) == 'unknown'


def test_require_clean_tree_returns_the_state_to_record(tools: tuple[ModuleType, ModuleType], tmp_path: Path) -> None:
    build_release = tools[0]
    assert build_release.require_clean_tree(tmp_path) == 'clean'
    build_release.FAKE_TREE = 'dirty'
    with pytest.raises(RuntimeError, match='--allow-dirty'):
        build_release.require_clean_tree(tmp_path)
    assert build_release.require_clean_tree(tmp_path, allow_dirty=True) == 'dirty'


def test_the_ignore_rules_cover_what_a_ci_job_leaves_behind() -> None:
    """What release.yml leaves before the builds (dependency installs, test and build output, caches, dist/) must stay
    ignored by the repository's .gitignore, or the clean-tree rule would refuse CI. This runs the file through the
    gitignore emulation of support/fake_git_tree.py, a proxy: the emulation is not git."""
    rules = fake_git_tree._rules(REPO)
    leftovers = ['frontend/app/node_modules/react/index.js', 'frontend/app/test-results/trace.txt', 'frontend/app/coverage/index.html',
                 'frontend/app/playwright-report/index.html', 'frontend/app/tsconfig.tsbuildinfo', 'frontend/webui/app/assets/new.js',
                 '.pytest_cache/README.md', 'backend/__pycache__/x.pyc', 'backend/tests/__pycache__/y.pyc', 'dist/TowerPilot-9.9.9-source.zip',
                 'dist/portable-build-abc/runtime/python.exe', 'dist/downloads/python-embed.zip']
    assert [name for name in leftovers if not fake_git_tree.is_ignored(rules, name)] == []
    assert not fake_git_tree.is_ignored(rules, 'backend/dashboard.py') and not fake_git_tree.is_ignored(rules, 'tools/build_release.py')


def test_a_rehearsal_build_records_dirty_and_the_gate_refuses_it(
        tools: tuple[ModuleType, ModuleType], portable_checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    tools[0].FAKE_TREE = 'dirty'
    monkeypatch.syspath_prepend(str(REPO / 'tools'))
    monkeypatch.delitem(sys.modules, 'check_release_zips', raising=False)
    import check_release_zips
    cases = [(tools[0].build(portable_checkout, tmp_path / 'dist', allow_dirty=True), 'source')]
    if PORTABLE_HOST:
        cases.append((build_portable_zip(tools, portable_checkout, monkeypatch, allow_dirty=True), 'portable'))
    for archive, kind in cases:
        with zipfile.ZipFile(archive) as zipped:
            (name,) = [n for n in zipped.namelist() if n.endswith('/release_identity.json')]
            assert json.loads(zipped.read(name))['working_tree'] == 'dirty'
        capsys.readouterr()
        assert check_release_zips.main(['--archive', str(archive), '--kind', kind]) == 1
        assert 'release identity mismatch: working_tree' in capsys.readouterr().out


@pytest.mark.parametrize('tool', ['build_release', 'build_portable'])
def test_the_command_line_refuses_a_tree_that_is_not_a_clean_checkout(tools: tuple[ModuleType, ModuleType], portable_checkout: Path,
                                                                      tool: str) -> None:
    """The scripts as CI runs them (`python tools/<name>.py`), from a folder that carries its own copy of them. This
    folder has no .git, so the tree is unknown and the script stops before it reads, copies, downloads or zips anything."""
    if tool == 'build_portable' and not PORTABLE_HOST:
        pytest.skip('the portable build needs 64-bit Python 3.12 on Windows')
    shutil.rmtree(portable_checkout / '.git')       # the revision file is for the builders above; here the folder is an unpacked archive
    for name in ('tools/build_release.py', 'tools/build_portable.py', 'tools/release_archive_policy.py'):
        _write(portable_checkout, {name: (REPO / name).read_bytes()})
    environment_ = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    environment_.pop('PYTHONPATH', None)
    refused = subprocess.run([sys.executable, f'tools/{tool}.py'], cwd=portable_checkout, env=environment_,
                             capture_output=True, text=True, timeout=120)
    assert refused.returncode != 0 and 'working tree is unknown' in refused.stderr and '--allow-dirty' in refused.stderr
    assert not (portable_checkout / 'dist').exists()


@pytest.mark.parametrize('module', ['build_release', 'build_portable'])
def test_allow_dirty_reaches_the_build_only_from_the_flag(tools: tuple[ModuleType, ModuleType], module: str,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    tool = tools[0] if module == 'build_release' else tools[1]
    seen = []
    monkeypatch.setattr(tool, 'build', lambda **kwargs: seen.append(kwargs))
    tool.main([])
    tool.main(['--allow-dirty'])
    assert seen == [dict(allow_dirty=False), dict(allow_dirty=True)]
    with pytest.raises(SystemExit):
        tool.main(['--allow-everything'])


# --------------------------------------------------------------------------- no path, user or machine
# What the identity may legitimately say. A folder, user or host that happens to be named one of these ("source",
# "clean", "version", "tree" inside the key working_tree) must not fail a leak check: it is not evidence of a leak.
VOCABULARY = {*build_info.IDENTITY_FIELDS, 'clean', 'source', 'portable-windows-x64', 'version', 'sha256', 'python'}
SHAPES = (r'1', r'9\.9\.9', r'source|portable-windows-x64', r'[0-9a-f]{64}', r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', r'[0-9a-f]{40}',
          r'clean', r'3\.12\.10')


def identity_values(identity: dict) -> list[str]:
    """Every value of the identity, nested ones included (never a key)."""
    return [str(value) for key, value in identity.items() if not isinstance(value, dict)] + [
        str(value) for value in identity.get('python', {}).values()]


def machine_names(tmp_path: Path, checkout: Path) -> set[str]:
    names = {str(tmp_path), tmp_path.name, tmp_path.parent.name, str(checkout), checkout.name, str(Path.cwd()),
             Path.cwd().name, getpass.getuser(), socket.gethostname(), platform.node(), str(Path.home()), Path.home().name,
             *(os.environ.get(variable, '') for variable in ('USERNAME', 'USER', 'USERPROFILE', 'COMPUTERNAME', 'HOSTNAME', 'TEMP'))}
    return names - {'', '.', 'tools'}


def assert_names_nothing_of_the_machine(identity: dict, text: str, names: set[str]) -> None:
    """The identity text holds no path, and none of `names` appears in any VALUE.

    Keys are never searched (a folder called "tree" is not a leak because of the key working_tree), a name that is itself
    part of the identity vocabulary or only hex digits is skipped here (it cannot be told from a legitimate value or a
    hash; the sentinel test below covers distinctive names), and every value must still have one of the allowed shapes."""
    assert not re.search(r'[A-Za-z]:[\\/]|\\\\|/Users/|/home/|/tmp/|@', text)
    values = identity_values(identity)
    assert all(re.fullmatch('|'.join(SHAPES), value) for value in values), values
    haystack = '\n'.join(values).casefold()
    for name in names:
        folded = name.casefold()
        if len(folded) > 3 and folded not in VOCABULARY and not re.fullmatch(r'[0-9a-f]+', folded):
            assert folded not in haystack, 'the identity names this machine, user or folder'


def test_no_path_or_user_in_the_identity(release: SimpleNamespace, tmp_path: Path) -> None:
    text = (release.install / 'release_identity.json').read_text(encoding='utf-8')
    assert_names_nothing_of_the_machine(release.identity, text, machine_names(tmp_path, release.checkout))


@pytest.mark.parametrize('folder', ['tree', 'source', 'version', 'clean', 'working_tree', 'python'])
def test_a_checkout_folder_named_like_an_identity_word_is_not_a_leak(
        tools: tuple[ModuleType, ModuleType], portable_checkout: Path, tmp_path: Path, folder: str) -> None:
    """The folder names that made the old substring check fail ("tree" matched the key working_tree) build and verify."""
    moved = tmp_path / folder / 'checkout'
    shutil.copytree(portable_checkout, moved)
    archive = tools[0].build(moved, tmp_path / folder / 'dist')
    install = extract(archive, tmp_path / 'install')
    identity = json.loads((install / 'release_identity.json').read_text(encoding='utf-8'))
    assert_names_nothing_of_the_machine(identity, (install / 'release_identity.json').read_text(encoding='utf-8'),
                                        machine_names(tmp_path, moved) | {folder})
    assert (identity['kind'], identity['working_tree']) == ('source', 'clean')
    assert build_info.release_identity(install)['matches_source'] is True


@pytest.mark.parametrize('kind', KINDS)
def test_distinctive_names_of_the_build_machine_never_reach_the_identity(
        tools: tuple[ModuleType, ModuleType], portable_checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    """Controlled sentinels in the checkout path, the output path and the working directory: none may be written anywhere in the
    identity, keys or values. (A sentinel cannot collide with a legitimate value, which is why this check searches the whole text.)"""
    if kind != 'source' and not PORTABLE_HOST:
        pytest.skip('the portable build needs 64-bit Python 3.12 on Windows')
    base = tmp_path / 'zq7sentineluser' / 'zq7sentineltree'
    moved = base / 'zq7checkout'
    shutil.copytree(portable_checkout, moved)
    elsewhere = base / 'zq7cwd'
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    archive = (tools[0].build(moved, base / 'zq7dist') if kind == 'source'
               else build_portable_zip(tools, moved, monkeypatch))
    text = (extract(archive, tmp_path / 'install') / 'release_identity.json').read_text(encoding='utf-8')
    assert 'zq7' not in text.casefold() and str(tmp_path).casefold() not in text.casefold()
    assert json.loads(text)['kind'] == kind


def test_the_fingerprint_is_taken_over_the_staged_tree_not_the_checkout(release: SimpleNamespace) -> None:
    """The checkout holds a developer helper under backend/tools that the allowlist drops. The server fingerprint counts it,
    so a fingerprint taken over the checkout would differ from the one the installed tree computes, and matches_source would
    read False on every real install."""
    helper = 'backend/tools/helper.py'
    assert (release.checkout / helper).is_file() and not (release.install / helper).exists()
    assert build_info.source_fingerprint(release.checkout) != release.identity['source_fingerprint']
    assert release.identity['source_fingerprint'] == build_info.source_fingerprint(release.install)
    assert build_info.release_identity(release.install)['matches_source'] is True


# -------------------------------------------------- one reader, used by the environment report and the route
def test_the_environment_reads_the_kind_from_the_one_reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def reader(root: Path, **options: bool) -> dict:
        calls.append((root, options))
        return dict(kind='portable-windows-x64')

    monkeypatch.setattr(build_info, 'release_identity', reader)
    (tmp_path / 'release_identity.json').write_text('{"kind": "source"}', encoding='utf-8')     # a second reader would say "source"
    assert environment.report(tmp_path)['release'] == dict(kind='portable-windows-x64')
    assert calls == [(tmp_path, dict(verify=False))]                                          # the kind only: no hashing per poll
    assert 'release_identity.json' not in Path(environment.__file__).read_text(encoding='utf-8')


@pytest.mark.parametrize('record', [None, '{"kind": "source"}', '{"kind": "portable-windows-x64"}', '{broken', '[]', '{"kind": "other"}'])
def test_the_environment_answers_for_a_record_are_unchanged(tmp_path: Path, record: str | None) -> None:
    expected = {None: 'checkout', '{"kind": "source"}': 'source', '{"kind": "portable-windows-x64"}': 'portable-windows-x64'}.get(record, 'unknown')
    if record is not None:
        (tmp_path / 'release_identity.json').write_text(record, encoding='utf-8')
    assert environment.report(tmp_path)['release'] == dict(kind=expected)


def test_runtime_route_carries_release_beside_server(local_dashboard: ModuleType) -> None:
    dash = local_dashboard
    root = Path(dash.ROOT).parent
    client = dash.app.test_client()
    body = client.get('/api/runtime').get_json()
    assert body['release'] == build_info.release_identity(root) and body['release']['kind'] == 'checkout'
    assert {'server', 'environment', 'capabilities', 'workers'} <= body.keys()
    (root / 'release_identity.json').write_text(json.dumps(dict(kind='source', version='9.9.9')), encoding='utf-8')
    body = client.get('/api/runtime').get_json()
    assert body['release'] == build_info.release_identity(root)
    assert (body['release']['kind'], body['release']['version'], body['release']['matches_source'],
            body['release']['matches_app_build']) == ('source', '9.9.9', False, False)
    assert body['environment']['release'] == dict(kind='source')


# ------------------------------------------------------------------------- what the source ZIP may carry
def test_the_allowlist_ships_the_release_tools_and_the_0_6_note(tools: tuple[ModuleType, ModuleType]) -> None:
    build_release = tools[0]
    for name in ('tools/build_release.py', 'tools/build_portable.py',
                 'tools/check_release_zips.py', 'tools/release_archive_policy.py', 'docs/RELEASE_0.6_BETA.md', 'docs/RELEASE_0.5_BETA.md'):
        assert build_release.allowed(name), name
    assert not build_release.allowed('tools/another_new_tool.py') and not build_release.allowed('docs/RELEASE_0.7_BETA.md')


def test_the_release_tools_import_no_application_code_until_an_identity_is_written(tmp_path: Path) -> None:
    """The archive checker imports build_release and must stay free of application code:
    the reader (runtime.build_info) is loaded by name only when an identity is written, never at import. Behavior, in a
    clean interpreter: importing the tools loads no `runtime`, `settings` or `dashboard` module; asking for the reader does."""
    code = ('import sys; sys.path.insert(0, sys.argv[1]); import build_release, check_release_zips, release_archive_policy, build_portable; '
            'bad = sorted(m for m in sys.modules if m.split(".")[0] in {"runtime", "settings", "dashboard", "flask", "yaml"}); print(bad); '
            'sys.path.insert(0, sys.argv[2]); info = build_release._build_info(); print(info.__name__, "runtime.build_info" in sys.modules)')
    done = subprocess.run([sys.executable, '-E', '-s', '-c', code, str(REPO / 'tools'), str(REPO / 'backend')],
                          cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines() == ['[]', 'runtime.build_info True'], done.stdout
