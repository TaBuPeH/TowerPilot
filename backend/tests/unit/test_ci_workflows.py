"""The CI workflows keep their install and test contracts.

pip refuses a bare requirement beside a hash-locked `-r` file ("In --require-hashes mode, all
requirements must have their versions pinned with =="), which broke release.yml's single
`pip install -r requirements-portable.lock pytest` line before any gate ran. These tests read the
workflow files themselves; the real proof is a workflow run on GitHub.
"""
import re
import shlex

import pytest
import yaml

import paths as test_paths

WORKFLOWS = test_paths.REPO / '.github' / 'workflows'
CONTRACT_MODULES = ('test_dashboard_contract.py', 'test_dashboard_contract_pages.py', 'test_harvest_contract.py')

pytestmark = pytest.mark.skipif(not WORKFLOWS.is_dir(), reason='this tree ships without .github/workflows')


def _load(name):
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding='utf-8'))


def _run_lines(workflow):
    for job in workflow['jobs'].values():
        for step in job['steps']:
            if 'run' in step:
                for line in step['run'].splitlines():
                    yield line.strip()


def _mixed_hash_installs(line):
    """The bare requirements one `pip install` command puts beside a hash-locked `-r` file."""
    if not re.search(r'\bpip\s+install\b', line):
        return []
    tokens = shlex.split(line.split('install', 1)[1], posix=False)
    hashed, bare = False, []
    skip = False
    for index, token in enumerate(tokens):
        if skip:
            skip = False
        elif token in ('-r', '--requirement'):
            skip = True
            path = test_paths.REPO / tokens[index + 1].strip('"\'')
            hashed = hashed or '--hash=' in path.read_text(encoding='utf-8')
        elif token in ('-c', '--constraint', '-i', '--index-url'):
            skip = True
        elif not token.startswith('-'):
            bare.append(token)
    return bare if hashed else []


def test_the_checker_catches_the_old_release_install_line():
    assert _mixed_hash_installs('python -m pip install -r requirements-portable.lock pytest') == ['pytest']
    assert _mixed_hash_installs('python -m pip install -r requirements-portable.lock') == []
    assert _mixed_hash_installs('python -m pip install pytest==9.1.1') == []


@pytest.mark.parametrize('name', sorted(path.name for path in WORKFLOWS.glob('*.yml')) if WORKFLOWS.is_dir() else [])
def test_no_pip_command_mixes_the_hashed_lock_with_a_bare_requirement(name):
    for line in _run_lines(_load(name)):
        assert _mixed_hash_installs(line) == [], f'{name}: {line}'


@pytest.mark.parametrize('name, job', [('ci.yml', 'backend'), ('ci.yml', 'contract-drift'), ('release.yml', 'release')])
def test_pytest_is_pinned_and_installed_apart_from_the_lock(name, job):
    lines = [line for line in _run_lines({'jobs': {job: _load(name)['jobs'][job]}}) if 'pip install' in line]
    assert any(re.search(r'\bpytest==\d', line) and 'requirements-portable.lock' not in line for line in lines), lines


def test_ci_runs_on_push_and_pull_request_for_master_and_the_branch_without_a_path_filter():
    workflow = _load('ci.yml')
    triggers = workflow.get(True, workflow.get('on'))   # PyYAML reads the key `on` as True
    for event in ('push', 'pull_request'):
        assert set(triggers[event]['branches']) == {'master'}
        assert 'paths' not in triggers[event] and 'paths-ignore' not in triggers[event]


def test_ci_has_backend_contract_and_frontend_jobs_on_windows():
    jobs = _load('ci.yml')['jobs']
    assert set(jobs) == {'backend', 'contract-drift', 'frontend'}
    assert {job['runs-on'] for job in jobs.values()} == {'windows-latest'}
    assert all('if' not in job for job in jobs.values()), 'a job with a condition could skip silently'


def test_contract_modules_run_in_the_contract_job_and_nowhere_else():
    jobs = _load('ci.yml')['jobs']
    backend = ' '.join(_run_lines({'jobs': {'backend': jobs['backend']}}))
    contract = ' '.join(_run_lines({'jobs': {'contract-drift': jobs['contract-drift']}}))
    backend_lines = [line for line in _run_lines({'jobs': {'b': jobs['backend']}}) if line.startswith('python -m pytest')]
    assert len(backend_lines) == 1 and backend_lines[0].startswith('python -m pytest backend/tests/unit ')
    pytest_line = [line for line in _run_lines({'jobs': {'c': jobs['contract-drift']}}) if line.startswith('python -m pytest')]
    assert len(pytest_line) == 1
    assert shlex.split(pytest_line[0])[3:] == [f'backend/tests/unit/{module}' for module in CONTRACT_MODULES]
    for module in CONTRACT_MODULES:
        assert f'--ignore=backend/tests/unit/{module}' in backend
    assert backend.count('--ignore=') == len(CONTRACT_MODULES)


def test_the_frontend_job_runs_node_22_typecheck_lint_and_unit_tests():
    job = _load('ci.yml')['jobs']['frontend']
    assert job['defaults']['run']['working-directory'] == 'frontend/app'
    node = [step for step in job['steps'] if str(step.get('uses', '')).startswith('actions/setup-node')]
    assert [str(step['with']['node-version']) for step in node] == ['22']
    assert [line for line in _run_lines({'jobs': {'frontend': job}})] == [
        'npm ci', 'npm run typecheck', 'npm run lint', 'npx vitest run']


def test_ci_needs_no_secret_adb_or_emulator():
    text = (WORKFLOWS / 'ci.yml').read_text(encoding='utf-8')
    code = '\n'.join(line for line in text.splitlines() if not line.lstrip().startswith('#'))
    assert 'secrets.' not in code and 'GH_TOKEN' not in code
    assert not re.search(r'\badb\b|mumu|emulator|8620|8630|5037', code, re.IGNORECASE)
    assert _load('ci.yml')['permissions'] == {'contents': 'read'}
