"""Source-at-start provenance must survive later edits and packaged installs."""
import hashlib
import json

import pytest

from runtime import build_info


def test_source_identity_is_immutable_across_edits_and_ignores_account_state(tmp_path):
    source = tmp_path / 'backend' / 'example.py'
    source.parent.mkdir()
    source.write_text('x = 1', encoding='utf-8')
    started = build_info.snapshot(tmp_path, 'worker')
    assert started['source_revision'] is None
    assert started['error'] is None
    assert build_info.compare(tmp_path, started)['source_changed'] is False
    account = tmp_path / 'backend' / 'accounts' / 'private.py'
    account.parent.mkdir()
    account.write_text('private data', encoding='utf-8')
    assert build_info.compare(tmp_path, started)['source_changed'] is False
    source.write_text('x = 2', encoding='utf-8')
    current = build_info.compare(tmp_path, started)
    assert current['source_changed'] is True
    assert current['source_fingerprint'] == started['source_fingerprint']
    assert current['disk_source_fingerprint'] != started['source_fingerprint']


def test_git_linked_worktree_packed_reference(tmp_path):
    root = tmp_path / 'checkout'
    root.mkdir()
    common = tmp_path / 'git'
    worktree = common / 'worktrees' / 'checkout'
    worktree.mkdir(parents=True)
    (root / '.git').write_text('gitdir: ../git/worktrees/checkout', encoding='utf-8')
    (worktree / 'commondir').write_text('../..', encoding='utf-8')
    (worktree / 'HEAD').write_text('ref: refs/heads/test', encoding='utf-8')
    (common / 'packed-refs').write_text('abc123 refs/heads/test\n', encoding='utf-8')
    assert build_info.revision(root) == 'abc123'


def test_unreadable_source_is_unknown_not_current(tmp_path, monkeypatch):
    def broken(_):
        raise PermissionError('locked source')
    monkeypatch.setattr(build_info, 'source_fingerprint', broken)
    started = build_info.snapshot(tmp_path, 'dashboard')
    assert started['source_fingerprint'] is None
    current = build_info.compare(tmp_path, started)
    assert current['source_changed'] is None
    assert current['error']['type'] == 'PermissionError'


# --------------------------------------------------------------- release identity (item 10 p10a)
def install(root, *, app=None, record=None):
    """A synthetic installed release: one source file, a built app with its inventory, and the identity naming both."""
    (root / 'backend').mkdir(parents=True)
    (root / 'backend' / 'example.py').write_text('x = 1', encoding='utf-8')
    app_dir = root.joinpath(*build_info.APP_BUILD)
    files = {'index.html': b'<p>app</p>', 'assets/main.js': b'console.log(1)'} if app is None else app
    for name, data in files.items():
        (app_dir / name).parent.mkdir(parents=True, exist_ok=True)
        (app_dir / name).write_bytes(data)
    inventory = ''.join(hashlib.sha256(data).hexdigest() + '  ' + name + '\n' for name, data in sorted(files.items()))
    (app_dir / build_info.APP_INVENTORY).write_bytes(inventory.encode())     # bytes: the build writes LF on every system
    identity = dict(schema_version=1, version='9.9.9', kind='source', source_fingerprint=build_info.source_fingerprint(root),
                    app_build_sha256=hashlib.sha256(inventory.encode()).hexdigest(), lock_sha256='0' * 64,
                    built_at='2027-01-15T08:00:00Z', source_revision=None, working_tree='clean')
    (root / build_info.RELEASE_IDENTITY).write_text(json.dumps(identity | (record or {})), encoding='utf-8')
    return identity


def test_a_tree_without_an_identity_is_a_checkout(tmp_path):
    result = build_info.release_identity(tmp_path)
    assert result['kind'] == 'checkout'
    assert result.keys() == {*build_info.IDENTITY_FIELDS, 'matches_source', 'matches_app_build', 'error'}
    assert {key: value for key, value in result.items() if key != 'kind'} == dict.fromkeys(result.keys() - {'kind'})


def test_a_matching_install_reads_true_for_both(tmp_path):
    identity = install(tmp_path)
    result = build_info.release_identity(tmp_path)
    assert {field: result[field] for field in build_info.IDENTITY_FIELDS} == identity | dict(python=None)
    assert (result['matches_source'], result['matches_app_build'], result['error']) == (True, True, None)


@pytest.mark.parametrize(('text', 'error'), [
    ('{broken', 'not valid JSON'), ('[]', 'not an object'), ('"source"', 'not an object'),
    ('{"kind": "unrecognized"}', 'unrecognized kind'), ('{}', 'unrecognized kind'), ('{"kind": ["source"]}', 'unrecognized kind')])
def test_a_record_the_server_cannot_use_is_unknown_and_says_why(tmp_path, text, error):
    (tmp_path / build_info.RELEASE_IDENTITY).write_text(text, encoding='utf-8')
    result = build_info.release_identity(tmp_path)
    assert (result['kind'], result['error'], result['matches_source'], result['matches_app_build']) == ('unknown', error, None, None)


