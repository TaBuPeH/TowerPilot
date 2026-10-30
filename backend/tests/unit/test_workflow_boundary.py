"""The installable workflow core must not acquire runtime dependencies."""

import paths as test_paths
import ast
import subprocess
import sys
from pathlib import Path


def test_core_imports_only_stdlib_and_its_own_package():
    root = test_paths.BACKEND / 'towerpilot_workflows'
    scanned = set()
    for path in root.rglob('*.py'):
        scanned.add(path.name)
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split('.')[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or '').split('.')[0]] if not node.level else []
            else:
                continue
            assert all(name in sys.stdlib_module_names or name == 'towerpilot_workflows'
                       for name in names), (path.name, node.lineno, names)

    assert '__init__.py' in scanned


def test_core_import_has_no_machine_or_device_side_effects(tmp_path):
    root = test_paths.BACKEND
    code = """
import sys
sys.path.insert(0, sys.argv[1])
from towerpilot_workflows.operations import Operations
assert 'settings' not in sys.modules
assert not any(name == 'device' or name.startswith('device.') for name in sys.modules)
from pathlib import Path
assert list(Path.cwd().iterdir()) == []
"""
    result = subprocess.run([sys.executable, '-I', '-c', code, str(root)],
                            cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
