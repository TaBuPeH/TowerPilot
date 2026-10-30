"""A collection-only worker cannot equip or start a farming run."""

import paths as test_paths
import importlib.util
from pathlib import Path
import types
import pytest


def dashboard():
    path=test_paths.DASHBOARD
    spec=importlib.util.spec_from_file_location('dashboard_gems_test',path)
    d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
    return d


def test_collection_start_rejects_existing_worker(monkeypatch):
    d=dashboard()
    monkeypatch.setattr(d,'load_config',lambda:{'active_instance':'main'})
    monkeypatch.setattr(d,'_procs',lambda:[{'runner':'gem_collector'}])
    assert d.app.test_client().post('/api/control',json={'action':'start_gems','preset':'bp_farm'}).status_code==409


def test_collection_start_uses_only_collector_script(monkeypatch, tmp_path):
    d=dashboard()
    monkeypatch.setattr(d, 'ROOT', str(tmp_path))
    cfg={'active_instance':'main','instances':{'main':{'allow_taps':True,'serial':'test-device'}}}
    monkeypatch.setattr(d,'load_config',lambda:cfg)
    monkeypatch.setattr(d,'_procs',lambda:[])
    monkeypatch.setattr(d,'_compiled_runs',lambda c:{'bp_farm':{'gather':{'ad_gems':False,'gem_orbit':{'enabled':True}}}})
    monkeypatch.setattr(d,'_procs_refresh',lambda:None)
    from player import accounts
    import cv2
    import numpy as np
    for digit in range(10):
        cv2.imwrite(str(tmp_path / f'{digit}.png'), np.full((32, 16), digit * 20, np.uint8))
    monkeypatch.setattr(accounts, 'template_path', lambda root, cfg, rel: tmp_path / Path(rel).name)
    cmds=[]
    monkeypatch.setattr(d.subprocess,'Popen',lambda cmd,**kw:cmds.append(cmd) or types.SimpleNamespace(pid=123,poll=lambda:None))
    import time
    monkeypatch.setattr(time,'sleep',lambda t:None)
    r=d.app.test_client().post('/api/control',json={'action':'start_gems','preset':'bp_farm'})
    assert r.status_code==200
    assert cmds[0][1:3] == ['-m', 'runtime.operation_worker']
    assert Path(cmds[0][3]).name=='gem_collector.py'
    assert cmds[0][-2:]==['--preset','bp_farm']
    assert '--tier' not in cmds[0]


def test_collection_accepts_verified_text_fallback_without_offer_template(monkeypatch, tmp_path):
    d=dashboard()
    monkeypatch.setattr(d, 'ROOT', str(tmp_path))
    cfg={'active_instance':'main','instances':{'main':{'allow_taps':True,'serial':'test-device'}}}
    monkeypatch.setattr(d,'load_config',lambda:cfg)
    monkeypatch.setattr(d,'_procs',lambda:[])
    monkeypatch.setattr(d,'_compiled_runs',lambda c:{'bp_farm':{'gather':{'ad_gems':True}}})
    monkeypatch.setattr(d,'_procs_refresh',lambda:None)
    from player import accounts
    import cv2
    import numpy as np
    for digit in range(10):
        cv2.imwrite(str(tmp_path / f'{digit}.png'), np.full((32, 16), digit * 20, np.uint8))
    monkeypatch.setattr(accounts, 'template_path', lambda root, cfg, rel: tmp_path / Path(rel).name)
    monkeypatch.setattr(d.subprocess,'Popen',lambda *a,**kw:types.SimpleNamespace(pid=123,poll=lambda:None))
    import time
    monkeypatch.setattr(time,'sleep',lambda t:None)
    client=d.app.test_client()
    assert client.post('/api/control',json={'action':'start_gems','preset':'bp_farm'}).status_code==200
    # That collector is an accepted operation and owns the emulator until it is retired; this test is about the
    # digit precheck, so end it the way a vanished worker ends (the owner refusal has its own tests).
    store=d.operation_api.ledger(d.ROOT)
    [accepted]=store.active()
    store.transition(accepted['id'], accepted['revision'], 'interrupted')
    (tmp_path / '9.png').unlink()
    result=client.post('/api/control',json={'action':'start_gems','preset':'bp_farm'})
    assert result.status_code==409
    assert 'wave digits' in result.json['error']


def test_collection_only_waits_through_menus_and_stops_at_run_boundary(monkeypatch):
    import gem_collector as worker
    from runtime import runners, device_lease
    monkeypatch.setattr(device_lease, 'prepare_instance', lambda name: None)
    frames=iter(['battle', 'exit_battle', 'battle', 'game_stats'])
    collected=[]
    events=[]
    monkeypatch.setattr(worker.settings, 'select_instance', lambda *a: None)
    monkeypatch.setattr(runners, 'other_runners', lambda: iter([]))
    monkeypatch.setattr(worker, 'GemWatch', lambda gather=None, **kw: types.SimpleNamespace(
        ad_gems=types.SimpleNamespace(enabled=True), orbit=types.SimpleNamespace(enabled=True),
        poll=lambda f, reads=None: collected.append(f)))
    monkeypatch.setattr(worker.capture, 'grab', lambda: next(frames))
    monkeypatch.setattr(worker.screen, 'identify', lambda f: types.SimpleNamespace(name=f))
    monkeypatch.setattr(worker.wave_reader, 'read_wave', lambda f: 100)
    monkeypatch.setattr(worker.time, 'sleep', lambda _: None)
    monkeypatch.setattr(worker.runflag, 'requested', lambda: 'finish this run')
    monkeypatch.setattr(worker.logger, 'event', lambda name, **kw: events.append((name, kw)))
    worker.main('main', 'bp_farm')
    assert collected == ['battle', 'battle']
    assert events[-1] == ('gem_collector_stopped', {'reason':'finish this run'})


def test_collection_cli_refuses_another_device_owner(monkeypatch):
    import gem_collector as worker
    from runtime import runners, device_lease
    monkeypatch.setattr(device_lease, 'prepare_instance', lambda name: None)
    monkeypatch.setattr(worker.settings, 'select_instance', lambda *a: None)
    monkeypatch.setattr(runners, 'other_runners', lambda: iter([(123, 'orchestrator.py', [])]))
    monkeypatch.setattr(worker.capture, 'grab', lambda: pytest.fail('capture while owned'))
    with pytest.raises(RuntimeError, match='Another runner'):
        worker.main('main', 'bp_farm')
