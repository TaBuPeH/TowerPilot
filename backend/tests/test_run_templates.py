"""Generic legacy recipes: isolation, validation, compilation and persistence."""
import copy
import sys
from pathlib import Path
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from player import playerprofile as pp, run_templates as rt

@pytest.fixture
def profile(monkeypatch):
    monkeypatch.setattr(pp, 'CONFIG', yaml.safe_load((ROOT/'backend/config.example.yaml').read_text(encoding='utf8')))
    p=yaml.safe_load((ROOT/'backend/profiles/default.yaml').read_text(encoding='utf8'))
    p['blueprints']={}
    p.pop('plan',None)
    return p

@pytest.mark.parametrize('template,kind',[('farm','coin'),('tournament','tournament'),('shard','shard')])
def test_generic_recipe_compiles_without_claiming_ownership(profile,template,kind):
    before=copy.deepcopy(profile)
    result=rt.instantiate(profile,template,'new_run')
    assert profile==before
    assert result['player']==before['player']
    assert pp.validate(result)==[]
    compiled=pp.compile_preset(result,'new_run')
    assert compiled['kind']==kind and compiled['tier']==1
    assert not compiled['uw_wanted'] and not compiled['rules']
    assert 'plan' not in result
    if template=='tournament': assert compiled['gem_entry_max']==0
    if template=='farm': assert compiled['shop_interval_sec']==90 and compiled['shopping']
    if template=='shard': assert '100' in str(compiled['runner_args'])

def test_each_run_has_independent_policies(profile):
    a=rt.instantiate(profile,'farm','one')
    b=rt.instantiate(a,'farm','two',{'label':'Other farm'})
    assert b['blueprints']['one']==a['blueprints']['one']
    first=b['blueprints']['one']['shopping'];second=b['blueprints']['two']['shopping']
    assert first!=second
    b['policies']['shopping_lists'][second].clear()
    assert b['policies']['shopping_lists'][first]

@pytest.mark.parametrize('options',[{'enable_uw':True},{'enable_rescue':True}])
def test_optional_legacy_rules_require_real_account_capabilities(profile,options):
    result=rt.instantiate(profile,'farm','new_run',options)
    assert pp.validate(result) or pp.warnings(result)

def test_legacy_timing_preserved():
    templates={t['id']:t for t in rt.catalogue()['templates']}
    for name,ref,window in [('farm','farm_cl_choreo',[4080,4120]),('tournament','tourney_cl',[500,550])]:
        assert templates[name]['policies']['uw_policies'][ref]['chain_lightning']['always_on_above' if name=='farm' else 'on_above']==window

@pytest.mark.parametrize('template,name,options',[('missing','run',{}),('farm','../run',{}),('farm','run',{'count':1}),('farm','run',{'enable_uw':'yes'}),('shard','run',{'enable_rescue':True})])
def test_bad_choices_refused(profile,template,name,options):
    with pytest.raises(ValueError):rt.instantiate(profile,template,name,options)

def test_duplicate_refused(profile):
    p=rt.instantiate(profile,'farm','run')
    with pytest.raises(ValueError):rt.instantiate(p,'farm','run')

def test_api_validates_before_writing_and_preserves_existing_runs(profile,tmp_path,monkeypatch):
    sys.path.insert(0,str(ROOT/'frontend'))
    import dashboard as d
    monkeypatch.setattr(d,'_profiles_dir',lambda:str(tmp_path))
    monkeypatch.setattr(d,'load_config',lambda:pp.CONFIG)
    target=tmp_path/'test.yaml'
    target.write_text(yaml.safe_dump(profile),encoding='utf8')
    client=d.app.test_client()
    body={'profile':'test','template':'farm','run_id':'farm_run','options':{'tier':-1}}
    before=target.read_bytes()
    assert client.post('/api/run-templates/add',json=body).status_code==400
    assert target.read_bytes()==before
    body['options']={'tier':1}
    assert client.post('/api/run-templates/add',json=body).status_code==200
    saved=yaml.safe_load(target.read_text(encoding='utf8'))
    assert saved['player']==profile['player'] and 'farm_run' in saved['blueprints']
    assert client.post('/api/run-templates/add',json=body).status_code==400
    assert yaml.safe_load(target.read_text(encoding='utf8'))==saved
    body['profile']='default'
    assert client.post('/api/run-templates/add',json=body).status_code==409
    assert len(client.get('/api/run-templates').json['templates'])==3


def test_shard_uses_blueprint_equipment_not_legacy_name(monkeypatch):
    from flows import shard
    monkeypatch.setitem(shard.CONFIG,'preset','bp_custom')
    monkeypatch.setitem(shard.CONFIG,'presets',{'bp_custom':{'loadout':'my_equipment'}})
    assert shard.configured_loadout()=='my_equipment'
    called=[]
    monkeypatch.setattr(shard.loadout,'spec',lambda name: called.append(('validate',name)) or {})
    monkeypatch.setattr(shard.loadout,'apply',lambda name:called.append(('apply',name)))
    monkeypatch.setattr(shard,'ensure_home',lambda:None)
    monkeypatch.setattr(shard,'set_tier',lambda tier:None)
    monkeypatch.setattr(shard.capture,'grab',lambda:None)
    monkeypatch.setattr(shard,'on_home',lambda frame:True)
    monkeypatch.setattr(shard,'start_battle',lambda:None)
    monkeypatch.setattr(shard.logger,'event',lambda *a,**k:None)
    shard.setup(1)
    assert called==[('validate','my_equipment'),('apply','my_equipment')]


def test_shard_missing_equipment_refuses_before_navigation(monkeypatch):
    from flows import shard
    monkeypatch.setitem(shard.CONFIG,'preset','bp_missing')
    monkeypatch.setitem(shard.CONFIG,'presets',{'bp_missing':{}})
    monkeypatch.setattr(shard,'ensure_home',lambda:pytest.fail('must not navigate'))
    with pytest.raises(shard.Abort):shard.setup(1)

@pytest.mark.parametrize('cancel_ok,nuke_ok,expected',[(False,True,'cancel'),(True,False,'nuke'),(True,True,'done')])
def test_shard_sequence_refuses_to_continue_after_failed_action(monkeypatch,cancel_ok,nuke_ok,expected):
    from flows import shard
    calls=[]
    monkeypatch.setattr(shard.logger,'shot',lambda *a,**k:None)
    monkeypatch.setattr(shard.logger,'event',lambda *a,**k:None)
    monkeypatch.setattr(shard,'wait_for_wave',lambda wave,**kw: (calls.append(wave) or (None,wave)))
    monkeypatch.setattr(shard,'ensure_max_speed',lambda:None)
    monkeypatch.setattr(shard,'cancel_sprint',lambda: calls.append('cancel') or cancel_ok)
    monkeypatch.setattr(shard,'wait_for_nuke_point',lambda **kw:calls.append('point'))
    monkeypatch.setattr(shard,'fire_nuke',lambda frame:calls.append('nuke') or nuke_ok)
    monkeypatch.setattr(shard.time,'sleep',lambda sec:None)
    monkeypatch.setattr(shard,'abandon_run',lambda **kw:calls.append('exit'))
    if expected=='done':shard.one_loop(1,last=True)
    else:
        with pytest.raises(shard.Abort):shard.one_loop(1,last=True)
    assert calls[:3]==[1,100,'cancel']
    assert ('exit' in calls)==(expected=='done')
    assert ('nuke' in calls)==cancel_ok
    assert shard.NUKE_WAVE==101 and shard.NUKE_AT_PROGRESS==.10
