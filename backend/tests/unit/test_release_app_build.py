"""The dashboard app build (frontend/webui/app/, git-ignored) ships inside both
release ZIPs, added from the filesystem, and a release is refused without it.
The app's sources ship too (ADR-001 decision 2); npm and test state never do.

Each test builds the real ZIP writers' output from a small temporary folder.
The two git calls of the release tools (the file listing and the working-tree
state, build_release.git_listing and working_tree) are replaced by
support/fake_git_tree.py, which runs no git, and the tree reads clean
(release_identity.json records that, and the archive gate requires it). The
portable test runs the same staging, identity and ZIP code build() uses; the
embedded Python download and pip install are not exercised here
(test_release_identity.py runs build() with those two steps stubbed)."""

import paths as test_paths
import hashlib
import importlib
import json
import shutil
import subprocess
import sys
import zipfile
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType

import pytest

import fake_git_tree

REPO = test_paths.REPO
BUILD = {
    "frontend/webui/app/index.html": (
        b'<!doctype html><div id=root></div>'
        b'<script type="module" crossorigin src="/ui/app/assets/index-abc123.js"></script>'
        b'<link rel="stylesheet" crossorigin href="/ui/app/assets/index-abc123.css">'),
    "frontend/webui/app/assets/index-abc123.js": b"console.log('app')",
    "frontend/webui/app/assets/index-abc123.css": b"body{color:#e5e7eb}",
    "frontend/webui/app/THIRD_PARTY_NOTICES.txt": b"- react 19.3.1 (MIT)\n",
}


def _inventory(files: Mapping[str, bytes]) -> bytes:
    """BUILD_INVENTORY.txt as frontend/app/scripts/build-inventory.ts writes it."""
    lines = [hashlib.sha256(data).hexdigest() + '  ' + name
             for name, data in sorted(files.items()) if name != 'BUILD_INVENTORY.txt']
    return ('# inventory\n' + '\n'.join(lines) + '\n').encode()


BUILD["frontend/webui/app/BUILD_INVENTORY.txt"] = _inventory(
    {name.removeprefix("frontend/webui/app/"): data for name, data in BUILD.items()})
SOURCES = {
    "VERSION": b"9.9.9\n",
    "README.md": b"# test\n",
    "requirements-portable.lock": b"flask==0.0.0\n",
    "docs/RELEASE_9.9.9.md": b"# Release\n\n## Environment limits\nSynthetic test only.\n",
    "backend/dashboard.py": b"# dashboard\n",
    # a source file the release allowlist drops (allowed() excludes backend/tools) but the server fingerprint counts:
    # the identity must be taken over the staged tree, not over this checkout (test_release_identity.py)
    "backend/tools/helper.py": b"# a developer helper that is not shipped\n",
    "frontend/webui/index.html": (b"<script>window.location.replace('/ui/app/')</script>"
                                  b'<a href="/ui/app/">open the dashboard</a>'),
    "frontend/app/package.json": b"{}\n",
    "frontend/app/package-lock.json": b"{}\n",
    "frontend/app/.npmrc": b"save-exact=true\n",
    "frontend/app/.prettierignore": b"node_modules/\n",
    "frontend/app/tsconfig.json": b"{}\n",
    "frontend/app/index.html": b"<div id=root></div>",
    "frontend/app/eslint.config.js": b"export default [];\n",
    "frontend/app/src/app/main.tsx": b"export {};\n",
    "frontend/app/src/core/facts.ts": b"export {};\n",
    "frontend/app/src/ui/tokens.css": b":root{}\n",
    "frontend/app/tests/support/fake-backend/fake-backend.mjs": b"export {};\n",
}
NEVER = {
    "frontend/app/node_modules/react/index.js": b"module.exports = {}",
    "frontend/app/node_modules/react/LICENSE": b"MIT",
    "frontend/app/test-results/smoke/trace.txt": b"trace",
    "frontend/app/playwright-report/index.html": b"<p>report</p>",
}
# The previous dashboard (retired by plan R17): pages, modules and node tests
# left in a checkout never reach a release; only the redirect page does.
RETIRED = {
    "frontend/webui/classic.html": b"<p>classic</p>",
    "frontend/webui/run-workspace.js": b"export {};",
    "frontend/webui/settings.css": b"body{}",
    "frontend/webui/vendor/pico.min.css": b"body{}",
    "frontend/tests/helpers.cjs": b"module.exports = {};",
    "frontend/tests/dashboard.test.cjs": b"require('node:test');",
}
NEVER.update(RETIRED)


