"""Home Ad Gem claims in the action history and the harvest evidence (r15c)."""
from types import SimpleNamespace

import pytest

from flows.shard import GemWatch
from interactions.shopper import Shopper
from runtime.action_session import ActionSession
from runtime.battle_session import BattleSession
from towerpilot_workflows.actions import Actions
from towerpilot_workflows.battles import Battles
from towerpilot_workflows.operations import Operations
from test_ad_gems_home import home, world  # noqa: F401 - the token frames and stubbed readers of the Home unit tests

CONTEXT = dict(device_key='device', account='account', instance='main', serial='fake')


@pytest.fixture
def collector_driver(tmp_path, world):
    """A collector launched on Home: an adopted battle record that is prepared, not yet observed."""
    path = tmp_path / 'operations.db'
    operation, _ = Operations(path).accept('gems', dict(CONTEXT), {})
    body = dict(gather={'ad_gems': True, 'gem_orbit': {'enabled': False}}, shopping=[], shop_interval_sec=90)
    session = BattleSession(Battles(path), operation['id'], body, {}, origin='adopted')
    actions = ActionSession.for_battle(session, 'collector', tracked_only=True)
    rs = SimpleNamespace(gems=GemWatch(body['gather'], session=actions), shop=Shopper(body),
                         mission=SimpleNamespace(active=False), shopping_enabled=False, rewards_enabled=True,
                         repeat_enabled=True, tracker=SimpleNamespace(last=None), last_shop=0)
    rs.gems.ad_gems.delay = (0.0, 0.0)
    return session, rs, path


def claim_on_home(world, rs, before=966):
    world.queue = [home(before), home(before + 6, offer=False), home(before + 6, offer=False)]
    rs.gems.ad_gems.poll_home(home(before))
    assert rs.gems.ad_gems.last_outcome['reason'] == 'balance_increased'


def test_a_home_claim_is_one_succeeded_execution_and_counts_in_the_prepared_battles_evidence(collector_driver, world):
    session, rs, path = collector_driver
    assert session.battle['status'] == 'prepared'
    claim_on_home(world, rs)
    [row] = Actions.read(str(path), device='device')[0]
    assert (row['action'], row['state'], row['host']['driver']) == ('ad_gems', 'succeeded', 'collector')
    assert row['outcome']['reason'] == 'balance_increased' and row['outcome']['evidence'] == ['balance_increased']
    assert (row['outcome']['effect'], row['outcome']['attempts'], row['outcome']['confirmed']) == ('confirmed', 1, 1)
    assert row['refs'] == dict(window_id=rs.gems.ad_gems.window_id, surface='home',
                               observation=dict(reads=2, incomplete=0, absent=0))     # decision frame and fresh frame, both visible
    assert row['host']['battle_id'] == session.battle['id']
    session.checkpoint(rs, battle_visible=False, observed=('ad_gems',))
    evidence = Battles(path).get(session.battle['id'])['runtime']['collection_evidence']
    assert evidence['ad_claim_confirmations'] == 1 and evidence['orbit_balance_confirmations'] == 0


def test_a_home_claim_after_the_battle_ended_is_recorded_operation_scoped(collector_driver, world):
    session, rs, path = collector_driver
    session.observe(rs, 120)
    session.finish('battle_no_longer_observed')
    claim_on_home(world, rs)
    [row] = Actions.read(str(path), device='device')[0]
    assert row['host']['battle_id'] is None and row['host']['generation'] is None and row['coverage'] == 'full'
    assert row['host']['operation_id'] == session.operation_id and row['state'] == 'succeeded'


def test_other_drivers_keep_recording_under_their_last_battle(collector_driver, world):
    session, rs, path = collector_driver
    session.finish('battle_no_longer_observed')
    plain = ActionSession.for_battle(session, 'collector')
    assert plain._identity() == (session.battle['id'], session.generation)
    assert plain._coverage() == 'since_adoption'


def test_the_host_keeps_a_home_offer_observed_while_the_battle_is_not_visible(collector_driver, world):
    session, rs, path = collector_driver
    ad = rs.gems.ad_gems
    ad.delay = (5.0, 5.0)
    ad.poll_home(home())
    assert ad.visibility == 'visible' and ad.claim_at == 105
    session.checkpoint(rs, battle_visible=False, observed=('ad_gems',))
    assert ad.visibility == 'visible' and ad.claim_at == 105
    published = Battles(path).get(session.battle['id'])['runtime']['routines']['ad_gems']
    assert published['visibility'] == 'visible' and published['visibility_reason'] == 'home_frame'
    world.now[0] += 2
    session.checkpoint(rs, battle_visible=False)
    assert ad.visibility == 'unknown' and ad.visibility_reason == 'waiting_for_game_state' and ad.claim_at is None


def test_an_unconfirmed_home_claim_is_failed_unconfirmed_and_never_evidence(collector_driver, world):
    session, rs, path = collector_driver
    world.queue = [home(), home(screen='unknown')]
    rs.gems.ad_gems.poll_home(home())
    [row] = Actions.read(str(path), device='device')[0]
    assert (row['state'], row['outcome']['effect'], row['outcome']['reason']) == ('failed', 'unconfirmed', 'post_claim_not_home')
    session.checkpoint(rs, battle_visible=False)
    evidence = Battles(path).get(session.battle['id'])['runtime']['collection_evidence']
    assert evidence['ad_claim_confirmations'] == 0