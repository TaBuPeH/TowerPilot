import datetime
from scheduling import global_rewards as rewards


def store_due(now):
    return rewards.store_decide(now).kind=='due'


def check_due(name,now):
    return rewards.decide(name,now).kind=='due'


def test_daily_store_clock_is_utc_stable_and_account_scoped(monkeypatch):
    state={}
    monkeypatch.setattr(rewards.daystate,'get_raw',lambda k,d=None:state.get(k,d))
    monkeypatch.setattr(rewards,'account_key',lambda:'alice')
    midnight=datetime.datetime(2026,9,10,tzinfo=datetime.timezone.utc).timestamp()
    assert not store_due(midnight+2999)
    assert store_due(midnight+4201)
    first=next(s for s in range(3000,4201) if store_due(midnight+s))
    assert store_due(midnight+first)
    state['alice:store']='2026-09-10'
    assert not store_due(midnight+80000)
    monkeypatch.setattr(rewards,'account_key',lambda:'bob')
    assert store_due(midnight+80000)
    assert store_due(midnight+86400+4201)


def test_reward_clocks_are_independent_and_survive_run_restart(monkeypatch):
    state={}
    monkeypatch.setattr(rewards,'account_key',lambda:'alice')
    monkeypatch.setattr(rewards.daystate,'get_raw',lambda k,d=None:state.get(k,d))
    monkeypatch.setattr(rewards.daystate,'set_raw',lambda k,v:state.update({k:v}))
    rewards.settle('guild_progress',1000)
    assert not check_due('guild_progress',1299)
    assert check_due('guild_progress',1300)
    assert check_due('event_missions',1001)
    assert check_due('daily_missions',1001)


def refusing_state(monkeypatch, state):
    """A daily state that reads fine and refuses every write the way a lock that stays held does."""
    monkeypatch.setattr(rewards,'account_key',lambda:'alice')
    monkeypatch.setattr(rewards,'_unsaved',{})
    monkeypatch.setattr(rewards.daystate,'get_raw',lambda k,d=None:state.get(k,d))
    def refuse(key,value):
        raise rewards.daystate.DaystateLockTimeout('stayed locked')
    monkeypatch.setattr(rewards.daystate,'set_raw',refuse)
    logged=[]
    monkeypatch.setattr('runtime.logger.event',lambda kind,**fields:logged.append(kind))
    return logged


def test_a_check_whose_write_the_file_refuses_is_not_repeated_on_every_pass(monkeypatch):
    state={}
    logged=refusing_state(monkeypatch,state)
    rewards.settle('guild_progress',1000)                       # a refused write does not raise into the loop
    assert state=={} and logged==['daystate_write_failed']
    assert not check_due('guild_progress',1299) and check_due('guild_progress',1300)     # the check just made still holds in this process
    monkeypatch.setattr(rewards.daystate,'set_raw',lambda k,v:state.update({k:v}))
    rewards.settle('guild_progress',2000)                       # a write that works makes the file the authority again
    assert rewards._unsaved=={} and state['alice:guild_progress:next']==2300


def test_a_store_claim_whose_write_the_file_refuses_still_closes_the_day_in_this_process(monkeypatch):
    state={}
    logged=refusing_state(monkeypatch,state)
    midnight=datetime.datetime(2026,9,10,tzinfo=datetime.timezone.utc).timestamp()
    assert store_due(midnight+4201)
    rewards.store_settle(midnight+4300)
    assert logged==['daystate_write_failed'] and not store_due(midnight+80000)           # claimed once: not claimed again today
    assert store_due(midnight+86400+4201)                                               # the next UTC day is a new day


def test_event_claim_detection_excludes_boost_purchase(monkeypatch):
    import cv2
    import numpy as np
    from interactions import event_rewards
    frame=np.zeros((2560,1080,3),np.uint8)
    cv2.rectangle(frame,(20,1240),(1050,1440),(255,255,255),5)
    cv2.rectangle(frame,(20,1450),(1050,1650),(255,255,255),5)
    cv2.rectangle(frame,(140,1300),(900,1380),(255,0,255),5)
    cv2.rectangle(frame,(140,1500),(900,1580),(255,0,255),5)
    texts=iter(['CLAIM 20 medal','£17.99'])
    monkeypatch.setattr(event_rewards.textocr,'read_lines',lambda *args:[(0,0,next(texts))])
    assert len(event_rewards.claim_buttons(frame))==1


def test_milestones_require_claim_not_claimed_or_price(monkeypatch):
    import numpy as np
    from interactions import event_rewards
    texts=iter(['Claimed','CLAIM','550','£17.99'])
    monkeypatch.setattr(event_rewards.textocr,'read_lines',lambda *args:[(0,0,next(texts))])
    assert event_rewards.claim_buttons(np.zeros((2560,1080,3),np.uint8),True)==[(787,470)]


def test_badge_result_is_json_serializable(monkeypatch):
    import json
    import numpy as np
    from interactions import missions
    monkeypatch.setattr(missions,'find_tile',lambda *a:(910,485))
    frame=np.zeros((2560,1080,3),np.uint8)
    frame[425:445,850:870]=(230,80,130)
    value=missions.events_badge(frame)
    assert type(value) is bool
    json.dumps({'badge':value})