def _write(root: Path, files: Mapping[str, bytes]) -> None:
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


@pytest.fixture()
def tools(monkeypatch: pytest.MonkeyPatch) -> tuple[ModuleType, ModuleType]:
    monkeypatch.syspath_prepend(str(REPO / "tools"))
    for name in ("build_release", "build_portable"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    release = importlib.import_module("build_release")
    release.DOCS.add('RELEASE_9.9.9.md')
    fake_git_tree.install(monkeypatch, release)
    return release, importlib.import_module("build_portable")


@pytest.fixture()
def checkout(tmp_path: Path) -> Path:
    """A checkout-shaped folder with the real .gitignore, app sources, npm/test state and a build. No git."""
    root = tmp_path / "checkout"
    root.mkdir()
    shutil.copyfile(REPO / ".gitignore", root / ".gitignore")
    _write(root, {**SOURCES, **NEVER, **BUILD})
    return root


def _entries(archive_path: Path, prefix: str) -> dict[str, bytes]:
    with zipfile.ZipFile(archive_path) as archive:
        return {name[len(prefix) + 1:]: archive.read(name) for name in archive.namelist()}


def _assert_release_contents(entries: Mapping[str, bytes]) -> None:
    for name, data in BUILD.items():
        assert entries.get(name) == data, name
    for name in ("frontend/app/package.json", "frontend/app/package-lock.json", "frontend/app/.npmrc",
                 "frontend/app/src/app/main.tsx", "frontend/app/tests/support/fake-backend/fake-backend.mjs",
                 "frontend/webui/index.html", "backend/dashboard.py"):
        assert name in entries, name
    assert "frontend/dashboard.py" not in entries
    for name in NEVER:
        assert name not in entries, name
    assert entries["frontend/webui/index.html"] == SOURCES["frontend/webui/index.html"]
    assert not any(name.startswith("frontend/tests/") for name in entries)
    inventory = entries["SHA256SUMS.txt"].decode()
    for name in BUILD:
        assert inventory.count("  " + name + "\n") == 1, name


def test_the_source_zip_carries_the_app_build_and_sources(
        tools: tuple[ModuleType, ModuleType], checkout: Path, tmp_path: Path) -> None:
    build_release, _ = tools
    output = build_release.build(root=checkout, dist=tmp_path / "dist")
    assert output.name == "TowerPilot-9.9.9-source.zip"
    _assert_release_contents(_entries(output, "TowerPilot-9.9.9"))


def test_the_portable_zip_carries_the_app_build_and_sources(
        tools: tuple[ModuleType, ModuleType], checkout: Path, tmp_path: Path) -> None:
    _, build_portable = tools
    prefix = "TowerPilot-9.9.9-windows-x64"
    stage = tmp_path / "stage" / prefix
    stage.mkdir(parents=True)
    build_portable.stage_tree(stage, checkout)
    output = build_portable.write_zip(stage, tmp_path / (prefix + ".zip"), prefix)
    _assert_release_contents(_entries(output, prefix))


@pytest.mark.parametrize("missing", ["frontend/webui/app/index.html",
                                     "frontend/webui/app/THIRD_PARTY_NOTICES.txt"])
def test_a_release_without_the_app_build_is_refused(
        tools: tuple[ModuleType, ModuleType], checkout: Path, tmp_path: Path, missing: str) -> None:
    build_release, build_portable = tools
    (checkout / missing).unlink()
    with pytest.raises(RuntimeError, match="npm ci; npm run build"):
        build_release.build(root=checkout, dist=tmp_path / "dist")
    with pytest.raises(RuntimeError, match="is not built"):
        build_portable.stage_tree(tmp_path / "stage", checkout)
    assert not (tmp_path / "dist" / "TowerPilot-9.9.9-source.zip").exists()


@pytest.mark.parametrize("extra", ["frontend/webui/app/assets/index-abc123.js.map",
                                   "frontend/webui/app/assets/logo.svg",
                                   "frontend/webui/app/assets/font.woff2",
                                   "frontend/webui/app/notes.txt"])
def test_the_app_build_may_hold_only_html_js_css_and_the_notices(
        tools: tuple[ModuleType, ModuleType], checkout: Path, tmp_path: Path, extra: str) -> None:
    build_release, _ = tools
    _write(checkout, {extra: b"x"})
    with pytest.raises(RuntimeError, match="may not ship"):
        build_release.build(root=checkout, dist=tmp_path / "dist")


def test_allowed_admits_app_sources_and_leaves_the_build_to_the_filesystem(
        tools: tuple[ModuleType, ModuleType]) -> None:
    build_release, _ = tools
    for name in ("frontend/app/package.json", "frontend/app/src/app/App.tsx", "frontend/app/src/core/facts.ts",
                 "frontend/app/vite.config.ts", "frontend/app/.npmrc", "frontend/app/README.md",
                 "frontend/app/tests/support/fake-backend/fake-backend.mjs"):
        assert build_release.allowed(name), name
    for name in ("frontend/app/node_modules/react/index.js", "frontend/app/test-results/x.txt",
                 "frontend/app/playwright-report/index.html", "frontend/app/coverage/index.html",
                 "frontend/webui/app/index.html", "frontend/app", *RETIRED,
                 "frontend/webui/dashboard-view.js", "frontend/webui/new-page.html", "frontend/other.py", "frontend/dashboard.py"):
        assert not build_release.allowed(name), name
    assert build_release.allowed("frontend/webui/index.html") and build_release.allowed("backend/dashboard.py")


def test_this_checkout_ships_the_redirect_and_no_retired_dashboard(tools: tuple[ModuleType, ModuleType]) -> None:
    """R17: the release carries the old-bookmark redirect and the tracked policy
    seeds that replaced the legacy page's ui-seeds block, and nothing else of
    the previous dashboard."""
    build_release, _ = tools
    if shutil.which("git") is None or not (REPO / ".git").exists():
        pytest.skip("needs git and a git checkout (a source archive or scratch copy has neither)")
    listed = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                            cwd=REPO, capture_output=True, text=True, check=True).stdout.splitlines()
    names = {name for name in listed if build_release.allowed(name) and (REPO / name).is_file()}
    assert {"frontend/webui/index.html", "backend/dashboard.py",
            "backend/tests/fixtures/ui_policy_seeds.json"} <= names
    outside_app = {name for name in names if name.startswith("frontend/")
                   and not name.startswith(("frontend/app/", "frontend/webui/app/"))}
    assert outside_app == {"frontend/webui/index.html"}