def test_an_identity_that_cannot_be_read_is_unknown_not_a_checkout(tmp_path):
    (tmp_path / build_info.RELEASE_IDENTITY).mkdir()        # reading a folder as a file fails with an OSError
    result = build_info.release_identity(tmp_path)
    assert (result['kind'], result['error']) == ('unknown', 'unreadable')


def test_verify_false_reads_the_record_and_hashes_nothing(tmp_path, monkeypatch):
    install(tmp_path)
    monkeypatch.setattr(build_info, 'source_fingerprint', lambda _: pytest.fail('hashed the source'))
    monkeypatch.setattr(build_info, '_matches_app_build', lambda *_: pytest.fail('hashed the app'))
    result = build_info.release_identity(tmp_path, verify=False)
    assert (result['kind'], result['matches_source'], result['matches_app_build']) == ('source', None, None)


def test_source_that_cannot_be_read_is_unknown_not_a_mismatch(tmp_path, monkeypatch):
    install(tmp_path)
    def broken(_):
        raise PermissionError('locked source')
    monkeypatch.setattr(build_info, 'source_fingerprint', broken)
    assert build_info.release_identity(tmp_path)['matches_source'] is None


def test_an_edited_extra_or_missing_source_file_reads_false_and_state_folders_do_not_count(tmp_path):
    install(tmp_path)
    (tmp_path / 'backend' / 'accounts').mkdir()
    (tmp_path / 'backend' / 'accounts' / 'private.py').write_text('state', encoding='utf-8')
    (tmp_path / 'backend' / '__pycache__').mkdir()
    (tmp_path / 'backend' / '__pycache__' / 'example.py').write_text('cache', encoding='utf-8')
    assert build_info.release_identity(tmp_path)['matches_source'] is True
    (tmp_path / 'backend' / 'example.py').write_text('x = 2', encoding='utf-8')
    assert build_info.release_identity(tmp_path)['matches_source'] is False
    (tmp_path / 'backend' / 'example.py').write_text('x = 1', encoding='utf-8')
    (tmp_path / 'backend' / 'extra.py').write_text('y = 1', encoding='utf-8')
    assert build_info.release_identity(tmp_path)['matches_source'] is False
    (tmp_path / 'backend' / 'extra.py').unlink()
    assert build_info.release_identity(tmp_path)['matches_source'] is True


def test_a_record_without_a_fingerprint_or_inventory_hash_matches_nothing(tmp_path):
    install(tmp_path, record=dict(source_fingerprint=None, app_build_sha256=None))
    result = build_info.release_identity(tmp_path)
    assert (result['matches_source'], result['matches_app_build']) == (False, False)


@pytest.mark.parametrize('change', ['edit', 'delete', 'extra', 'extra-in-new-folder', 'edit-inventory', 'delete-inventory'])
def test_app_bytes_are_checked_against_the_inventory_not_only_the_inventory_against_the_record(tmp_path, change):
    install(tmp_path)
    app = tmp_path.joinpath(*build_info.APP_BUILD)
    assert build_info.release_identity(tmp_path)['matches_app_build'] is True
    if change == 'edit':
        (app / 'assets' / 'main.js').write_bytes(b'console.log(2)')
    elif change == 'delete':
        (app / 'assets' / 'main.js').unlink()
    elif change == 'extra':
        (app / 'assets' / 'extra.js').write_bytes(b'x')
    elif change == 'extra-in-new-folder':
        (app / 'more').mkdir()
        (app / 'more' / 'extra.js').write_bytes(b'x')
    elif change == 'edit-inventory':
        (app / build_info.APP_INVENTORY).write_bytes(b'# edited\n')
    else:
        (app / build_info.APP_INVENTORY).unlink()
    assert build_info.release_identity(tmp_path)['matches_app_build'] is False


@pytest.mark.parametrize('lines', [
    ['not an inventory line'], ['0' * 64 + ' one-space'], [], ['# only a comment'],
    ['{digest}  index.html', '{digest}  index.html'], ['{digest}  BUILD_INVENTORY.txt'], ['{digest}  ../escape.js']])
def test_an_inventory_the_build_would_not_have_written_reads_false(tmp_path, lines):
    install(tmp_path)
    app = tmp_path.joinpath(*build_info.APP_BUILD)
    digest = hashlib.sha256(b'<p>app</p>').hexdigest()
    inventory = ''.join(line.format(digest=digest) + '\n' for line in lines)
    (app / build_info.APP_INVENTORY).write_bytes(inventory.encode())
    record = json.loads((tmp_path / build_info.RELEASE_IDENTITY).read_text(encoding='utf-8'))
    record['app_build_sha256'] = hashlib.sha256(inventory.encode()).hexdigest()    # the record agrees: the inventory itself is wrong
    (tmp_path / build_info.RELEASE_IDENTITY).write_text(json.dumps(record), encoding='utf-8')
    assert build_info.release_identity(tmp_path)['matches_app_build'] is False


def test_an_app_that_cannot_be_read_is_unknown_not_edited(tmp_path, monkeypatch):
    install(tmp_path)
    real = build_info.Path.read_bytes
    def locked(self):
        if self.name == 'main.js':
            raise PermissionError('locked')
        return real(self)
    monkeypatch.setattr(build_info.Path, 'read_bytes', locked)
    assert build_info.release_identity(tmp_path)['matches_app_build'] is None
