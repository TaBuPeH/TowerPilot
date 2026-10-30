import json
from player.manifest_contribution import export, apply_reference


def test_export_excludes_account_labels_paths_hashes_and_unknown_targets():
    manifest = {"version":3,"layout":{"width":1080,"height":2560,"dpi":360},
        "screens":{"home":{"targets":[{"rel":"home/button.png","rect":[1,2,30,40]}]}},
        "asset_bindings":{"home/button.png":{"screen":"home","names":["button"]}}}
    report={"account":"SECRET", "entries":[
        {"rel":"home/button.png","verified":True,"rect":[2,3,30,40],"confirmation":1,
         "source_kind":"installed_asset","name":"SECRET","image_sha256":"PRIVATE"},
        {"rel":"cards/preset_SECRET.png","verified":True,"rect":[1,2,3,4]}]}
    contribution=export(manifest,report)
    assert len(contribution['targets'])==1
    assert 'SECRET' not in json.dumps(contribution) and 'PRIVATE' not in json.dumps(contribution)
    updated,count=apply_reference(manifest,contribution)
    assert count==1
    assert updated['asset_bindings']['home/button.png']['reference_rect']==[2,3,30,40]
    assert 'reference_rect' not in manifest['asset_bindings']['home/button.png']


def test_ambiguous_screen_and_out_of_bounds_never_export():
    m={"version":3,"layout":{"width":1080,"height":2560},"screens":{
      s:{"targets":[{"rel":"buttons/return.png"}]} for s in ('guild','events')}}
    entries=[{"rel":"buttons/return.png","verified":True,"rect":[1,2,3,4]},
             {"rel":"buttons/return.png","screen":"guild","verified":True,"rect":[1079,2,30,40]}]
    assert export(m,{"entries":entries})['targets']==[]


def test_new_screen_candidates_export_only_original_art_names():
    from player.manifest_contribution import discovery_candidates
    m={'layout':{'width':1080,'height':2560},'screens':{}}
    analysis={'status':'done','screen':'PRIVATE PLAYER NAME','ocr':['SECRET'],
      'matches':[{'names':['OriginalSprite','SECRET'],'rect':[10,20,30,40],'inliers':20},
                 {'names':['OriginalSprite'],'rect':[10,20,30,40],'inliers':30,'ambiguous':True}]}
    result=discovery_candidates(m,analysis,{'OriginalSprite'})
    assert len(result)==1 and result[0]['screen']=='unclassified'
    assert 'SECRET' not in json.dumps(result) and 'PRIVATE' not in json.dumps(result)
    assert result[0]['requires_review']