def test_every_app_source_in_this_checkout_ships(tools: tuple[ModuleType, ModuleType]) -> None:
    """A new kind of source file under frontend/app/ must not be dropped from releases silently."""
    build_release, _ = tools
    if shutil.which("git") is None or not (REPO / ".git").exists():
        pytest.skip("needs git and a git checkout (a source archive or scratch copy has neither)")
    names = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "frontend/app"],
                           cwd=REPO, capture_output=True, text=True, check=True).stdout.splitlines()
    assert "frontend/app/package.json" in names
    assert [name for name in names if not build_release.allowed(name)] == []


def test_deleted_referenced_assets_refuse_both_packaging_paths(
        tools: tuple[ModuleType, ModuleType], checkout: Path, tmp_path: Path) -> None:
    build_release, build_portable = tools
    missing = ('assets/index-abc123.js', 'assets/index-abc123.css')
    for name in missing:
        (checkout / 'frontend/webui/app' / name).unlink()
    for package in (lambda: build_release.build(checkout, tmp_path / 'dist'),
                    lambda: build_portable.stage_tree(tmp_path / 'stage', checkout)):
        with pytest.raises(RuntimeError) as failure:
            package()
        for name in missing:
            assert name in str(failure.value)
        assert 'cd frontend/app; npm ci; npm run build' in str(failure.value)
    assert not (tmp_path / 'dist').exists()
    assert not (tmp_path / 'stage').exists()


