"""Completed operation evidence survives later scans and manual repairs."""
import json
from pathlib import Path

import pytest

from control import operations as service
from runtime import operations


def accepted(tmp_path, monkeypatch, kind='calibrate'):
    cfg = {'active_instance': 'main', 'instances': {'main': {'serial': 'fake:1'}}}
    context = service.context(tmp_path, cfg, {}, None)
    store = operations.Operations(tmp_path / 'operations.db')
    op, _ = store.accept(kind, context, {'phases': 'c'})
    monkeypatch.setenv('TOWER_OPERATION_ID', op['id'])
    monkeypatch.setenv('TOWER_OPERATION_DB', store.path)
    folder = Path(context['artifact_dir'])
    folder.mkdir(parents=True)
    return store, op, folder


@pytest.mark.parametrize('kind', ['calibrate', 'observe', 'scan', 'screen_analysis'])
def test_completed_evidence_survives_working_files_replaced(tmp_path, monkeypatch, kind):
    store, op, folder = accepted(tmp_path, monkeypatch, kind)
    name = 'scan' if kind == 'scan' else 'calibrate'
    state_name = 'screen_analysis.json' if kind == 'screen_analysis' else f'{name}_state.json'
    other_name = 'screen_analysis.png' if kind == 'screen_analysis' else f'{name}_report.json'
    state = {'operation_id': op['id'], 'status': 'done',
             'phases': {'cards': {'status': 'done'}}, 'player': {'cards': ['observed']},
             'final_screen': {'name': 'home', 'verified': True, 'operation_id': op['id']}}
    original = {state_name: json.dumps(state).encode(), other_name: b'original evidence'}
    with operations.worker_operation():
        if kind == 'screen_analysis':
            import numpy as np
            from player import screen_analysis
            state['screenshot'] = screen_analysis._save_screenshot(
                folder / other_name, np.zeros((24, 24, 3), dtype=np.uint8))
            original = {state_name: json.dumps(state).encode(),
                        other_name: (folder / other_name).read_bytes()}
        for filename, raw in original.items():
            (folder / filename).write_bytes(raw)
    completed = store.get(op['id'])
    assert completed['status'] == ('awaiting_apply' if kind in ('calibrate', 'observe') else 'succeeded')
    assert completed['error'] is None
    for filename, raw in original.items():
        (folder / filename).write_bytes(b'later scan or approved crop')
        reference = completed['result_refs'][filename]
        saved = Path(reference['path'])
        assert saved.parent == folder / 'operations' / op['id']
        assert saved.read_bytes() == raw
        assert operations.file_revision(saved) == reference['revision']


@pytest.mark.parametrize('fault', ['write_denied', 'readback_changed', 'existing_evidence'])
def test_snapshot_failure_cannot_publish_capture_success(tmp_path, monkeypatch, fault):
    store, op, folder = accepted(tmp_path, monkeypatch)
    destination = folder / 'operations' / op['id'] / 'calibrate_state.json'
    if fault == 'write_denied':
        original = Path.open
        def guarded(path, *args, **kwargs):
            if path == destination:
                raise PermissionError('snapshot write denied')
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'open', guarded)
    elif fault == 'readback_changed':
        monkeypatch.setattr(operations, 'file_revision', lambda path: 'wrong digest')
    else:
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b'preserved original')
    with operations.worker_operation():
        (folder / 'calibrate_state.json').write_text(json.dumps({
            'operation_id': op['id'], 'phases': {'cards': {'status': 'done'}},
            'final_screen': {'name': 'home', 'verified': True, 'operation_id': op['id']},
        }))
    result = store.get(op['id'])
    assert result['status'] == 'failed'
    assert result['error']['code'] == 'invalid_artifact'
    assert 'calibrate_state.json' not in result['result_refs']
    if fault == 'existing_evidence':
        assert destination.read_bytes() == b'preserved original'


def test_invalid_state_is_retained_as_failed_evidence(tmp_path, monkeypatch):
    store, op, folder = accepted(tmp_path, monkeypatch)
    with operations.worker_operation():
        (folder / 'calibrate_state.json').write_bytes(b'[]')
    result = store.get(op['id'])
    assert result['status'] == 'failed'
    assert result['error']['code'] == 'invalid_artifact'
    assert Path(result['result_refs']['calibrate_state.json']['path']).read_bytes() == b'[]'


