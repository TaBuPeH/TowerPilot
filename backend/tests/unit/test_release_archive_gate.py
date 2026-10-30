"""Offline archive mutations refresh both inventories and the ZIP sidecar.

Personal-looking strings are synthetic and assembled at runtime: these tests
themselves ship as source and must pass the same generic content-marker gate.
No test downloads dependencies, starts the app, or touches machine/account state.
"""

import paths as test_paths
import hashlib
import importlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType

import pytest

REPO = test_paths.REPO
APP = 'frontend/webui/app/'
PACKAGES = 'runtime/Lib/site-packages/'
Artifact = tuple[Path, str, str, dict[str, bytes]]


def inventory(entries: Mapping[str, bytes]) -> bytes:
    return ''.join(hashlib.sha256(data).hexdigest() + '  ' + name + '\n'
                   for name, data in sorted(entries.items())).encode()


def identity_for(kind: str, entries: Mapping[str, bytes]) -> dict[str, str]:
    """The fields the gate compares; the archive's own app inventory is what the identity must name."""
    return {'version': '9.9.9', 'kind': kind, 'working_tree': 'clean',
            'app_build_sha256': hashlib.sha256(entries[APP + 'BUILD_INVENTORY.txt']).hexdigest()}


@pytest.fixture()
def checker(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(REPO / 'tools'))
    monkeypatch.delitem(sys.modules, 'check_release_zips', raising=False)
    return importlib.import_module('check_release_zips')


@pytest.fixture(params=['source', 'portable'])
def artifact(tmp_path: Path, request: pytest.FixtureRequest) -> Artifact:
    kind = request.param
    suffix = '-windows-x64' if kind == 'portable' else ''
    prefix = 'TowerPilot-9.9.9' + suffix
    filename = prefix + ('.zip' if kind == 'portable' else '-source.zip')
    app = {
        'index.html': b'<script type="module" src="/ui/app/main.js"></script>',
        'main.js': b'export {};',
        'THIRD_PARTY_NOTICES.txt': b'Synthetic dependency licence\n',
    }
    app['BUILD_INVENTORY.txt'] = inventory(app)
    entries = {APP + name: data for name, data in app.items()}
    entries.update({'VERSION': b'9.9.9\n',
                    'docs/RELEASE_9.9.9.md': b'## Environment limits\nSynthetic test only.\n',
                    'backend/config.example.yaml': b'instances: {main: {serial: "", allow_taps: false, autostart: false}}\n'
                                                   b'active_instance: main\nactive_profile: default\n',
                    'backend/profiles/default.yaml': b'player: {}\n'})
    artifact = (tmp_path / filename, prefix, kind, entries)
    refresh_identity(artifact)
    return artifact


def refresh_identity(artifact: Artifact) -> None:
    """Name the archive's current app inventory in its identity, after a test changed the inventory."""
    kind = 'portable-windows-x64' if artifact[2] == 'portable' else 'source'
    artifact[3]['release_identity.json'] = json.dumps(identity_for(kind, artifact[3])).encode()


def write_artifact(artifact: Artifact) -> Path:
    path, prefix, _, entries = artifact
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in {**entries, 'SHA256SUMS.txt': inventory(entries)}.items():
            archive.writestr(prefix + '/' + name, data)
    path.with_suffix('.zip.sha256').write_text(
        hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name + '\n')
    return path


def check(checker: ModuleType, artifact: Artifact, *options: str) -> int:
    path = write_artifact(artifact)
    return checker.main(['--archive', str(path), '--kind', artifact[2], *options])


def test_clean_artifact_passes(checker: ModuleType, artifact: Artifact) -> None:
    assert check(checker, artifact) == 0


@pytest.mark.parametrize('name', [
    'backend/config.yaml', 'backend/config.yaml.bak-123',
    'backend/accounts/test/account.json', 'backend/profiles/personal.yaml',
    'backend/profiles/default.yaml.bak', 'backend/logs/event.jsonl',
    'backend/runs/run/stats.json', 'backend/captures/screen.txt',
    'backend/templates/header.txt', '.claude/settings.local.json',
    'tools/platform-tools/source.json', 'tools/scrcpy/server.txt', 'dist/old.txt',
    'Backend/Accounts/test/account.json', 'BACKEND/CONFIG.YAML',
])
def test_state_paths_are_refused(checker: ModuleType, artifact: Artifact, name: str,
                                capsys: pytest.CaptureFixture[str]) -> None:
    artifact[3][name] = b'{}'
    assert check(checker, artifact) == 1
    assert 'forbidden state path:' in capsys.readouterr().out


