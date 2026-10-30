"""Test paths have one owner, independent of each consumer's depth."""
import ast

import pytest

import paths


def file_path_arithmetic(source):
    tree = ast.parse(source)
    anchors = {'__file__'}

    def anchored(node):
        return any(isinstance(part, ast.Name) and part.id in anchors for part in ast.walk(node))

    # Follow assigned anchors too: HERE = Path(__file__); HERE.parent.
    while True:
        before = anchors.copy()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None and anchored(node.value):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                anchors.update(target.id for target in targets if isinstance(target, ast.Name))
        if anchors == before:
            break
    return sorted({node.lineno for node in ast.walk(tree) if anchored(node) and (
        isinstance(node, ast.Attribute) and node.attr in {'parent', 'parents', 'with_name', 'with_suffix', 'joinpath', 'dirname', 'abspath', 'realpath'}
        or isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {'dirname', 'abspath', 'realpath', 'join'}
        or isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div))})


def test_test_tree_uses_the_shared_path_owner():
    scanned = set()
    offenders = {}
    for file in paths.TESTS.rglob('*.py'):
        if file == paths.TESTS / 'support' / 'paths.py':
            continue
        relative = file.relative_to(paths.TESTS).as_posix()
        lines = file_path_arithmetic(file.read_text(encoding='utf-8-sig'))
        scanned.add(relative)
        if lines:
            offenders[relative] = lines
    assert 'conftest.py' in scanned
    assert offenders == {}, f'__file__ path arithmetic belongs in support/paths.py: {offenders}'


@pytest.mark.parametrize('source', [
    'Path(__file__).resolve().parents[1]',
    'Path(__file__).parent / "fixtures"',
    'Path(__file__).with_name("child.py")',
    'os.path.dirname(os.path.dirname(os.path.abspath(__file__)))',
    'Path(__file__) / "child"',
    'anchor = Path(__file__)\nroot = anchor.parent',
])
def test_path_guard_rejects_depth_dependent_anchors(source):
    assert file_path_arithmetic(source)


def test_path_guard_allows_imported_module_files_and_provenance_text():
    assert file_path_arithmetic('Path(module.__file__).parent / "data"') == []
    assert file_path_arithmetic('note = "Path(__file__).parent"') == []


def test_shared_paths_locate_the_checkout():
    assert (paths.BACKEND / 'settings.py').is_file()
    assert paths.DASHBOARD.is_file()
    assert paths.BACKEND == paths.REPO / 'backend'
    assert paths.TESTS == paths.BACKEND / 'tests'
    assert paths.FIXTURES == paths.TESTS / 'fixtures'
    assert paths.FRONTEND_APP == paths.REPO / 'frontend' / 'app'
    assert paths.FRONTEND_FIXTURES == paths.FRONTEND_APP / 'tests' / 'fixtures'
    assert paths.RELEASE_TOOLS == paths.REPO / 'tools'
    assert paths.BACKEND_TOOLS == paths.BACKEND / 'tools'
