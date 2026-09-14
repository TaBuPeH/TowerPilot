"""Build a source beta from reviewed source paths, never the workspace wholesale."""
import hashlib
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOP = {'VERSION', 'LICENSE', 'README.md', 'requirements.txt', 'requirements-dev.txt', 'requirements-portable.lock',
       'pytest.ini', 'Start Tower Pilot.cmd', 'Start-TowerPilot.ps1', '.gitignore', '.gitattributes',
       # agent rules and skills ship with the source (0.2-beta): CLAUDE.md is
       # the rule book, AGENTS.md points Codex and others at it
       'CLAUDE.md', 'AGENTS.md'}
DOCS = {'README.md', 'ACCOUNT_SETUP.md', 'BLUESTACKS.md', 'GLOBAL_REWARDS.md',
        'RUN_TEMPLATES.md', 'RELEASE_0.1_BETA.md', 'RELEASE_0.2_BETA.md', 'RELEASE_0.3_BETA.md', 'RELEASE_0.4_BETA.md', 'RUNTIME_GEOMETRY.md',
        'PORTABLE_BUILD.md'}
# Whole folders that ship as they are: the Claude Code skills, agents, hooks
# and workflow helpers, the project-local Codex config, the release workflow.
# Personal files never do: .claude/settings.local.json is a per-machine
# permission list and is excluded by name.
AGENT_DIRS = {'.claude', '.codex', '.github'}
AGENT_EXCLUDE = {'.claude/settings.local.json'}


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
                'tools/build_release.py', 'tools/build_portable.py', 'tools/portable_launcher.py'}:
        return True
    if p.parts[0] == 'backend':
        return (len(p.parts) > 1 and p.parts[1] not in {
            'accounts', 'captures', 'logs', 'runs', 'recordings', 'profiles', 'templates',
            'tools', '__pycache__'} and p.suffix in {'.py', '.json', '.md'})
    if p.parts[0] == 'frontend':
        return p.suffix in {'.py', '.html', '.css', '.js', '.cjs'}
    return False


def build():
    version = (ROOT / 'VERSION').read_text().strip()
    names = subprocess.check_output(
        ['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
        cwd=ROOT).decode().split('\0')
    files = sorted({n for n in names if n and allowed(n) and (ROOT / n).is_file()})
    prefix = f'TowerPilot-{version}'
    output = ROOT / 'dist' / f'{prefix}-source.zip'
    output.parent.mkdir(exist_ok=True)
    inventory = []
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in files:
            content = (ROOT / name).read_bytes()
            archive.writestr(prefix + '/' + name, content)
            inventory.append(hashlib.sha256(content).hexdigest() + '  ' + name)
        archive.writestr(prefix + '/SHA256SUMS.txt', '\n'.join(inventory) + '\n')
    output.with_suffix('.zip.sha256').write_text(
        hashlib.sha256(output.read_bytes()).hexdigest() + '  ' + output.name + '\n')
    print(f'{output} ({len(files)} files)')
    return output


if __name__ == '__main__':
    build()