@pytest.mark.parametrize('name', [
    'backend/config.yaml.', 'backend/config.yaml ',
    'backend/accounts./synthetic.json', 'backend/accounts /synthetic.json',
    '.claude/settings.local.json.', '.claude/settings.local.json ',
])
def test_windows_extraction_aliases_are_refused(checker: ModuleType, artifact: Artifact,
                                               name: str, capsys: pytest.CaptureFixture[str]) -> None:
    artifact[3][name] = b'{}'
    assert check(checker, artifact) == 1
    output = capsys.readouterr().out
    assert 'invalid ZIP entry path:' in output
    assert 'hash mismatch' not in output


def personal_markers() -> list[tuple[str, str]]:
    return [
        ('windows-user-profile', 'Z:' + '\\Users\\' + 'synthetic-person\\file'),
        ('drive-project-path', 'Z:' + '/Projects/' + 'synthetic/file'),
        ('tool-temp-path', '/tmp/' + 'codex-synthetic/file'),
        ('tool-temp-path', '%TEMP%' + '\\portable-build-synthetic\\file'),
        ('url-credentials', 'https://' + 'synthetic:password' + chr(64) + 'example.invalid/path'),
        ('url-credentials', 'https://' + 'synthetic-token' + chr(64) + 'localhost/path'),
        ('email', 'synthetic-person' + chr(64) + 'example.invalid'),
    ]


@pytest.mark.parametrize(('marker', 'text'), personal_markers())
@pytest.mark.parametrize('encoding', ['utf-8', 'utf-16'])
def test_markers_are_refused_without_echoing_text(
        checker: ModuleType, artifact: Artifact, marker: str, text: str, encoding: str,
        capsys: pytest.CaptureFixture[str]) -> None:
    artifact[3]['backend/state.txt'] = text.encode(encoding)
    assert check(checker, artifact) == 1
    output = capsys.readouterr().out
    assert 'content marker ' + marker in output
    assert text not in output


@pytest.mark.parametrize('relative', ['package/data.txt', 'fixture.dist-info/licenses/LICENSE.txt'])
def test_local_forbid_file_is_read_and_applies_inside_site_packages(
        checker: ModuleType, artifact: Artifact, tmp_path: Path,
        capsys: pytest.CaptureFixture[str], relative: str) -> None:
    secret = 'SYNTHETIC-' + 'PRIVATE-MARKER'
    forbid = tmp_path / 'private-forbid.txt'
    forbid.write_text('# local only\n\n' + secret + '\n', encoding='utf-8')
    artifact[3][PACKAGES + relative] = secret.lower().encode()
    assert check(checker, artifact, '--forbid-file', str(forbid)) == 1
    output = capsys.readouterr().out
    assert 'local-forbid-list' in output
    assert secret.lower() not in output.lower()


def test_missing_forbid_file_fails_closed(checker: ModuleType, artifact: Artifact, tmp_path: Path) -> None:
    assert check(checker, artifact, '--forbid-file', str(tmp_path / 'missing')) == 1


def test_checker_source_has_no_machine_strings() -> None:
    sources = ''.join((REPO / 'tools' / name).read_text(encoding='utf-8') for name in (
        'check_release_zips.py', 'release_archive_policy.py', 'generate_dependency_images.py'))
    for value in (os.environ.get('USERNAME'), os.environ.get('USERPROFILE')):
        if value and len(value) > 3:
            assert value.casefold() not in sources.casefold()


@pytest.mark.parametrize(('relative', 'passes'), [
    ('fixture.dist-info/LICENSE.txt', True),
    ('fixture.dist-info/licenses/LICENSE', True),
    ('fixture.dist-info/NOTICE', True),
    ('fixture.dist-info/METADATA', False),
    ('fixture.dist-info/RECORD', False),
    ('fixture.dist-info/data.txt', False),
    ('fixture.dist-info/licenses/code.py', False),
    ('fixture.dist-info/licenses/LICENSE.py', False),
    ('package/LICENSE', False),
    ('package/code.py', False),
])
def test_email_marker_exempts_only_dist_info_licences_not_forbid_list(
        checker: ModuleType, artifact: Artifact, tmp_path: Path, relative: str, passes: bool,
        capsys: pytest.CaptureFixture[str]) -> None:
    email = 'synthetic-author' + chr(64) + 'example.invalid'
    artifact[3][PACKAGES + relative] = email.encode()
    assert check(checker, artifact) == (0 if passes else 1)
    capsys.readouterr()
    forbid = tmp_path / 'forbid.txt'
    forbid.write_text(email)
    assert check(checker, artifact, '--forbid-file', str(forbid)) == 1
    assert 'local-forbid-list' in capsys.readouterr().out


