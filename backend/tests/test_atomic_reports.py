import json
import pytest
from runtime import files


def test_transient_reader_lock_retries_then_publishes(tmp_path,monkeypatch):
    path=tmp_path/'report.json'
    path.write_text('{"old":true}')
    original=files.os.replace
    attempts=[]
    def replace(source,destination):
        attempts.append(1)
        if len(attempts)<3:
            assert json.loads(path.read_text())=={'old':True}
            raise PermissionError('reader holds file')
        original(source,destination)
    monkeypatch.setattr(files.os,'replace',replace)
    monkeypatch.setattr(files.time,'sleep',lambda _:None)
    files.atomic_json(path,{'new':True})
    assert len(attempts)==3 and json.loads(path.read_text())=={'new':True}
    assert not list(tmp_path.glob('*.tmp'))


def test_permanent_failure_keeps_last_valid_report(tmp_path,monkeypatch):
    path=tmp_path/'report.json'
    path.write_text('{"old":true}')
    def refuse(*args): raise PermissionError('locked')
    monkeypatch.setattr(files.os,'replace',refuse)
    monkeypatch.setattr(files.time,'sleep',lambda _:None)
    with pytest.raises(PermissionError): files.atomic_json(path,{'new':True})
    assert json.loads(path.read_text())=={'old':True}
    assert not list(tmp_path.glob('*.tmp'))