def test_portable_build_refuses_missing_assets_before_downloading(
        tools: tuple[ModuleType, ModuleType], checkout: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, build_portable = tools
    monkeypatch.setattr(build_portable, 'ROOT', checkout)

    def unexpected_download(*args: object, **kwargs: object) -> None:
        pytest.fail('portable build downloaded before validating the app')

    monkeypatch.setattr(build_portable.urllib.request, 'urlretrieve', unexpected_download)
    (checkout / 'frontend/webui/app/assets/index-abc123.js').unlink()
    with pytest.raises(RuntimeError, match='missing referenced app file: assets/index-abc123.js'):
        build_portable.build()
    assert not (checkout / 'dist').exists()


@pytest.mark.parametrize(('change', 'expected'), [
    ('index', 'missing index.html'),
    ('ref', 'missing referenced app file: assets/missing.css'),
    ('traversal', 'invalid app reference (path traversal)'),
    ('encoded-traversal', 'invalid app reference (path traversal)'),
    ('module', 'no <script type="module">'),
    ('notices', 'empty THIRD_PARTY_NOTICES.txt'),
    ('map', 'sourcemap may not ship: assets/app.js.map'),
])
def test_app_build_problems(
        tools: tuple[ModuleType, ModuleType], change: str, expected: str) -> None:
    build_release, _ = tools
    files = {name.removeprefix('frontend/webui/app/'): data for name, data in BUILD.items()}
    if change == 'index':
        del files['index.html']
    elif change == 'ref':
        files['index.html'] += b'<link href="/ui/app/assets/missing.css">'
    elif change in {'traversal', 'encoded-traversal'}:
        traversal = '..' if change == 'traversal' else '%2e%2e'
        files['index.html'] += f'<link href="/ui/app/{traversal}/outside.css">'.encode()
    elif change == 'module':
        files['index.html'] = b'<script src="/ui/app/assets/index-abc123.js"></script>'
    elif change == 'notices':
        files['THIRD_PARTY_NOTICES.txt'] = b' \n'
    elif change == 'map':
        files['assets/app.js.map'] = b'{}'
    assert any(expected in problem for problem in build_release.app_build_problems(files))


def test_query_and_fragment_references_resolve_to_build_files(
        tools: tuple[ModuleType, ModuleType]) -> None:
    build_release, _ = tools
    files = {name.removeprefix('frontend/webui/app/'): data for name, data in BUILD.items()}
    files['index.html'] = files['index.html'].replace(b'.js"', b'.js?v=1&amp;mode=prod#start"')
    files['index.html'] = files['index.html'].replace(b'.css"', b'.css#theme')
    files['BUILD_INVENTORY.txt'] = _inventory(files)
    assert build_release.app_build_problems(files) == []


@pytest.fixture()
def release_checker(tools: tuple[ModuleType, ModuleType], monkeypatch: pytest.MonkeyPatch,
                    checkout: Path) -> ModuleType:
    monkeypatch.delitem(sys.modules, 'check_release_zips', raising=False)
    checker = importlib.import_module('check_release_zips')
    monkeypatch.setattr(checker, 'ROOT', checkout)
    return checker


def _sidecar(path: Path) -> None:
    path.with_suffix('.zip.sha256').write_text(
        hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name + '\n')


@pytest.fixture()
def release_pair(tools: tuple[ModuleType, ModuleType], checkout: Path,
                 tmp_path: Path) -> tuple[Path, Path, Path]:
    build_release, build_portable = tools
    dist = tmp_path / 'dist'
    source = build_release.build(checkout, dist)
    stage = tmp_path / 'stage'
    build_portable.stage_tree(stage, checkout)
    build_release.write_identity(stage, checkout, 'portable-windows-x64', build_release.working_tree(checkout),
                                 python=dict(version=build_portable.PYTHON, sha256=build_portable.SHA256))
    prefix = 'TowerPilot-9.9.9-windows-x64'
    portable = build_portable.write_zip(stage, dist / (prefix + '.zip'), prefix)
    _sidecar(portable)
    return dist, source, portable


def test_check_release_zips_accepts_a_good_pair(
        release_checker: ModuleType, release_pair: tuple[Path, Path, Path],
        capsys: pytest.CaptureFixture[str]) -> None:
    dist, source, portable = release_pair
    capsys.readouterr()
    assert release_checker.main(['--dist', str(dist)]) == 0
    output = capsys.readouterr().out
    for path in (source, portable):
        with zipfile.ZipFile(path) as archive:
            assert f'{path.name}: {len(archive.infolist())} entries' in output
    assert output.startswith('OK: ')


def _rewrite_zip(path: Path, mutation: str) -> None:
    """Rewrite only a temporary archive copy; refresh metadata unless it is under test."""
    with zipfile.ZipFile(path) as archive:
        entries = {info.filename: archive.read(info) for info in archive.infolist()}
    prefix = next(iter(entries)).split('/')[0] + '/'
    if mutation == 'missing-asset':
        del entries[prefix + 'frontend/webui/app/assets/index-abc123.js']
    elif mutation == 'tamper':
        entries[prefix + 'README.md'] = b'tampered'
    elif mutation == 'missing-source':
        del entries[prefix + 'frontend/app/src/app/main.tsx']
    elif mutation == 'node_modules':
        entries[prefix + 'frontend/app/node_modules/react/index.js'] = b'bad'
    elif mutation == 'different-build':
        entries[prefix + 'frontend/webui/app/assets/index-abc123.js'] = b'different'
    elif mutation == 'prefix':
        entries['wrong-prefix/file.txt'] = b'bad'
    elif mutation == 'map':
        entries[prefix + 'frontend/webui/legacy.js.map'] = b'{}'
    elif mutation == 'duplicate-inventory':
        entries[prefix + 'SHA256SUMS.txt'] += (
            hashlib.sha256(entries[prefix + 'README.md']).hexdigest() + '  README.md\n').encode()
    elif mutation == 'missing-inventory':
        del entries[prefix + 'SHA256SUMS.txt']
    elif mutation == 'missing-identity':
        del entries[prefix + 'release_identity.json']
    elif mutation in {'dirty-identity', 'foreign-app-build'}:
        identity = json.loads(entries[prefix + 'release_identity.json'])
        identity.update(dict(working_tree='dirty') if mutation == 'dirty-identity' else dict(app_build_sha256='0' * 64))
        entries[prefix + 'release_identity.json'] = json.dumps(identity).encode()
    if mutation not in {'tamper', 'duplicate-inventory', 'missing-inventory'}:
        entries[prefix + 'SHA256SUMS.txt'] = ''.join(
            hashlib.sha256(data).hexdigest() + '  ' + name.removeprefix(prefix) + '\n'
            for name, data in entries.items() if name != prefix + 'SHA256SUMS.txt').encode()
    replacement = path.with_suffix('.rewritten.zip')
    with zipfile.ZipFile(replacement, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    replacement.replace(path)
    _sidecar(path)


@pytest.mark.parametrize('which', [1, 2], ids=['source', 'portable'])
@pytest.mark.parametrize(('mutation', 'expected'), [
    ('missing-asset', 'missing referenced app file: assets/index-abc123.js'),
    ('wrong-hash', 'sidecar hash mismatch:'),
    ('missing-sidecar', 'missing sidecar:'),
    ('missing-archive', 'missing archive:'),
    ('tamper', 'SHA256SUMS.txt hash mismatch: README.md'),
    ('missing-source', 'missing frontend/app source: frontend/app/src/app/main.tsx'),
    ('node_modules', 'forbidden path segment:'),
    ('different-build', 'app build bytes differ from on-disk build: assets/index-abc123.js'),
    ('wrong-sidecar-name', 'sidecar names wrong.zip, expected'),
    ('prefix', 'entry outside expected prefix'),
    ('map', 'forbidden file suffix:'),
    ('duplicate-inventory', 'SHA256SUMS.txt must list README.md exactly once (found 2)'),
    ('missing-inventory', 'missing SHA256SUMS.txt'),
    ('missing-identity', 'missing or invalid release_identity.json'),
    ('dirty-identity', 'release identity mismatch: working_tree'),
    ('foreign-app-build', 'release identity mismatch: app_build_sha256'),
])
def test_check_release_zips_rejects_mutated_archives(
        release_checker: ModuleType, release_pair: tuple[Path, Path, Path],
        capsys: pytest.CaptureFixture[str], which: int, mutation: str, expected: str) -> None:
    dist = release_pair[0]
    path = release_pair[which]
    if mutation == 'wrong-hash':
        path.with_suffix('.zip.sha256').write_text('0' * 64 + '  ' + path.name + '\n')
    elif mutation == 'missing-sidecar':
        path.with_suffix('.zip.sha256').unlink()
    elif mutation == 'missing-archive':
        path.unlink()
    elif mutation == 'wrong-sidecar-name':
        path.with_suffix('.zip.sha256').write_text(
            hashlib.sha256(path.read_bytes()).hexdigest() + '  wrong.zip\n')
    else:
        _rewrite_zip(path, mutation)
    capsys.readouterr()
    assert release_checker.main(['--dist', str(dist)]) == 1
    output = capsys.readouterr().out
    assert path.name + ':' in output
    assert expected in output
    assert 'OK:' not in output


def test_check_release_zips_uses_custom_build_directory(
        release_checker: ModuleType, release_pair: tuple[Path, Path, Path],
        checkout: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    custom = tmp_path / 'custom-build'
    shutil.copytree(checkout / 'frontend/webui/app', custom)
    (custom / 'assets/index-abc123.js').write_bytes(b'new custom build')
    assert release_checker.main(['--dist', str(release_pair[0]), '--build-dir', str(custom)]) == 1
    assert 'app build bytes differ from on-disk build: assets/index-abc123.js' in capsys.readouterr().out


def test_check_release_zips_defaults_are_relative_to_repo_root(
        release_checker: ModuleType, release_pair: tuple[Path, Path, Path], checkout: Path,
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shutil.copytree(release_pair[0], checkout / 'dist')
    monkeypatch.chdir(tmp_path)
    assert release_checker.main([]) == 0


@pytest.mark.parametrize('default_state', ['missing', 'invalid'])
def test_custom_build_does_not_require_a_valid_default_build(
        release_checker: ModuleType, release_pair: tuple[Path, Path, Path], checkout: Path,
        tmp_path: Path, default_state: str) -> None:
    default = checkout / 'frontend/webui/app'
    custom = tmp_path / 'custom-build'
    shutil.copytree(default, custom)
    if default_state == 'missing':
        shutil.rmtree(default)  # The checkout fixture lives entirely under tmp_path.
    else:
        (default / 'assets/index-abc123.js').unlink()
    assert release_checker.main(['--dist', str(release_pair[0]), '--build-dir', str(custom)]) == 0

# ------------------------------------- the build inventory (compliance M02)
def _snapshot() -> dict[str, bytes]:
    return {name.removeprefix('frontend/webui/app/'): data for name, data in BUILD.items()}


def test_the_synthetic_build_is_complete(tools: tuple[ModuleType, ModuleType]) -> None:
    build_release, _ = tools
    assert build_release.app_build_problems(_snapshot()) == []


def test_a_missing_chunk_that_index_html_does_not_name_is_refused(
        tools: tuple[ModuleType, ModuleType]) -> None:
    build_release, _ = tools
    # the compliance reviewer's snapshot: the entry exists, the chunk it imports does not
    files = {
        'index.html': b'<script type="module" src="/ui/app/main.js"></script>',
        'main.js': b'import "./missing-chunk.js";',
        'THIRD_PARTY_NOTICES.txt': b'licence',
    }
    assert any('missing BUILD_INVENTORY.txt' in p for p in build_release.app_build_problems(files))
    complete = dict(files, **{'missing-chunk.js': b'export {};'})
    files['BUILD_INVENTORY.txt'] = _inventory(complete)
    assert build_release.app_build_problems(files) == [
        'missing build output listed in BUILD_INVENTORY.txt: missing-chunk.js']


@pytest.mark.parametrize(('change', 'expected'), [
    ('missing-css-asset', 'missing build output listed in BUILD_INVENTORY.txt: assets/font-data.css'),
    ('changed', 'build output differs from BUILD_INVENTORY.txt: assets/index-abc123.js'),
    ('extra', 'file not in BUILD_INVENTORY.txt: assets/stale.js'),
    ('malformed', 'BUILD_INVENTORY.txt malformed line 2'),
    ('empty', 'BUILD_INVENTORY.txt lists no files'),
])
def test_the_build_must_match_its_inventory(
        tools: tuple[ModuleType, ModuleType], change: str, expected: str) -> None:
    build_release, _ = tools
    files = _snapshot()
    if change == 'missing-css-asset':      # a stylesheet the CSS imports, absent from the build
        files['BUILD_INVENTORY.txt'] = _inventory(dict(files, **{'assets/font-data.css': b'x'}))
    elif change == 'changed':
        files['assets/index-abc123.js'] = b'changed after the build'
    elif change == 'extra':
        files['assets/stale.js'] = b'left over from another build'
    elif change == 'malformed':
        files['BUILD_INVENTORY.txt'] = b'# inventory\nnot a line\n'
    elif change == 'empty':
        files['BUILD_INVENTORY.txt'] = b'# inventory\n'
    assert expected in build_release.app_build_problems(files)


def test_a_build_without_its_inventory_refuses_both_packaging_paths(
        tools: tuple[ModuleType, ModuleType], checkout: Path, tmp_path: Path) -> None:
    build_release, build_portable = tools
    (checkout / 'frontend/webui/app/BUILD_INVENTORY.txt').unlink()
    for package in (lambda: build_release.build(checkout, tmp_path / 'dist'),
                    lambda: build_portable.stage_tree(tmp_path / 'stage', checkout)):
        with pytest.raises(RuntimeError, match='missing BUILD_INVENTORY.txt'):
            package()


PACKAGE_PNG = 'runtime/Lib/site-packages/werkzeug/debug/shared/console.png'


def _add_entry(path: Path, relative: str) -> None:
    """Add one entry to a temporary archive copy and refresh its inventory and sidecar."""
    with zipfile.ZipFile(path) as archive:
        entries = {info.filename: archive.read(info) for info in archive.infolist()}
    prefix = next(iter(entries)).split('/')[0] + '/'
    entries[prefix + relative] = b'REVIEW-CANARY'
    entries[prefix + 'SHA256SUMS.txt'] = ''.join(
        hashlib.sha256(data).hexdigest() + '  ' + name.removeprefix(prefix) + '\n'
        for name, data in entries.items() if name != prefix + 'SHA256SUMS.txt').encode()
    rewritten = path.with_suffix('.rewritten.zip')
    with zipfile.ZipFile(rewritten, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    rewritten.replace(path)
    _sidecar(path)


# R10-03: even a known pinned image path fails with synthetic canary bytes.
# Synthetic path/hash allowlist coverage lives in test_release_archive_gate.py.
@pytest.mark.parametrize(('relative', 'expected'), [
    (PACKAGE_PNG, None),
    ('runtime/Lib/site-packages/werkzeug/debug/shared/CONSOLE.PNG', None),
    ('runtime/node_modules/leak.js', 'forbidden path segment:'),
    ('runtime/Lib/site-packages/node_modules/leak.js', 'forbidden path segment:'),
    ('runtime/private.js.map', 'forbidden file suffix:'),
    ('runtime/Lib/site-packages/werkzeug/debug/shared/debugger.js.map', 'forbidden file suffix:'),
    ('runtime/Lib/site-packages/werkzeug/debug/shared/icon.svg', 'forbidden file suffix:'),
    ('runtime/coverage/index.html', 'forbidden path segment:'),
    ('runtime/game.png', 'forbidden file suffix:'),
    ('runtime/Lib/game.png', 'forbidden file suffix:'),
    ('Runtime/Lib/site-packages/game.png', 'forbidden file suffix:'),
    ('runtime/lib/site-packages/game.png', 'forbidden file suffix:'),
    ('frontend/webui/leak.png', 'forbidden file suffix:'),
])
@pytest.mark.parametrize('which', [1, 2], ids=['source', 'portable'])
def test_only_package_pngs_in_the_portable_runtime_are_exempt(
        release_checker: ModuleType, release_pair: tuple[Path, Path, Path],
        capsys: pytest.CaptureFixture[str], which: int, relative: str, expected: str | None) -> None:
    dist, path = release_pair[0], release_pair[which]
    _add_entry(path, relative)
    capsys.readouterr()
    result = release_checker.main(['--dist', str(dist)])
    output = capsys.readouterr().out
    assert result == 1, output
    assert f'{path.name}: ' in output and relative in output
    assert (expected or 'forbidden file suffix:') in output