def test_licence_exemption_never_suppresses_other_markers(
        checker: ModuleType, artifact: Artifact) -> None:
    artifact[3][PACKAGES + 'fixture.dist-info/LICENSE'] = personal_markers()[0][1].encode()
    assert check(checker, artifact) == 1


def test_unexpected_dependency_image_fails(checker: ModuleType, artifact: Artifact,
                                          capsys: pytest.CaptureFixture[str]) -> None:
    artifact[3][PACKAGES + 'a_different_package/personal.png'] = b'synthetic image'
    assert check(checker, artifact) == 1
    output = capsys.readouterr().out
    assert 'forbidden file suffix:' in output
    assert 'hash mismatch' not in output


def test_known_path_with_other_bytes_fails(checker: ModuleType, artifact: Artifact) -> None:
    manifest = json.loads((REPO / 'tools/dependency_images.json').read_bytes())
    for image in manifest['images']:
        artifact[3][image['path']] = b'synthetic mutation'
    assert check(checker, artifact) == 1


@pytest.mark.parametrize('suffix', ['.map', '.png', '.jpg', '.jpeg', '.webp', '.gif',
                                  '.bmp', '.ico', '.svg', '.woff', '.woff2'])
def test_forbidden_file_kinds_fail(checker: ModuleType, artifact: Artifact, suffix: str) -> None:
    artifact[3]['frontend/webui/local' + suffix] = b'synthetic'
    assert check(checker, artifact) == 1


def test_known_pinned_images_pass_and_source_zip_has_no_image_exception(
        checker: ModuleType, artifact: Artifact, monkeypatch: pytest.MonkeyPatch) -> None:
    # Offline contract test uses a synthetic trusted allowlist at the loader boundary.
    # Production provenance and real wheel bytes were generated separately, not guessed.
    image = PACKAGES + 'fixture/debug.png'
    data = b'synthetic fixture pixels'
    monkeypatch.setattr(checker, 'dependency_images', lambda directory: {
        image: hashlib.sha256(data).hexdigest()})
    artifact[3][image] = data
    assert check(checker, artifact) == (0 if artifact[2] == 'portable' else 1)
    artifact[3][image.upper()] = artifact[3].pop(image)
    assert check(checker, artifact) == 1


def test_recorded_image_provenance_matches_every_pin(checker: ModuleType) -> None:
    manifest = json.loads((REPO / 'tools/dependency_images.json').read_bytes())
    pins = json.loads((REPO / 'tools/portable_pins.json').read_bytes())
    inspected = manifest['inspected_artifacts']
    assert {(row['requirement'], row['filename'], row['sha256']) for row in inspected} == {
        (row['requirement'], artifact['filename'], artifact['sha256'])
        for row in pins['packages'] for artifact in row['artifacts']}
    images = checker.dependency_images(REPO / 'tools')
    assert len(images) == sum(row['png_count'] for row in inspected) == 3


def identity(artifact: Artifact) -> dict[str, str]:
    return json.loads(artifact[3]['release_identity.json'])


@pytest.mark.parametrize('field', ['version', 'kind', 'app_build_sha256', 'working_tree'])
def test_identity_must_match_archive(checker: ModuleType, artifact: Artifact, field: str,
                                    capsys: pytest.CaptureFixture[str]) -> None:
    data = identity(artifact)
    data[field] = 'wrong'
    artifact[3]['release_identity.json'] = json.dumps(data).encode()
    assert check(checker, artifact) == 1
    assert 'release identity mismatch: ' + field in capsys.readouterr().out


