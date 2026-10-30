import yaml
from device import layout
from player.geometry import Display


def test_first_frame_selects_game_even_when_launcher_size_matches(monkeypatch):
    import struct
    import settings
    from device import capture, adbclient
    inst = {'serial':'test-device'}
    monkeypatch.setattr(settings, 'instance', lambda: inst)
    monkeypatch.setattr(capture, '_resolved_serial', None)
    monkeypatch.setattr(capture, 'CONFIG', {'screen':{'width':20,'height':20}})
    probes, commands = [], []
    def resolve(serial):
        probes.append(serial)
        inst.update(display='123', input_display=2)
        return '123'
    monkeypatch.setattr(capture, 'refresh_display', resolve)
    def frame(serial, command, **kwargs):
        commands.append(command)
        return struct.pack('<III',20,20,1) + bytes(20*20*4)
    monkeypatch.setattr(adbclient, 'exec_out', frame)
    assert capture.grab().shape == (20,20,3)
    capture.grab()
    assert probes == ['test-device']
    assert commands == ['screencap -d 123','screencap -d 123']


def setup(monkeypatch,tmp_path,rendering=None):
    import settings
    p=tmp_path/'config.yaml'
    cfg={'instances':{'main':{'rendering':rendering}},'active_instance':'main','loadouts':{'preserve':{}}}
    p.write_text(yaml.safe_dump(cfg))
    monkeypatch.setattr(settings,'CONFIG_PATH',p)
    monkeypatch.setattr(settings,'CONFIG',cfg)
    monkeypatch.setattr(layout,'discover',lambda:Display(1080,1920,280))
    monkeypatch.setattr(layout,'activate',lambda d:None)
    return p


def test_unchanged_display_does_not_replace_config(monkeypatch,tmp_path):
    p=setup(monkeypatch,tmp_path,{'width':1080,'height':1920,'dpi':280})
    before=p.read_bytes()
    monkeypatch.setattr('os.replace',lambda *a: (_ for _ in ()).throw(AssertionError('unexpected write')))
    assert layout.prepare_scan()==Display(1080,1920,280)
    assert p.read_bytes()==before


def test_display_change_retries_windows_reader_lock(monkeypatch,tmp_path):
    import os
    p=setup(monkeypatch,tmp_path)
    real=os.replace; attempts=[]
    def locked_once(*args):
        attempts.append(args)
        if len(attempts)==1: raise PermissionError('reader lock')
        return real(*args)
    monkeypatch.setattr(os,'replace',locked_once)
    layout.prepare_scan()
    saved=yaml.safe_load(p.read_text())
    assert len(attempts)==2
    assert saved['instances']['main']['rendering']['height']==1920
    assert saved['loadouts']=={'preserve':{}}


def test_native_discovery_does_not_disable_regular_capture_lock(monkeypatch):
    import struct
    import pytest
    from device import capture, adbclient
    raw=struct.pack('<III',100,200,1)+bytes([10,20,30,255])*20000
    monkeypatch.setattr(adbclient,'exec_out',lambda *a,**k:b'[Warning] Multiple displays\n'+raw)
    monkeypatch.setattr(capture,'CONFIG',{'screen':{'width':1080,'height':2560}})
    frame=capture.grab(serial='test',discover=True)
    assert frame.shape==(200,100,3)
    with pytest.raises(capture.CaptureError,match='unexpected resolution'):
        capture.grab(serial='test')
