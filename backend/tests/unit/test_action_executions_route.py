"""`GET /api/actions/executions`: read-only, context-checked, paged (roadmap item 6, A2-6) and the refresher's
settlement. All on a temporary ledger and a temporary dashboard root."""

import paths as test_paths
import importlib.util
from pathlib import Path

import pytest
import yaml

from control import actions as actions_api
from control import operations
from harvest_support import fake_inspect
from towerpilot_workflows.actions import Actions, new_record
from towerpilot_workflows.operations import Operations


@pytest.fixture
def dash(tmp_path, monkeypatch):
    source = test_paths.DASHBOARD
    spec = importlib.util.spec_from_file_location('actions_route_dashboard_test', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cfg = dict(active_instance='main', active_profile='mine', preset='bp_farm',
               instances={'main': {'serial': 'fake:1'}, 'other': {'serial': 'fake:2'}}, loadouts={}, presets={})
    (tmp_path / 'config.yaml').write_text(yaml.safe_dump(cfg))
    monkeypatch.setattr(module, 'ROOT', str(tmp_path))
    monkeypatch.setattr(module, 'CONFIG_PATH', str(tmp_path / 'config.yaml'))
    monkeypatch.setattr(operations, 'inspect_worker', fake_inspect(lambda worker: True))
    return module


def context(dash):
    with dash.app.test_request_context('/'):
        return dash._battle_history_context()


def seed(dash, count=3, *, account=None, action='ad_gems'):
    selected = context(dash)
    store = Actions(operations.ledger_path(dash.ROOT))
    ids = []
    for index in range(count):
        body = new_record(device=selected['device_key'], action=action, instance=action, trigger='automatic',
                          context=dict(account=account or selected['account']), started_at=1000.0 + index)
        ids.append(store.finish(store.begin(body)['id'], result='no_op', reason='no_offer_seen', finished_at=1001.0 + index)['id'])
    return ids


def get(dash, query=''):
    response = dash.app.test_client().get('/api/actions/executions' + query)
    return response.status_code, response.get_json()


def test_a_cold_checkout_answers_an_empty_history_and_creates_nothing(dash, tmp_path):
    ledger = Path(operations.ledger_path(dash.ROOT))
    status, body = get(dash)
    assert (status, body['executions'], body['truncated'], body['ledger_id']) == (200, [], False, None)
    assert body['schema_version'] == 1 and body['context']['account'] == context(dash)['account']
    assert not ledger.exists()
    Operations(ledger)                                             # a ledger that never recorded an execution
    before = ledger.read_bytes()
    assert get(dash)[1]['executions'] == [] and ledger.read_bytes() == before     # still no table, no row


def test_it_returns_the_selected_accounts_executions_newest_first_and_never_another_accounts(dash):
    ids = seed(dash, 3)
    seed(dash, 2, account='someone-else')                          # same device, another account: never shown
    status, body = get(dash)
    assert status == 200 and [row['id'] for row in body['executions']] == ids[::-1]
    assert body['ledger_id'] and body['executions'][0]['context']['account'] == context(dash)['account']


def test_history_written_by_a_newer_build_is_not_interpreted_and_says_so(dash):
    import sqlite3
    seed(dash, 2)
    db = sqlite3.connect(operations.ledger_path(dash.ROOT))
    db.execute('UPDATE action_meta SET schema_version=99')
    db.commit(); db.close()
    status, body = get(dash)
    assert status == 200 and body['executions'] == [] and body['reason'] == 'newer_schema' and body['ledger_schema_version'] == 99


def test_the_action_filter_pages_and_reports_truncation(dash):
    ids = seed(dash, 5)
    seed(dash, 2, action='floating_gems')
    assert {row['action'] for row in get(dash, '?action=ad_gems')[1]['executions']} == {'ad_gems'}
    first = get(dash, '?action=ad_gems&limit=2')[1]
    assert [row['id'] for row in first['executions']] == [ids[4], ids[3]]
    assert first['truncated'] is True and first['next_before'] == f'1003.0:{ids[3]}'
    second = get(dash, f"?action=ad_gems&limit=2&before={first['next_before']}")[1]
    assert [row['id'] for row in second['executions']] == [ids[2], ids[1]] and second['truncated'] is True
    last = get(dash, f"?action=ad_gems&limit=2&before={second['next_before']}")[1]
    assert [row['id'] for row in last['executions']] == [ids[0]] and last['truncated'] is False and last['next_before'] is None


@pytest.mark.parametrize('query', ['?limit=0', '?limit=501', '?limit=abc', '?limit=1.5', '?before=nan', '?before=inf', '?before=x'])
def test_bad_paging_is_a_coded_400_that_reads_nothing(dash, query):
    seed(dash, 1)
    status, body = get(dash, query)
    assert status == 400 and body['code'] == 'invalid_request' and body['ok'] is False and body['error']


def test_an_unknown_emulator_is_a_400_and_a_get_never_settles_or_changes_a_row(dash):
    assert get(dash, '?instance=nowhere')[0] == 400
    selected = context(dash)
    store = Actions(operations.ledger_path(dash.ROOT))
    operation, _ = Operations(operations.ledger_path(dash.ROOT)).accept('farm', dict(selected, profile='mine'), {}, 'k1')
    body = new_record(device=selected['device_key'], action='ad_gems', instance='ad_gems', trigger='automatic',
                      host=dict(operation_id=operation['id']), context=dict(account=selected['account']), started_at=1000.0)
    store.begin(body)
    Operations(operations.ledger_path(dash.ROOT)).transition(operation['id'], operation['revision'], 'failed')
    rows = get(dash)[1]['executions']
    assert [row['state'] for row in rows] == ['running']          # the host is provably gone, and a GET still settled nothing
    assert actions_api.settle(dash.ROOT) == [rows[0]['id']]       # the refresher's step does
    assert get(dash)[1]['executions'][0]['state'] == 'interrupted'


def test_the_refresher_settles_before_it_reconciles_launches_and_a_cold_checkout_creates_no_schema(dash, monkeypatch):
    seen = []
    monkeypatch.setattr(actions_api, 'settle', lambda root, event=None: seen.append('settle'))
    monkeypatch.setattr(dash, '_procs_cached', lambda: [])
    monkeypatch.setattr(dash.operation_api, 'project_processes', lambda *args, **kwargs: [])
    monkeypatch.setattr(dash.battle_api, 'reconcile_existing', lambda root: seen.append('reconcile'))
    monkeypatch.setattr(dash.harvest_api, 'tick', lambda *args, **kwargs: seen.append(('tick', kwargs['actions'])))
    dash._harvest_tick()
    assert seen[:2] == ['reconcile', 'settle'] and seen[2][0] == 'tick' and seen[2][1].driver == 'dashboard'
    assert not Path(operations.ledger_path(dash.ROOT)).exists()   # nothing was ever recorded: nothing was created


def test_the_capability_is_advertised_and_a_new_action_adds_no_route(dash):
    assert 'action_executions' in dash._CAPABILITIES
    assert dash.app.test_client().get('/api/runtime').get_json()['capabilities'].count('action_executions') == 1
    assert [rule.rule for rule in dash.app.url_map.iter_rules()].count('/api/actions/executions') == 1

# ----------------------------------------------------------- the battle detail
def make_battle(dash, key):
    from control import battles
    from player.run_controls import resolve
    cfg = dash.load_config()
    selected = operations.context(dash.ROOT, cfg, {'preset': 'bp_farm'}, None)
    operation, _ = operations.ledger(dash.ROOT).accept('run', selected, {}, key)
    preset = {'tier': 1, 'gather': {'ad_gems': True}, 'label': 'Farm'}
    envelope = {'preset': preset, 'controls': resolve(preset, cfg), 'config': cfg, 'profile': {'private': True}}
    return battles.ledger(dash.ROOT).create(operation['id'], selected, envelope, generation='driver'), selected, operation


def detail(dash, battle):
    response = dash.app.test_client().get(f"/api/battles/{battle['id']}")
    return response.status_code, response.get_json()


def test_a_battle_recorded_before_the_history_existed_says_so_instead_of_reading_as_a_battle_where_nothing_ran(dash):
    from control import battles
    old, selected, operation = make_battle(dash, 'old')
    status, body = detail(dash, old)
    assert status == 200 and body['executions'] == dict(items=None, reason='recorded_before_action_history', truncated=False)
    store = Actions(operations.ledger_path(dash.ROOT))              # the store now exists, created after the battle

    def execution(battle_id, at, action='ad_gems'):
        body_ = new_record(device=selected['device_key'], action=action, instance=action, trigger='automatic',
                           host=dict(operation_id=operation['id'], battle_id=battle_id, generation='driver'),
                           context=dict(account=selected['account']), started_at=at)
        return store.finish(store.begin(body_)['id'], result='no_op', finished_at=at + 1)['id']
    execution('another-battle', 900.0, 'floating_gems')
    assert detail(dash, old)[1]['executions'] == dict(items=None, reason='recorded_before_action_history', truncated=False)   # the store knows other battles, not this one
    own = execution(old['id'], 1000.0)
    assert [row['id'] for row in detail(dash, old)[1]['executions']['items']] == [own]      # a row of its own decides: it was recorded
    battles.ledger(dash.ROOT).finish(old['id'], 'driver', reason='observed_results', outcome='unknown', status='ended')
    ledger = operations.ledger(dash.ROOT)
    ledger.transition(operation['id'], ledger.get(operation['id'])['revision'], 'interrupted')
    fresh, selected, operation = make_battle(dash, 'fresh')
    store.finish(store.begin(new_record(device=selected['device_key'], action='floating_gems', instance='floating_gems', trigger='manual',
                                        host=dict(operation_id=operation['id'], battle_id=fresh['id'], generation='driver'),
                                        context=dict(account=selected['account']), started_at=2000.0))['id'],
                 result='failed', effect='unconfirmed', finished_at=2001.0)
    status, body = detail(dash, fresh)
    assert status == 200 and body['executions']['reason'] is None and body['executions']['truncated'] is False
    assert [(row['action'], row['trigger'], row['state']) for row in body['executions']['items']] == [('floating_gems', 'manual', 'failed')]


# ============================================ review R11-02: the first instrumented battle shows what it recorded
def first_battle_session(dash):
    """The real order of a driver's start: the battle is created, THEN its action store appears on the first record."""
    from runtime.action_session import ActionSession
    from runtime.battle_session import BattleSession
    from towerpilot_workflows.battles import Battles
    battle, selected, operation = make_battle(dash, 'first-instrumented')
    assert not Actions.read_meta(operations.ledger_path(dash.ROOT))                   # no action schema yet
    driver = BattleSession(Battles(operations.ledger_path(dash.ROOT)), operation['id'], {}, dash.load_config(), prepared=battle)
    return battle, driver, ActionSession.for_battle(driver, 'collector')


def test_the_first_instrumented_battle_exposes_the_executions_it_recorded(dash):
    battle, driver, session = first_battle_session(dash)
    execution = session.begin('ad_gems')
    session.finish(execution, result='no_op', reason='no_offer_seen')
    assert Actions.read_meta(operations.ledger_path(dash.ROOT))['created_at'] > battle['created_at']      # the store is younger than the battle
    status, body = detail(dash, battle)
    assert status == 200 and body['executions']['reason'] is None
    assert [row['id'] for row in body['executions']['items']] == [execution] and body['executions']['truncated'] is False


def test_the_rows_of_an_adopted_battle_say_they_cover_it_only_since_the_adoption(dash):
    battle, driver, session = first_battle_session(dash)
    started = session.begin('ad_gems')
    session.finish(started, result='no_op')
    driver.finish('observed_results')                                      # a live battle is not replaced: this driver's next one is adopted
    driver.prepare_next(origin='adopted')
    adopted = session.begin('ad_gems')
    rows = {row['id']: row for row in Actions.read(session.path, device=battle['context']['device_key'])[0]}
    assert rows[started]['coverage'] == 'full' and rows[adopted]['coverage'] == 'since_adoption'


def test_a_battle_on_a_ledger_a_newer_build_wrote_is_not_interpreted(dash):
    import sqlite3
    battle, driver, session = first_battle_session(dash)
    session.finish(session.begin('ad_gems'), result='no_op')
    db = sqlite3.connect(operations.ledger_path(dash.ROOT))
    db.execute('UPDATE action_meta SET schema_version=99')
    db.commit()
    db.close()
    assert detail(dash, battle)[1]['executions'] == dict(items=None, reason='newer_schema', truncated=False)


# ============================================ review R11-03: a page is a page of this account's rows, and the cursor loses none
def ledger_store(dash):
    return Actions(operations.ledger_path(dash.ROOT))


def add(dash, at, *, account=None, action='ad_gems', instance=None):
    selected = context(dash)
    body = new_record(device=selected['device_key'], action=action, instance=instance or action, trigger='automatic',
                      context=dict(account=account or selected['account']), started_at=at)
    return ledger_store(dash).record_terminal(body, result='no_op', finished_at=at + 0.5)['id']


def follow(dash, query):
    seen, cursor = [], None
    for _ in range(100):
        status, body = get(dash, query + ('' if cursor is None else f'&before={cursor}'))
        assert status == 200, body
        seen += [row['id'] for row in body['executions']]
        if not body['truncated']:
            assert body['next_before'] is None
            return seen
        assert body['executions'] and body['next_before']
        cursor = body['next_before']
    raise AssertionError('the traversal does not end')


def test_the_selected_accounts_older_execution_is_reachable_behind_two_newer_rows_of_another_account(dash):
    mine = add(dash, 100.0)
    add(dash, 200.0, account='someone-else')
    add(dash, 300.0, account='someone-else')
    status, body = get(dash, '?limit=2')
    assert status == 200 and [row['id'] for row in body['executions']] == [mine]
    assert body['truncated'] is False and body['next_before'] is None


def test_every_execution_of_a_run_of_equal_start_times_is_reachable_by_following_next_before(dash):
    ids = {add(dash, 100.0, instance=f'i{n}') for n in range(3)}
    first = get(dash, '?limit=2')[1]
    assert len(first['executions']) == 2 and first['truncated'] is True and first['next_before'].startswith('100.0:')
    second = get(dash, f"?limit=2&before={first['next_before']}")[1]
    assert second['truncated'] is False and {row['id'] for row in first['executions'] + second['executions']} == ids


@pytest.mark.parametrize('limit', [1, 2, 3, 5])
def test_following_next_before_visits_each_row_of_the_account_once_across_ties_other_accounts_and_damaged_rows(dash, limit):
    import sqlite3
    mine = [add(dash, at, instance=f'i{index}') for index, at in enumerate([100.0, 100.0, 105.0, 100.0, 120.0, 120.0, 90.0, 105.0])]
    for index, at in enumerate([100.0, 105.0, 105.0, 120.0]):
        add(dash, at, account='someone-else', instance=f'o{index}')
    db = sqlite3.connect(operations.ledger_path(dash.ROOT))
    for number, at in enumerate([100.0, 105.0, 120.0, 120.0]):
        db.execute("INSERT INTO action_executions(id, device, action, instance, account, started_at, terminal, body) VALUES (?, ?, 'ad_gems', 'x', ?, ?, 1, '{')",
                   (f'damaged-{number}', context(dash)['device_key'], context(dash)['account'], at))
    db.commit()
    db.close()
    visited = follow(dash, f'?limit={limit}')
    assert sorted(visited) == sorted(mine) and len(visited) == len(set(visited))


@pytest.mark.parametrize('before', ['', ':', 'x', 'x:y', '100.0:', 'nan', 'inf:abc', ':abc'])
def test_a_cursor_that_is_not_one_is_a_coded_400(dash, before):
    add(dash, 100.0)
    status, body = get(dash, f'?before={before}')
    assert status == 400 and body['code'] == 'invalid_request' and body['ok'] is False


def test_the_page_says_how_many_damaged_rows_it_skipped(dash):
    import sqlite3
    mine = add(dash, 100.0)
    db = sqlite3.connect(operations.ledger_path(dash.ROOT))
    db.execute("INSERT INTO action_executions(id, device, action, instance, account, started_at, terminal, body) VALUES ('damaged', ?, 'ad_gems', 'x', ?, 200.0, 1, '{')",
               (context(dash)['device_key'], context(dash)['account']))
    db.commit()
    db.close()
    body = get(dash)[1]
    assert [row['id'] for row in body['executions']] == [mine] and body['unusable'] == 1


# ============================================ review R11-01: a damaged row never stops the refresher's lifecycle
def seed_open_row(dash, body_text):
    import sqlite3
    selected = context(dash)
    path = operations.ledger_path(dash.ROOT)
    row = Actions(path).begin(new_record(device=selected['device_key'], action='ad_gems', instance='ad_gems', trigger='automatic',
                                         context=dict(account=selected['account'])))
    db = sqlite3.connect(path)
    db.execute('UPDATE action_executions SET body=? WHERE id=?', (body_text, row['id']))
    db.commit()
    db.close()
    return row['id']


@pytest.fixture
def tick_parts(dash, monkeypatch):
    seen = []
    monkeypatch.setattr(dash, '_procs_cached', lambda: [])
    monkeypatch.setattr(dash.operation_api, 'project_processes', lambda *args, **kwargs: [])
    monkeypatch.setattr(dash.battle_api, 'reconcile_existing', lambda root: seen.append('battles'))
    monkeypatch.setattr(dash.harvest_api, 'tick', lambda *args, **kwargs: seen.append('harvest'))
    return seen


@pytest.mark.parametrize('damaged', ['{', '{}', '{"host":null}', 'null'])
def test_repeated_ticks_over_a_damaged_open_row_still_reconcile_battles_and_launches(dash, tick_parts, damaged):
    seed_open_row(dash, damaged)
    dash._harvest_tick()
    dash._harvest_tick()
    assert tick_parts == ['battles', 'harvest', 'battles', 'harvest']
    assert dash._HARVEST_HOST['last_error'] is None                      # the lifecycle did not fail: the history is not its business


def test_a_settlement_that_raises_never_skips_the_harvest_tick_and_is_reported_once(dash, tick_parts, monkeypatch, caplog):
    def locked(root, event=None):
        raise RuntimeError('database is locked')
    monkeypatch.setattr(actions_api, 'settle', locked)
    for _ in range(3):
        dash._harvest_tick()
    assert tick_parts == ['battles', 'harvest'] * 3
    assert [record.getMessage() for record in caplog.records].count('Action history settlement failed') == 1      # a standing fault is not a log line per tick
    assert dash._HARVEST_HOST['last_error'] is None


def test_damaged_rows_are_reported_once_while_they_stand(dash, tick_parts, monkeypatch):
    seed_open_row(dash, '{')
    events = []
    monkeypatch.setattr(dash, '_diagnostic_event', lambda kind, **fields: events.append((kind, fields)))
    for _ in range(3):
        dash._harvest_tick()
    assert [kind for kind, _ in events].count('action_history_unusable') == 1 and events[0][1]['rows'] == 1
    assert tick_parts.count('harvest') == 3


def test_the_refreshers_settlement_leaves_a_ledger_a_newer_build_wrote_alone_and_the_tick_runs(dash, tick_parts):
    import sqlite3
    row_id = seed_open_row(dash, '{')
    path = operations.ledger_path(dash.ROOT)
    db = sqlite3.connect(path)
    db.execute('UPDATE action_meta SET schema_version=99')
    db.commit()
    before = db.execute('SELECT * FROM action_executions').fetchall()
    db.close()
    dash._harvest_tick()
    db = sqlite3.connect(path)
    assert db.execute('SELECT * FROM action_executions').fetchall() == before and row_id
    db.close()
    assert tick_parts == ['battles', 'harvest'] and dash._ACTION_HISTORY['failure'] is None


# ============================================ review round 2, R11-03: a body can neither order nor continue a page
def set_body_field(dash, execution_id, **changes):
    import json
    import sqlite3
    path = operations.ledger_path(dash.ROOT)
    db = sqlite3.connect(path)
    body = json.loads(db.execute('SELECT body FROM action_executions WHERE id=?', (execution_id,)).fetchone()[0])
    body.update(changes)
    db.execute('UPDATE action_executions SET body=? WHERE id=?', (json.dumps(body), execution_id))
    db.commit()
    db.close()


def test_a_body_whose_start_time_disagrees_with_its_index_cannot_hide_the_intact_history_behind_it(dash):
    """The reviewer's reproduction: rows at 300, 200 and 100, the newest body's started_at changed to 0. The page used to continue
    from the body (`0:<id>`), so the rows at 200 and 100 were stored but unreachable."""
    newest, middle, oldest = add(dash, 300.0), add(dash, 200.0), add(dash, 100.0)
    set_body_field(dash, newest, started_at=0)
    first = get(dash, '?limit=1')[1]
    assert [row['id'] for row in first['executions']] == [middle] and first['unusable'] == 1 and first['truncated'] is True
    assert first['next_before'] == f'200.0:{middle}', 'the cursor is made of the index values of the row returned'
    second = get(dash, f"?limit=1&before={first['next_before']}")[1]
    assert [row['id'] for row in second['executions']] == [oldest] and second['truncated'] is False and second['next_before'] is None
    assert follow(dash, '?limit=1') == [middle, oldest]


@pytest.mark.parametrize('changes', [dict(started_at=0), dict(started_at='soon'), dict(id='another'), dict(device='elsewhere'), dict(instance='else'),
                                     dict(context={'account': 'another'}), dict(state='running')])
def test_whatever_one_body_says_every_intact_execution_of_the_account_is_reachable_through_the_route(dash, changes):
    ids = [add(dash, 400.0 - 100 * n, instance=f'i{n}') for n in range(4)]
    set_body_field(dash, ids[1], **changes)
    for limit in (1, 2, 3):
        assert follow(dash, f'?limit={limit}') == [ids[0], ids[2], ids[3]], (changes, limit)