def test_dirty_identity_is_refused(checker: ModuleType, artifact: Artifact,
                                  capsys: pytest.CaptureFixture[str]) -> None:
    data = identity(artifact)
    data['working_tree'] = 'dirty'
    artifact[3]['release_identity.json'] = json.dumps(data).encode()
    assert check(checker, artifact) == 1
    assert 'release identity mismatch: working_tree' in capsys.readouterr().out


@pytest.mark.parametrize('data', [None, b'{invalid', b'[]'])
def test_identity_is_required_by_default_missing_or_malformed_fails(
        checker: ModuleType, artifact: Artifact, data: bytes | None,
        capsys: pytest.CaptureFixture[str]) -> None:
    if data is None:
        del artifact[3]['release_identity.json']
    else:
        artifact[3]['release_identity.json'] = data
    assert check(checker, artifact) == 1
    assert 'missing or invalid release_identity.json' in capsys.readouterr().out


def test_matching_identity_passes(checker: ModuleType, artifact: Artifact) -> None:
    assert check(checker, artifact) == 0


def test_there_is_no_flag_to_switch_the_identity_check_off(checker: ModuleType, artifact: Artifact) -> None:
    path = write_artifact(artifact)
    with pytest.raises(SystemExit) as refused:
        checker.main(['--archive', str(path), '--kind', artifact[2], '--require-identity'])
    assert refused.value.code == 2


@pytest.mark.parametrize('data', [None, b'# Release without limits\n'])
def test_limits_doc_required(checker: ModuleType, artifact: Artifact, data: bytes | None) -> None:
    del artifact[3]['docs/RELEASE_9.9.9.md']
    if data is not None:
        artifact[3]['docs/RELEASE_9.9.9.md'] = data
    assert check(checker, artifact) == 1


def test_app_bytes_must_match_build_inventory_even_with_fresh_archive_hashes(
        checker: ModuleType, artifact: Artifact, capsys: pytest.CaptureFixture[str]) -> None:
    artifact[3][APP + 'main.js'] = b'export const changed = true;'
    assert check(checker, artifact) == 1
    assert 'build output differs from BUILD_INVENTORY.txt: main.js' in capsys.readouterr().out


def test_archive_version_must_match_name(checker: ModuleType, artifact: Artifact) -> None:
    artifact[3]['VERSION'] = b'8.8.8'
    assert check(checker, artifact) == 1


