"""The two additions to the history reads (the A2-7 backend requests B6 and B9): every row carries the label its action has in the catalog, and a truncated
battle block hands out the cursor the generic route continues from."""
import json

import pytest

from control import actions as actions_api
from control import operations
from towerpilot_workflows.action_contract import CATALOG, ActionDefinition, Capabilities, Field, StartToStart, use_catalog
from towerpilot_workflows.actions import Actions, new_record
from towerpilot_workflows.operations import Operations

DEVICE = 'device-1'
CONTEXT = dict(device_key=DEVICE, account=None, instance_name='main')


@pytest.fixture
def root(tmp_path):
    return tmp_path


def seed(root, count, *, action='ad_gems', battle='battle-1', start=1000.0):
    path = operations.ledger_path(root)
    Operations(path)                                                                      # the ledger the history lives in
    store = Actions(path)
    ids = []
    for index in range(count):
        body = new_record(device=DEVICE, action=action, instance=action, trigger='automatic', context=dict(account=None),
                          host=dict(driver='collector', operation_id=None, battle_id=battle, generation='g1'), started_at=start + index,
                          occurrence_key=f'window:{action}:{index}')
        ids.append(store.finish(store.begin(body)['id'], result='no_op', reason='no_offer_seen', finished_at=start + index + 1)['id'])
    return ids


def battle(created_at=0.0, name='battle-1'):
    # a battle record as the ledger stores one: its status, origin and the attachment (generation) the seeded rows ran under; the read validates rows against them
    return dict(id=name, created_at=created_at, context=dict(device_key=DEVICE), status='active', origin='started',
                segments=[dict(generation='g1', operation_id=None, started_at=0.0, ended_at=None)])


# ------------------------------------------------------------------------------------------------ B9 label
def test_every_row_of_the_generic_read_carries_the_label_of_its_action(root):
    seed(root, 1, action='ad_gems')
    seed(root, 1, action='shopping', start=2000.0)
    rows = actions_api.executions(root, CONTEXT)['executions']
    assert {row['action']: row['label'] for row in rows} == {'ad_gems': 'Ad gems', 'shopping': 'Shopping'}


def test_every_row_of_a_battle_block_carries_the_label_too(root):
    seed(root, 2)
    block = actions_api.for_battle(root, battle())
    assert [row['label'] for row in block['items']] == ['Ad gems', 'Ad gems']


def test_an_action_the_catalog_does_not_hold_has_a_null_label_not_a_missing_one(root):
    seed(root, 1, action='retired_action')
    [row] = actions_api.executions(root, CONTEXT)['executions']
    assert 'label' in row and row['label'] is None


def test_the_label_follows_the_catalog_in_force(root):
    seed(root, 1, action='probe_action')
    catalog = CATALOG.copy()
    catalog.register(ActionDefinition(id='probe_action', label='Probe action', family='gems', hosts=frozenset({'test'}), schedule=StartToStart(),
                                      fields=(Field('enabled', 'bool', label='Enabled'),), capabilities=Capabilities(live_controls=True, pause=True, run_now=True, variants=False),
                                      saved=None, evidence=()))
    with use_catalog(catalog):
        assert actions_api.executions(root, CONTEXT)['executions'][0]['label'] == 'Probe action'
    assert actions_api.executions(root, CONTEXT)['executions'][0]['label'] is None


def test_the_label_is_added_on_read_and_never_stored(root):
    seed(root, 2)
    before = operations.ledger_path(root).read_bytes()
    assert all('label' in row for row in actions_api.executions(root, CONTEXT)['executions'])
    stored, _ = Actions.read(operations.ledger_path(root), device=DEVICE)
    assert all('label' not in row for row in stored) and operations.ledger_path(root).read_bytes() == before


# ------------------------------------------------------------------------------------------------ B6 cursor
def test_a_truncated_battle_block_hands_out_the_cursor_the_generic_route_continues_from(root):
    ids = seed(root, 5)
    block = actions_api.for_battle(root, battle(), limit=3)
    assert block['truncated'] is True and len(block['items']) == 3 and block['reason'] is None and block['next_before']
    rest = actions_api.executions(root, CONTEXT, battle_id='battle-1', before=block['next_before'], limit=10)
    assert [row['id'] for row in block['items']] + [row['id'] for row in rest['executions']] == ids[::-1]
    assert rest['truncated'] is False and rest['next_before'] is None


def test_the_cursor_walks_every_row_once_whatever_the_page_size(root):
    ids = seed(root, 7)
    seen, block = [], actions_api.for_battle(root, battle(), limit=2)
    seen += [row['id'] for row in block['items']]
    while block.get('truncated'):
        page = actions_api.executions(root, CONTEXT, battle_id='battle-1', before=block['next_before'], limit=2)
        seen += [row['id'] for row in page['executions']]
        block = dict(truncated=page['truncated'], next_before=page['next_before'])
    assert seen == ids[::-1]


def test_a_complete_battle_block_has_no_cursor_key_at_all(root):
    seed(root, 3)
    block = actions_api.for_battle(root, battle(), limit=3)
    assert block['truncated'] is False and 'next_before' not in block
    assert set(block) == {'items', 'reason', 'truncated'}


def test_the_blocks_without_rows_keep_their_shape(root):
    assert actions_api.for_battle(root, battle()) == dict(items=None, reason='recorded_before_action_history', truncated=False)
    seed(root, 1, battle='another-battle')
    meta = Actions.read_meta(operations.ledger_path(root))
    assert actions_api.for_battle(root, battle(created_at=meta['created_at'] + 1)) == dict(items=[], reason=None, truncated=False)
    assert json.dumps(actions_api.for_battle(root, battle(created_at=0.0, name='nothing'))) == json.dumps(dict(items=None, reason='recorded_before_action_history', truncated=False))
