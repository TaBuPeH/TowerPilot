import numpy as np
from player import clicker


def fixture():
    rng = np.random.default_rng(42)
    template = rng.integers(20, 240, (16, 16, 3), dtype=np.uint8)
    frame = np.zeros((2560, 1080, 3), np.uint8)
    frame[600:616, 300:316] = template
    return frame, template


def test_match_is_local_and_rejects_dimmed_parent():
    frame, template = fixture()
    assert clicker.locate(frame, template, [300,600,16,16])["ok"]
    assert not clicker.locate(frame, template, [600,600,16,16])["ok"]
    assert not clicker.locate(frame//2, template, [300,600,16,16])["ok"]
    assert not clicker.locate(frame[:1000], template, [300,600,16,16])["ok"]


def test_duplicate_target_is_refused():
    frame, template = fixture()
    frame[600:616, 322:338] = template
    assert not clicker.locate(frame, template, [300,600,16,16])["ok"]


def test_dynamic_search_finds_moved_icon_and_refuses_duplicates():
    frame, template = fixture()
    assert clicker.locate(frame, template, [900,100,16,16], search=[0,0,1080,1100])["ok"]
    frame[800:816, 900:916] = template
    assert not clicker.locate(frame, template, [900,100,16,16], search=[0,0,1080,1100])["ok"]


def test_only_matching_screen_uses_learned_position():
    frame, template = fixture()
    action = clicker.ACTIONS["guild"]
    learned = {action["target"]:{"screen":"home", "rect":[300,600,16,16]}}
    result = clicker.inspect(frame, action, learned, lambda _:template, lambda _:"home")
    assert result["ok"] and result["position_source"] == "learned"
    assert not clicker.inspect(frame, action, learned, lambda _:template, lambda _:"battle")["ok"]
    learned[action["target"]]["screen"] = "cards"
    assert not clicker.inspect(frame, action, learned, lambda _:template, lambda _:"home")["ok"]


def test_second_frame_change_never_clicks_and_uncertain_result_never_retries():
    frame, template = fixture()
    action = clicker.ACTIONS["guild"]
    learned = {action["target"]:{"screen":"home", "rect":[300,600,16,16]}}
    names = iter(["home", "battle"])
    taps = []
    result = clicker.perform(action, learned, lambda:frame, lambda _:template,
        lambda _:next(names), lambda *a,**kw:taps.append(a), True, lambda _:None)
    assert not result["ok"] and not taps
    names = iter(["home", "home", "unknown"])
    def tap(*a, **kw):
        taps.append(a)
        return {"dry_run":False}
    result = clicker.perform(action, learned, lambda:frame, lambda _:template,
        lambda _:next(names), tap, True, lambda _:None)
    assert result["clicked"] and not result["ok"] and len(taps)==1


def test_preview_never_taps():
    frame, template = fixture()
    action = clicker.ACTIONS["guild"]
    learned = {action["target"]:{"screen":"home", "rect":[300,600,16,16]}}
    def forbidden(*a, **kw):
        raise AssertionError("preview tapped")
    assert clicker.perform(action, learned, lambda:frame, lambda _:template,
        lambda _:"home", forbidden)["ok"]


def test_dashboard_rejects_other_account_and_running_worker(monkeypatch):
    import importlib.util
    from pathlib import Path
    path=Path(__file__).resolve().parents[2]/'frontend'/'dashboard.py'
    spec=importlib.util.spec_from_file_location('clicker_dashboard_test',path)
    dash=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dash)
    monkeypatch.setattr(dash,'load_config',lambda:{'active_instance':'main','instances':{'main':{'account':'alice','allow_taps':True}}})
    monkeypatch.setattr(dash,'_procs',lambda:[{'runner':'calibrate'}])
    with dash.app.test_client() as client:
        assert client.post('/api/clicker/step',json={'action':'guild','instance':'main','account':'bob'}).status_code==400
        assert client.post('/api/clicker/step',json={'action':'guild','instance':'main','account':'alice'}).status_code==409
        # Rejections release the mutation lock; read-only endpoints stay usable.
        assert not dash._ACTION_LOCK.locked()
        assert client.get('/api/clicker/actions').status_code==200
