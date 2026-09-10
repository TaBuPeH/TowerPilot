"""A collection-only worker cannot equip or start a farming run."""
import importlib.util
from pathlib import Path
import types


def dashboard():
    path=Path(__file__).resolve().parents[2]/'frontend/dashboard.py'
    spec=importlib.util.spec_from_file_location('dashboard_gems_test',path)
    d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
    return d


def test_collection_start_rejects_existing_worker(monkeypatch):
    d=dashboard()
    monkeypatch.setattr(d,'load_config',lambda:{'active_instance':'main'})
    monkeypatch.setattr(d,'_procs',lambda:[{'runner':'gem_collector'}])
    assert d.app.test_client().post('/api/control',json={'action':'start_gems','preset':'bp_farm'}).status_code==409


def test_collection_start_uses_only_collector_script(monkeypatch):
    d=dashboard()
    cfg={'active_instance':'main','instances':{'main':{'allow_taps':True}}}
    monkeypatch.setattr(d,'load_config',lambda:cfg)
    monkeypatch.setattr(d,'_procs',lambda:[])
    monkeypatch.setattr(d,'_compiled_runs',lambda c:{'bp_farm':{'gather':{'ad_gems':False,'gem_orbit':{'enabled':True}}}})
    monkeypatch.setattr(d,'_procs_refresh',lambda:None)
    cmds=[]
    monkeypatch.setattr(d.subprocess,'Popen',lambda cmd,**kw:cmds.append(cmd) or types.SimpleNamespace(pid=123,poll=lambda:None))
    import time
    monkeypatch.setattr(time,'sleep',lambda t:None)
    r=d.app.test_client().post('/api/control',json={'action':'start_gems','preset':'bp_farm'})
    assert r.status_code==200
    assert Path(cmds[0][1]).name=='gem_collector.py'
    assert cmds[0][-2:]==['--preset','bp_farm']
    assert '--tier' not in cmds[0]
