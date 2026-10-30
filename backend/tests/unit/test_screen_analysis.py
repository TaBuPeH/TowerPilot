import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from player import screen_analysis as sa


def test_classifier_rejects_unknown_and_prefers_specific_child(monkeypatch):
    monkeypatch.setattr(sa, 'screen_matches', lambda f,l,s: s in ('events','bots'))
    assert sa.classify(None, [])['name'] == 'bots'
    monkeypatch.setattr(sa, 'screen_matches', lambda *a: False)
    assert sa.classify(None, []) == {'name':'unknown', 'candidates':[]}


def test_catalogue_aliases_deduplicate_and_unknown_roles_stay_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(sa, 'classify', lambda *a: {'name':'unknown','candidates':[]})
    rng = np.random.default_rng(91)
    source = rng.integers(0,256,(140,140,3),dtype=np.uint8)
    frame = np.zeros((400,400,3),np.uint8)
    frame[110:250,120:260] = source
    cv2.imwrite(str(tmp_path/'source.png'),source)
    records = [{'sha256':'same','file':'source.png','name':n} for n in ('first','alias')]
    result = sa.analyze(frame, [], tmp_path, {'images':records})
    assert result['tested'] == 1
    assert len(result['matches']) == 1
    hit = result['matches'][0]
    assert hit['names'] == ['alias','first']
    assert hit['roles'] == [] and not hit['ambiguous']
    assert abs(hit['rect'][0]-120) <= 1
    blank = sa.analyze(np.zeros_like(frame), [], tmp_path, {'images':records})
    assert blank['matches'] == []


@pytest.fixture
def analysis_job(tmp_path, monkeypatch):
    import settings
    from control import operations as service
    from runtime import operations
    from player import accounts, asset_library
    from device import capture
    from vision import textocr

    cfg = {'active_instance': 'main', 'instances': {'main': {'serial': 'fake:1'}}}
    store = operations.Operations(tmp_path / 'operations.db')
    op, _ = store.accept('screen_analysis', service.context(tmp_path, cfg, {}, None), {})
    monkeypatch.setenv('TOWER_OPERATION_ID', op['id'])
    monkeypatch.setenv('TOWER_OPERATION_DB', store.path)
    directory = Path(op['context']['artifact_dir'])
    library = directory / 'asset_library' / 'synthetic'
    library.mkdir(parents=True)
    (library / 'index.json').write_text(json.dumps({'images': []}))
    frame = np.random.default_rng(92).integers(0, 256, (64, 64, 3), dtype=np.uint8)
    monkeypatch.setattr('sys.argv', ['screen_analysis', '--instance', 'main'])
    monkeypatch.setattr(settings, 'bind_device', lambda *a: None)
    monkeypatch.setattr(settings, 'CONFIG', cfg)
    monkeypatch.setattr(settings, 'instance', lambda: cfg['instances']['main'])
    monkeypatch.setattr(accounts, 'calibration_dir', lambda *a: directory)
    monkeypatch.setattr(asset_library, 'installed', lambda *a: {'key': 'synthetic'})
    monkeypatch.setattr(asset_library, 'valid_index', lambda *a: True)
    monkeypatch.setattr(capture, 'grab', lambda: frame.copy())
    monkeypatch.setattr(textocr, 'read_lines', lambda *a: [])
    return operations, store, op, directory, frame


def test_producer_and_finalizer_bind_exact_encoded_screenshot(analysis_job, monkeypatch):
    operations, store, op, directory, frame = analysis_job
    states = []
    write = sa._write_state
    def observe(path, state):
        write(path, state)
        states.append(json.loads(path.read_text()))
    monkeypatch.setattr(sa, '_write_state', observe)
    with operations.worker_operation():
        sa.main()
    result = store.get(op['id'])
    assert result['status'] == 'succeeded'
    assert result['error'] is None
    refs = result['result_refs']
    state = json.loads(Path(refs['screen_analysis.json']['path']).read_text())
    screenshot = state['screenshot']
    assert screenshot == {
        'operation_id': op['id'], 'path': str(directory / 'screen_analysis.png'),
        'revision': refs['screen_analysis.png']['revision'],
    }
    expected = cv2.imencode('.png', frame)[1].tobytes()
    assert Path(refs['screen_analysis.png']['path']).read_bytes() == expected
    assert operations.file_revision(refs['screen_analysis.png']['path']) == screenshot['revision']
    assert all(item['screenshot'] == screenshot for item in states[1:])
    assert 'screenshot' not in states[0]


@pytest.mark.parametrize('fault', ['encode', 'write', 'short_write', 'readback'])
def test_producer_write_failure_cannot_bless_previous_png(analysis_job, monkeypatch, fault):
    operations, store, op, directory, _ = analysis_job
    image = directory / 'screen_analysis.png'
    previous = b'previous-operation-image'
    image.write_bytes(previous)
    if fault == 'encode':
        monkeypatch.setattr(sa.cv2, 'imencode', lambda *a: (False, None))
    elif fault in ('write', 'short_write'):
        original = Path.write_bytes
        def fail(path, raw):
            if path == image:
                if fault == 'write':
                    raise PermissionError('PNG write denied')
                return original(path, raw[:8])
            return original(path, raw)
        monkeypatch.setattr(Path, 'write_bytes', fail)
    else:
        original = Path.read_bytes
        def changed(path):
            return previous if path == image else original(path)
        monkeypatch.setattr(Path, 'read_bytes', changed)
    with operations.worker_operation():
        sa.main()
    result = store.get(op['id'])
    assert result['status'] == 'failed'
    assert result['error']['code'] == 'analysis_incomplete'
    state = json.loads(Path(result['result_refs']['screen_analysis.json']['path']).read_text())
    assert state['status'] == 'error'
    expected_message = {
        'encode': 'Screen analysis screenshot could not be encoded',
        'write': 'PNG write denied',
        'short_write': 'Screen analysis screenshot readback failed',
        'readback': 'Screen analysis screenshot readback failed',
    }[fault]
    assert state['message'] == expected_message
    assert 'screenshot' not in state
    assert state['operation_id'] == op['id']
    if fault in ('write', 'encode'):
        assert image.read_bytes() == previous


def test_analysis_error_retains_producer_image_evidence(analysis_job, monkeypatch):
    from vision import textocr
    operations, store, op, _, _ = analysis_job
    def fail(*args):
        raise RuntimeError('OCR failed')
    monkeypatch.setattr(textocr, 'read_lines', fail)
    with operations.worker_operation():
        sa.main()
    result = store.get(op['id'])
    assert result['status'] == 'failed'
    assert result['error'] == {'code': 'analysis_incomplete', 'message': 'OCR failed'}
    state = json.loads(Path(result['result_refs']['screen_analysis.json']['path']).read_text())
    assert state['screenshot']['operation_id'] == op['id']
    assert state['screenshot']['revision'] == result['result_refs']['screen_analysis.png']['revision']