def test_single_artifact_runs_without_checkout_or_application_imports(
        checker: ModuleType, artifact: Artifact, tmp_path: Path) -> None:
    path = write_artifact(artifact)
    tools = tmp_path / 'standalone'
    tools.mkdir()
    for name in ('check_release_zips.py', 'release_archive_policy.py', 'build_release.py',
                 'dependency_images.json', 'portable_pins.json', 'release_marker_allowlist.json'):
        shutil.copyfile(REPO / 'tools' / name, tools / name)
    result = subprocess.run([sys.executable, '-E', '-s', str(tools / 'check_release_zips.py'),
                             '--archive', str(path), '--kind', artifact[2]],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.startswith('OK: ')


def test_gate_source_and_data_pass_their_own_content_rules(checker: ModuleType, artifact: Artifact) -> None:
    for name in ('check_release_zips.py', 'release_archive_policy.py', 'generate_dependency_images.py',
                 'dependency_images.json', 'portable_pins.json', 'release_marker_allowlist.json'):
        artifact[3]['tools/' + name] = (REPO / 'tools' / name).read_bytes()
    artifact[3]['backend/tests/unit/test_release_archive_gate.py'] = (test_paths.TESTS / 'unit' / 'test_release_archive_gate.py').read_bytes()
    assert check(checker, artifact) == 0


@pytest.mark.parametrize('seed', ['unchanged-example', 'binding', 'credential', 'serial', 'authority'])
def test_no_seeded_config_can_ship_even_when_equal_to_generic_example(
        checker: ModuleType, artifact: Artifact, seed: str) -> None:
    # R10-02's run-time equality check belongs to the qualifier (p10d).
    # At the archive boundary, even an unmodified seed is local state.
    data = artifact[3]['backend/config.example.yaml']
    extras = {'binding': b'account: synthetic\n', 'credential': b'password: synthetic\n',
              'serial': b'serial: synthetic-device\n', 'authority': b'allow_taps: true\n'}
    artifact[3]['backend/config.yaml'] = data + extras.get(seed, b'')
    assert check(checker, artifact) == 1


def test_generator_verifies_wheel_bytes_before_inspecting_images(
        checker: ModuleType, tmp_path: Path) -> None:
    generator = importlib.import_module('generate_dependency_images')
    wheel = tmp_path / 'fixture-1.0-py3-none-any.whl'
    image = 'fixture/debug.png'
    data = b'synthetic dependency pixels'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr(image, data)
    pins = {'packages': [{'requirement': 'fixture==1.0', 'artifacts': [{
        'filename': wheel.name, 'sha256': hashlib.sha256(wheel.read_bytes()).hexdigest(),
        'url': 'https://example.invalid/never-downloaded'}]}]}
    pins_path = tmp_path / 'pins.json'
    pins_path.write_text(json.dumps(pins))
    manifest = generator.generate(pins_path, tmp_path)
    assert manifest['images'] == [{
        'path': PACKAGES + image, 'sha256': hashlib.sha256(data).hexdigest(),
        'package': 'fixture', 'version': '1.0', 'wheel': wheel.name,
        'wheel_sha256': pins['packages'][0]['artifacts'][0]['sha256'], 'wheel_path': image,
    }]
    wheel.write_bytes(b'mutated wheel')
    with pytest.raises(ValueError, match='Pinned artifact hash mismatch'):
        generator.generate(pins_path, tmp_path)

def test_android_device_temp_path_is_not_a_host_temp_path(checker: ModuleType, artifact: Artifact) -> None:
    artifact[3]['backend/device/push.py'] = b'remote = "/data/local/tmp/towerpilot_preview_00.jar"\n'
    assert check(checker, artifact) == 0
    artifact[3]['backend/device/push.py'] = ('local = "' + '/tmp/' + 'towerpilot_preview_00.jar"\n').encode()
    assert check(checker, artifact) == 1


def test_allowlisted_marker_string_is_waived_only_for_its_exact_path_and_text(
        checker: ModuleType, artifact: Artifact, capsys: pytest.CaptureFixture[str]) -> None:
    import release_archive_policy as policy
    rows = json.loads((REPO / 'tools' / 'release_marker_allowlist.json').read_bytes())['entries']
    row = rows[0]
    artifact[3][row['path']] = ('x = "' + row['text'] + '"\n').encode()
    assert check(checker, artifact) == 0
    artifact[3]['backend/tests/other_file.py'] = ('x = "' + row['text'] + '"\n').encode()
    assert check(checker, artifact) == 1
    assert 'content marker email: backend/tests/other_file.py' in capsys.readouterr().out
    del artifact[3]['backend/tests/other_file.py']
    other = 'synthetic-person' + chr(64) + 'example.invalid'
    artifact[3][row['path']] = ('x = "' + row['text'] + '"\ny = "' + other + '"\n').encode()
    assert check(checker, artifact) == 1
    for bad in ({**row, 'text': 'not a match'}, {**row, 'reason': ' '}, {**row, 'marker': 'unknown'}):
        with pytest.raises(ValueError):
            _load_allowlist(policy, bad)


def _load_allowlist(policy: ModuleType, row: dict) -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as folder:
        Path(folder, 'release_marker_allowlist.json').write_text(json.dumps({'entries': [row]}), encoding='utf-8')
        policy.marker_allowlist(Path(folder))


def test_generated_app_notices_waive_only_the_email_marker(checker: ModuleType, artifact: Artifact) -> None:
    email = 'synthetic-author' + chr(64) + 'example.invalid'
    artifact[3][APP + 'THIRD_PARTY_NOTICES.txt'] = ('Copyright ' + email + '\n').encode()
    app = {name[len(APP):]: data for name, data in artifact[3].items() if name.startswith(APP) and not name.endswith('BUILD_INVENTORY.txt')}
    artifact[3][APP + 'BUILD_INVENTORY.txt'] = inventory(app)
    refresh_identity(artifact)
    assert check(checker, artifact) == 0
    artifact[3][APP + 'THIRD_PARTY_NOTICES.txt'] = ('Copyright ' + email + '\n' + personal_markers()[0][1] + '\n').encode()
    app = {name[len(APP):]: data for name, data in artifact[3].items() if name.startswith(APP) and not name.endswith('BUILD_INVENTORY.txt')}
    artifact[3][APP + 'BUILD_INVENTORY.txt'] = inventory(app)
    refresh_identity(artifact)
    assert check(checker, artifact) == 1
