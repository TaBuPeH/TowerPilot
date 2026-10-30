import io
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile
import cv2
import numpy as np
import pytest
from PIL import Image
from player import asset_library as assets, asset_verify


def archive(entries):
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as z:
        for name,data in entries:z.writestr(name,data)
    buffer.seek(0)
    return zipfile.ZipFile(buffer)


def test_unity_split_files_are_reassembled_numerically_and_non_assets_ignored():
    entries=[('classes.dex',b'ignore'),('assets/bin/Data/Managed/a.dll',b'ignore')]
    entries += [(f'assets/bin/Data/main.assets.split{i}',bytes([i])) for i in [10,3,2,0,1,4,5,6,7,8,9]]
    assert list(assets.unity_members(archive(entries)))==[('assets/bin/Data/main.assets',bytes(range(11)))]


@pytest.mark.parametrize('entries',[
    [('assets/bin/Data/a.split0',b'0'),('assets/bin/Data/a.split2',b'2')],
    [('assets/bin/Data/../../escape',b'x')],
    [('assets/bin/Data/a',b'a'),('assets/bin/Data/a.split0',b'0')],
])
def test_incomplete_or_unsafe_archive_is_rejected(entries):
    with pytest.raises(ValueError):list(assets.unity_members(archive(entries)))


def test_objects_with_duplicate_names_and_ids_keep_distinct_identity_and_safe_paths(tmp_path):
    def obj(source,color):
        return SimpleNamespace(type=SimpleNamespace(name='Sprite'),assets_file=SimpleNamespace(name=source),
            path_id=2,peek_name=lambda:'../../outside',parse_as_object=lambda:SimpleNamespace(image=Image.new('RGBA',(24,24),color)))
    env=SimpleNamespace(objects=[obj('one','red'),obj('two','blue')])
    records,errors=assets.export_images(env,tmp_path,lambda *a,**k:None,lambda:None)
    assert not errors and len({r['id'] for r in records})==2
    assert len({r['file'] for r in records})==2
    assert all((tmp_path/r['file']).resolve().is_relative_to(tmp_path.resolve()) for r in records)
    assert all(r['name']=='../../outside' for r in records)
    index={'schema':assets.SCHEMA,'decoder':assets.DECODER_VERSION,'images':records}
    assert assets.valid_index(tmp_path,index)
    (tmp_path/records[0]['file']).write_bytes(b'corrupt')
    assert not assets.valid_index(tmp_path,index)


def test_mapping_is_exact_and_does_not_claim_screen_verification_or_ownership():
    index={'images':[{'name':'Guild Icon','type':'Sprite','sha256':'a','id':'one'},
                     {'name':'Guild Icon','type':'Texture2D','sha256':'b','id':'two'},
                     {'name':'Guild Icon 2','type':'Sprite','sha256':'c','id':'three'}]}
    mapped=assets.map_targets(index,{'home/tile_guild.png':{'names':['Guild Icon'],'screen':'home'},
                                    'icons/chest_lock.png':{'names':['lock'],'screen':'missions'}})
    assert mapped['home/tile_guild.png']['candidates']==[index['images'][0]]
    assert mapped['home/tile_guild.png']['verification']=='pending'
    assert mapped['icons/chest_lock.png']['status']=='missing_source'
    assert 'player' not in mapped and 'owned' not in str(mapped)


def test_installed_package_identity_changes_with_game_version_and_emulator():
    def shell(serial,cmd):
        if cmd.startswith('pm path'):return b'package:/data/app/package/base.apk\n'
        if cmd.startswith('stat'):return b'100'
        return b'versionCode=7 versionName=1.2.3'
    a=assets.installed('one',shell);b=assets.installed('two',shell)
    assert a['key']!=b['key'] and a['version']=='1.2.3'
    with pytest.raises(RuntimeError):
        assets.installed('one',lambda *a:b'package:/sdcard/other.apk')


