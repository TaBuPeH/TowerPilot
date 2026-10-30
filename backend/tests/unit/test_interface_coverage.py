import hashlib

from player.interface_manifest import coverage


def test_descriptions_and_files_are_not_verified(tmp_path):
    path = tmp_path / 'cut.png'
    path.write_bytes(b'local cut')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    rel = 'home/battle_btn.png'
    def state(entries):
        model = coverage(lambda _: path, entries)
        home = next(s for s in model['screens'] if s['id'] == 'home')
        return next(c for c in home['controls'] if c['template'] == rel)['status']
    proof = dict(rel=rel, screen='home', verified=True, image_sha256=digest)
    assert state([]) == 'captured'
    assert state([proof]) == 'verified'
    assert state([dict(proof, screen='tournament')]) == 'captured'
    assert state([dict(proof, image_sha256='old')]) == 'captured'
    assert state([proof, dict(proof, verified=False)]) == 'captured'
    path.unlink()
    assert state([proof]) == 'documented'


def test_empty_screen_is_not_complete(tmp_path):
    model = coverage(lambda _: tmp_path / 'missing', [])
    assert model['verified'] == 0
    assert all(s['status'] != 'verified' for s in model['screens'])