@pytest.mark.parametrize('fault', [
    'missing_png', 'missing_reference', 'invalid_reference', 'foreign_reference',
    'foreign_state', 'wrong_path', 'wrong_digest', 'changed_png', 'old_png_and_reference',
])
def test_screen_analysis_rejects_unbound_image(tmp_path, monkeypatch, fault):
    import numpy as np
    from player import screen_analysis
    store, op, folder = accepted(tmp_path, monkeypatch, 'screen_analysis')
    image = folder / 'screen_analysis.png'
    with operations.worker_operation():
        screenshot = screen_analysis._save_screenshot(image, np.zeros((24, 24, 3), dtype=np.uint8))
        state = {'operation_id': op['id'], 'status': 'done', 'screenshot': screenshot}
        if fault == 'missing_png':
            image.unlink()
        elif fault == 'missing_reference':
            del state['screenshot']
        elif fault == 'invalid_reference':
            state['screenshot'] = []
        elif fault in ('foreign_reference', 'old_png_and_reference'):
            screenshot['operation_id'] = 'previous-operation'
        elif fault == 'foreign_state':
            state['operation_id'] = 'previous-operation'
        elif fault == 'wrong_path':
            screenshot['path'] = str(folder / 'previous.png')
        elif fault == 'wrong_digest':
            screenshot['revision'] = 'wrong-digest'
        elif fault == 'changed_png':
            image.write_bytes(b'previous-operation-image')
        raw = json.dumps(state).encode()
        (folder / 'screen_analysis.json').write_bytes(raw)
    result = store.get(op['id'])
    assert result['status'] == 'failed'
    assert result['error']['code'] == (
        'missing_capture_outcome' if fault == 'foreign_state' else 'analysis_image_unverified')
    assert Path(result['result_refs']['screen_analysis.json']['path']).read_bytes() == raw
    if image.exists():
        saved = result['result_refs']['screen_analysis.png']
        assert Path(saved['path']).read_bytes() == image.read_bytes()
        assert saved['revision'] == operations.file_revision(image)
    else:
        assert 'screen_analysis.png' not in result['result_refs']


@pytest.mark.parametrize('fault', ['write_denied', 'readback_changed', 'existing_evidence'])
def test_screen_analysis_snapshot_failure_preserves_json(tmp_path, monkeypatch, fault):
    import numpy as np
    from player import screen_analysis
    store, op, folder = accepted(tmp_path, monkeypatch, 'screen_analysis')
    destination = folder / 'operations' / op['id'] / 'screen_analysis.png'
    with operations.worker_operation():
        screenshot = screen_analysis._save_screenshot(
            folder / 'screen_analysis.png', np.zeros((24, 24, 3), dtype=np.uint8))
        screen_analysis._write_state(folder / 'screen_analysis.json', {
            'status': 'done', 'screenshot': screenshot})
        if fault == 'write_denied':
            original = Path.open
            def guarded(path, *args, **kwargs):
                if path == destination:
                    raise PermissionError('snapshot write denied')
                return original(path, *args, **kwargs)
            monkeypatch.setattr(Path, 'open', guarded)
        elif fault == 'readback_changed':
            original = operations.file_revision
            monkeypatch.setattr(operations, 'file_revision',
                                lambda path: 'wrong digest' if path == destination else original(path))
        else:
            destination.parent.mkdir(parents=True)
            destination.write_bytes(b'preserved original')
    result = store.get(op['id'])
    assert result['status'] == 'failed'
    assert result['error']['code'] == 'invalid_artifact'
    assert 'screen_analysis.json' in result['result_refs']
    assert 'screen_analysis.png' not in result['result_refs']
    if fault == 'existing_evidence':
        assert destination.read_bytes() == b'preserved original'


def test_prior_successful_analysis_image_cannot_complete_new_operation(tmp_path, monkeypatch):
    import numpy as np
    from player import screen_analysis
    store, prior, folder = accepted(tmp_path, monkeypatch, 'screen_analysis')
    with operations.worker_operation():
        screenshot = screen_analysis._save_screenshot(
            folder / 'screen_analysis.png', np.zeros((24, 24, 3), dtype=np.uint8))
        screen_analysis._write_state(folder / 'screen_analysis.json', {
            'status': 'done', 'screenshot': screenshot})
    prior_result = store.get(prior['id'])
    assert prior_result['status'] == 'succeeded'
    current, _ = store.accept('screen_analysis', prior['context'], {})
    monkeypatch.setenv('TOWER_OPERATION_ID', current['id'])
    with operations.worker_operation():
        screen_analysis._write_state(folder / 'screen_analysis.json', {
            'status': 'done', 'screenshot': screenshot})
    result = store.get(current['id'])
    assert result['status'] == 'failed'
    assert result['error']['code'] == 'analysis_image_unverified'
    assert store.get(prior['id']) == prior_result
    assert operations.file_revision(prior_result['result_refs']['screen_analysis.png']['path']) == screenshot['revision']


def test_screen_image_validation_preserves_primary_worker_error(tmp_path, monkeypatch):
    store, op, folder = accepted(tmp_path, monkeypatch, 'screen_analysis')
    with pytest.raises(RuntimeError, match='primary failure'):
        with operations.worker_operation():
            (folder / 'screen_analysis.json').write_text(json.dumps({
                'operation_id': op['id'], 'status': 'done'}))
            raise RuntimeError('primary failure')
    result = store.get(op['id'])
    assert result['status'] == 'failed'
    assert result['error'] == {'code': 'RuntimeError', 'message': 'primary failure'}
    assert 'screen_analysis.json' in result['result_refs']


def test_screen_image_validation_preserves_cancellation(tmp_path, monkeypatch):
    store, op, folder = accepted(tmp_path, monkeypatch, 'screen_analysis')
    with operations.worker_operation():
        (folder / 'screen_analysis.json').write_text(json.dumps({
            'operation_id': op['id'], 'status': 'done'}))
        current = store.get(op['id'])
        store.transition(op['id'], current['revision'], 'cancelling')
    result = store.get(op['id'])
    assert result['status'] == 'cancelled'
    assert result['error']['code'] == 'analysis_image_unverified'
    assert 'screen_analysis.json' in result['result_refs']