def test_source_art_locates_a_shifted_native_icon_but_rejects_unrelated_art():
    rng=np.random.default_rng(65)
    icon=np.zeros((150,150,4),np.uint8)
    for _ in range(110):
        cv2.circle(icon,tuple(map(int,rng.integers(10,140,2))),int(rng.integers(2,8)),(*map(int,rng.integers(80,255,3)),255),-1)
    frame=np.zeros((500,500,3),np.uint8)
    frame[270:390,210:330]=cv2.resize(icon[:,:,:3],(120,120))
    hit=asset_verify.locate(icon,frame,[50,50,400,400])
    assert hit and abs(hit['rect'][0]-210)<3 and abs(hit['rect'][1]-270)<3
    assert hit['inliers']>=8
    other=rng.integers(0,255,icon.shape,dtype=np.uint8)
    assert asset_verify.locate(other,frame,[50,50,400,400]) is None


def test_stop_interrupts_extraction_before_decoding():
    def stopped():raise RuntimeError('stopped')
    env=SimpleNamespace(objects=[SimpleNamespace(type=SimpleNamespace(name='Sprite'))])
    # No real asset data is read after cancellation.
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as d,pytest.raises(RuntimeError,match='stopped'):
        assets.export_images(env,Path(d),lambda *a,**k:None,stopped)


def test_binary_package_stream_is_cancellable_and_closes_socket(monkeypatch):
    from device import adbclient
    payload=b'OKAYOKAY'+b'A'*(1<<20)+b'B'*20
    class Socket:
        def __init__(self):self.stream=io.BytesIO(payload);self.closed=False;self.sent=[]
        def __enter__(self):return self
        def __exit__(self,*args):self.closed=True
        def settimeout(self,value):pass
        def sendall(self,data):self.sent.append(data)
        def recv(self,size):return self.stream.read(size)
    sock=Socket();monkeypatch.setattr(adbclient.socket,'create_connection',lambda *a,**k:sock)
    sink=io.BytesIO();seen=[]
    def stop():
        if seen:raise RuntimeError('stop requested')
    with pytest.raises(RuntimeError,match='stop requested'):
        adbclient.exec_to('test','cat /data/app/game/base.apk',sink,progress=seen.append,check_stop=stop)
    assert sock.closed and len(sink.getvalue())==1<<20
    assert b'host:transport:test' in sock.sent[0]
    assert b'exec:cat /data/app/game/base.apk' in sock.sent[1]


def test_empty_font_placeholders_do_not_attempt_a_pixel_decode(tmp_path):
    class Empty:
        m_Width=0
        m_Height=0
        @property
        def image(self):pytest.fail('Empty placeholder has no source image to decode')
    empty=SimpleNamespace(type=SimpleNamespace(name='Texture2D'),assets_file=SimpleNamespace(name='a'),path_id=1,
        peek_name=lambda:'Font Texture',parse_as_object=lambda:Empty())
    valid=SimpleNamespace(type=SimpleNamespace(name='Sprite'),assets_file=SimpleNamespace(name='a'),path_id=2,
        peek_name=lambda:'Icon',parse_as_object=lambda:SimpleNamespace(image=Image.new('RGBA',(10,10),'blue')))
    rows,errors=assets.export_images(SimpleNamespace(objects=[empty,valid]),tmp_path,lambda *a,**k:None,lambda:None)
    assert len(rows)==1 and len(errors)==1
    assert errors[0]['kind']=='empty_placeholder'


def test_checked_cache_reuses_art_without_running_decoder(tmp_path,monkeypatch):
    identity={'key':'test','apks':[],'version':'1','package':assets.PACKAGE}
    folder=tmp_path/'asset_library/test';folder.mkdir(parents=True)
    index={'installation':identity,'images':[{'id':'one'}]}
    assets.atomic_json(folder/'index.json',index)
    monkeypatch.setattr(assets,'installed',lambda serial:identity)
    monkeypatch.setattr(assets,'valid_index',lambda *a:True)
    monkeypatch.setattr(assets,'load_unity',lambda *a:pytest.fail('Valid cache must not decode again'))
    messages=[]
    result,loaded=assets.acquire(tmp_path,'test',lambda text,**kw:messages.append(text),lambda:None)
    assert result==folder and loaded==index
    assert any('verified local' in m for m in messages)
