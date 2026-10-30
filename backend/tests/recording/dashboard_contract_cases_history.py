"""Contract cases of the Run history page: the recorded battles (GET /api/battles, GET /api/battles/{id}).

Two families of dashboard_contract_pages_v1.json (README rows H-01 and H-02):

  history_list    the list: pages, offsets and limits, every status filter, malformed queries, the emulator and
                  account a request names, and an unreadable ledger;
  history_detail  the detail of one record, with the evidence variants the page must tell apart: nothing observed,
                  started vs adopted vs partially adopted, setup verified/degraded/unknown, the stats capture
                  verified/unverified/failed/pending/missing, sampled collection evidence (what is and is not counted),
                  observation gaps and every unknown the record can hold.

The records are made by the real BattleSession over the real collectors (the Driver of test_dashboard_contract.py),
launched through the real POST /api/control start; setup reports are built by interactions.loadout's own builders,
the stats capture by runtime.runlog.clipboard_provenance, collection evidence by BattleSession's own counting.
Where a record holds a value no code path writes by itself (evidence written for another generation) it is put there
through the ledger's own writer (Battles.update_status) and the case name says so.
"""
import copy
import sqlite3

from control import battles as battle_api
from dashboard_contract_pages_world import json_of
from test_dashboard_contract import Driver, raising

LIST, DETAIL = 'history_list', 'history_detail'


def records(w):
    """Five records of the emulator `main`, newest first: active, interrupted (observation lost),
    interrupted (a successor driver adopted it), ended, ended."""
    one = Driver(w, 'h-one', wave=40, origin='started').go_live()
    ids = [one.battle['id']]
    one.end('observed_results', 'death', last_observed_wave=187)
    one.session.prepare_next()
    one.observe(41)
    one.publish()
    ids.append(one.battle['id'])
    one.end('observed_results', 'death', last_observed_wave=95)
    one.session.prepare_next()
    one.observe(12)
    one.publish()
    ids.append(one.battle['id'])
    w.table.exit(one.op['worker']['pid'])
    two = Driver(w, 'h-two', adopt=True, wave=77).go_live()          # adoption finishes the gone driver's battle
    ids.append(two.battle['id'])
    w.table.exit(two.op['worker']['pid'])
    w.dash._harvest_tick()                                            # the refresher closes this one: observation lost
    three = Driver(w, 'h-three', adopt=True, wave=5).go_live()
    ids.append(three.battle['id'])
    return ids


def page(w, case, query):
    return w.get(LIST, case, '/api/battles' + query)


def history_list_pages_and_filters(gen):
    with gen.world('hist_pages') as w:
        ids = records(w)
        page(w, 'first_page', '?instance=main&limit=2')
        page(w, 'second_page', '?instance=main&limit=2&offset=2')
        final = page(w, 'final_page_one_row', '?instance=main&limit=2&offset=4')
        assert len(final.get_json()['battles']) == 1
        assert page(w, 'past_the_end_empty', '?instance=main&limit=2&offset=5').get_json()['battles'] == []
        every = page(w, 'limit_larger_than_the_rows_returns_all', '?instance=main&limit=200').get_json()['battles']
        assert [b['id'] for b in every] == list(reversed(ids)) and [b['status'] for b in every] == [
            'active', 'interrupted', 'interrupted', 'ended', 'ended']
        page(w, 'limit_one', '?instance=main&limit=1')
        for status, count in (('ended', 2), ('interrupted', 2), ('active', 1), ('prepared', 0)):
            rows = page(w, f'status_{status}' if count else 'status_prepared_none', f'?instance=main&status={status}&limit=2').get_json()['battles']
            assert len(rows) == count and all(row['status'] == status for row in rows)
        page(w, 'status_ended_past_its_rows_empty', '?instance=main&status=ended&limit=2&offset=2')
        page(w, 'status_ended_second_page_of_one', '?instance=main&status=ended&limit=1&offset=1')
        page(w, 'status_empty_text_means_all', '?instance=main&status=&limit=1')
        page(w, 'no_instance_uses_the_active_emulator', '?limit=1')
        assert page(w, 'other_emulator_has_no_battles', '?instance=other&limit=2').get_json()['battles'] == []
        for case, query in (('limit_zero_refused', '?instance=main&limit=0'), ('limit_201_refused', '?instance=main&limit=201'),
                            ('limit_negative_refused', '?instance=main&limit=-1'), ('offset_negative_refused', '?instance=main&offset=-1'),
                            ('limit_text_refused', '?instance=main&limit=abc'), ('limit_empty_refused', '?instance=main&limit='),
                            ('offset_text_refused', '?instance=main&offset=abc'), ('limit_decimal_refused', '?instance=main&limit=2.5'),
                            ('status_unknown_refused', '?instance=main&status=finished'), ('status_upper_case_refused', '?instance=main&status=ENDED'),
                            ('emulator_unknown_refused', '?instance=nope&limit=1')):
            assert page(w, case, query).status_code == 400, case
        assert w.client.get('/api/battles?instance=main&limit=1&offset=0').status_code == 200


def history_list_accounts_and_failures(gen):
    library = {'alpha': {'label': 'Alpha', 'active_profile': 'mine', 'loadouts': {}},
               'beta': {'label': 'Beta', 'active_profile': 'mine', 'loadouts': {}}}

    def as_account(w, account):
        def change(cfg):
            cfg['accounts'] = copy.deepcopy(library)
            cfg['instances']['main']['account'] = account
        w.edit_config(change)
    with gen.world('hist_accounts') as w:
        as_account(w, 'alpha')
        one = Driver(w, 'a-one', origin='started').go_live()
        alpha_id = one.battle['id']
        one.end('observed_results', 'death', last_observed_wave=150)
        w.table.exit(one.op['worker']['pid'])
        as_account(w, 'beta')
        two = Driver(w, 'b-one', origin='started').go_live()
        beta_id = two.battle['id']
        two.end('observed_results', 'death', last_observed_wave=60)
        mine = page(w, 'beta_sees_only_its_own_record', '?instance=main&limit=5').get_json()
        assert [b['id'] for b in mine['battles']] == [beta_id] and mine['context']['account'] == 'beta'
        as_account(w, 'alpha')
        theirs = page(w, 'alpha_sees_only_its_own_record', '?instance=main&limit=5').get_json()
        assert [b['id'] for b in theirs['battles']] == [alpha_id]
        as_account(w, None)
        assert page(w, 'no_account_selected_sees_neither', '?instance=main&limit=5').get_json()['battles'] == []
        as_account(w, 'beta')
        refused = w.get(DETAIL, 'detail_of_the_other_accounts_record_is_a_404', f'/api/battles/{alpha_id}?instance=main')
        assert refused.status_code == 404 and 'selected emulator and account' in refused.get_json()['error']
        assert w.get(DETAIL, 'detail_of_this_accounts_record', f'/api/battles/{beta_id}?instance=main').status_code == 200
    with gen.world('hist_unreadable') as w:
        one = Driver(w, 'u-one', origin='started').go_live()
        record_id = one.battle['id']
        w.mp.setattr(battle_api, 'ledger', raising(sqlite3.OperationalError('database is locked')))
        assert page(w, 'ledger_unreadable_500', '?instance=main&limit=2').status_code == 500
        assert w.get(DETAIL, 'detail_ledger_unreadable_500', f'/api/battles/{record_id}?instance=main').status_code == 500


def run_history_list_cases(gen):
    history_list_pages_and_filters(gen)
    history_list_accounts_and_failures(gen)

# ---------------------------------------------------------------- detail
def record_detail(w, case, battle_id, *, instance='main'):
    return w.get(DETAIL, case, f'/api/battles/{battle_id}?instance={instance}')


def a_setup(kind):
    """A setup report as interactions.loadout builds it."""
    from interactions.loadout import setup_failure, unknown_setup
    modules = [dict(module='Defense Module', role='category', state='verified', detail='equipped_header', reason=None),
               dict(module='Attack Module', role='category', state='verified', detail='equipped_header', reason=None)]
    if kind == 'verified':      # Loadout._apply after manual equipment with final evidence
        report = unknown_setup('as_is', detail='as_is')
        report.update(state='verified', detail='manual_equipment', modules=modules)
        return report
    if kind == 'verified_global_preset':
        return dict(state='verified', loadout='farm_loadout', detail='global_preset_selection', reason=None,
                    continued_with_partial=False, modules=[])
    if kind == 'degraded_refused':
        return setup_failure('farm_loadout', RuntimeError('the module plan could not be equipped'))
    if kind == 'degraded_module_not_equipped':      # _verify_plan: one module is not positively equipped
        report = unknown_setup(detail='manual_equipment', reason='module_plan_not_verified')
        report.update(state='degraded', modules=[modules[0], dict(module='Attack Module', role='category', state='unknown',
                                                                   detail='not_verified', reason='module_not_positively_equipped')])
        return report
    if kind == 'degraded_optional_failed':
        report = a_setup('verified')
        report.update(state='degraded', reason='optional_bot_preset_failed')
        return report
    raise AssertionError(kind)


def stats_capture(w, battle, kind):
    """The stats-capture status as runtime.runlog and the orchestrator write it."""
    from runtime import runlog
    tier = battle['envelope']['preset']['tier']
    text = f'Wave\t187\nCoins earned\t1,000\nTier\t{tier}'
    artifact = str(w.root / 'runs' / 'main' / ('20270115_080000_' + battle['id']))
    if kind == 'verified':
        status = runlog.clipboard_provenance('', text, 10, 11, battle)
    elif kind == 'unverified_wrong_tier':
        status = runlog.clipboard_provenance('', text.replace(f'Tier\t{tier}', f'Tier\t{tier + 4}'), 10, 11, battle)
    elif kind == 'unverified_not_fresh':
        status = runlog.clipboard_provenance(text, text, 10, 10, battle)
    elif kind == 'unverified_not_structured':
        status = runlog.clipboard_provenance('', 'nothing readable', 10, 11, battle)
    elif kind == 'pending':
        status = dict(status='pending', verified=False, battle_id=battle['id'], envelope_hash=battle['envelope_hash'])
    elif kind == 'failed_with_artifact':
        status = dict(status='failed', verified=False, error='tap refused during runlog (runlog_copy)',
                      battle_id=battle['id'], envelope_hash=battle['envelope_hash'])
    elif kind == 'failed_no_artifact':      # the orchestrator's own placeholder when collect() returned None
        return dict(status='failed', artifact=None, verified=False)
    else:
        raise AssertionError(kind)
    status['artifact'] = artifact
    return status


def ended_with(d, status=None):
    """End the battle the way the orchestrator does at the results dialog (outcome unknown), then record the capture."""
    battle = d.session.finish('observed_results', evidence={'last_observed_wave': d.wave,
                              'outcome_certainty': 'results_dialog_does_not_distinguish_death_from_manual_surrender'})
    if status is not None:
        d.store.update_status(battle['id'], d.generation, {'stats_capture': status})
    return battle['id']


def next_battle(d, wave):
    d.session.prepare_next()
    d.observe(wave)
    d.publish()


def history_detail_origin_and_setup(gen):
    with gen.world('hd_prepared') as w:
        d = Driver(w)
        detail = record_detail(w, 'prepared_nothing_observed', d.battle['id']).get_json()['battle']
        assert detail['status'] == 'prepared' and detail['started_at'] is None and detail['wave'] is None
    with gen.world('hd_active_started') as w:
        d = Driver(w, origin='started').go_live()
        battle = record_detail(w, 'active_started_entry_observed', d.battle['id']).get_json()['battle']
        assert battle['start_certainty'] == 'observed_entry' and battle['started_at'] is not None
    with gen.world('hd_active_adopted') as w:
        d = Driver(w, origin='adopted').go_live()
        battle = record_detail(w, 'active_adopted_partial_observation', d.battle['id']).get_json()['battle']
        assert battle['start_certainty'] == 'partial_observation' and battle['origin'] == 'adopted'
    with gen.world('hd_partial_adoption') as w:
        first = Driver(w, 'p-one', wave=30, origin='started').go_live()
        first_id = first.battle['id']
        w.table.exit(first.op['worker']['pid'])
        second = Driver(w, 'p-two', adopt=True, wave=44).go_live()
        record_detail(w, 'partial_adoption_links_the_interrupted_record', second.battle['id'])
        gone = record_detail(w, 'interrupted_by_the_adopting_driver', first_id).get_json()['battle']
        assert gone['status'] == 'interrupted' and gone['end_reason'] == 'driver_operation_ended' and gone['final_wave'] is None
    with gen.world('hd_setup') as w:
        d = Driver(w, 'su', wave=50, origin='started').go_live()
        kinds = ['verified', 'verified_global_preset', 'degraded_refused', 'degraded_module_not_equipped', 'degraded_optional_failed']
        unknown = record_detail(w, 'setup_unknown_not_verified_for_this_battle', d.battle['id']).get_json()['battle']
        assert unknown['runtime']['setup']['state'] == 'unknown'
        ended_with(d)
        for number, kind in enumerate(kinds):
            next_battle(d, 51 + number)
            d.session.set_setup(a_setup(kind))
            ident = ended_with(d)
            shown = record_detail(w, f'setup_{kind}', ident).get_json()['battle']
            assert shown['runtime']['setup']['state'] == ('verified' if kind.startswith('verified') else 'degraded')

def history_detail_stats_capture(gen):
    with gen.world('hd_stats') as w:
        d = Driver(w, 'st', wave=187, origin='started').go_live()
        record_detail(w, 'stats_capture_missing_while_the_battle_is_live', d.battle['id'])
        ident = ended_with(d)
        record_detail(w, 'stats_capture_missing_after_the_battle_ended', ident)
        for number, kind in enumerate(('verified', 'unverified_wrong_tier', 'unverified_not_fresh', 'unverified_not_structured',
                                       'pending', 'failed_with_artifact', 'failed_no_artifact')):
            next_battle(d, 187 + number)
            ident = ended_with(d, stats_capture(w, d.battle, kind))
            shown = record_detail(w, f'stats_capture_{kind}', ident).get_json()['battle']['runtime']['stats_capture']
            assert shown['status'] == {'verified': 'verified', 'pending': 'pending'}.get(kind, 'failed' if kind.startswith('failed') else 'captured_unverified')
    with gen.world('hd_stats_adopted') as w:
        d = Driver(w, 'sa', wave=187, origin='adopted').go_live()      # entry never observed: the capture cannot be verified
        ident = ended_with(d, stats_capture(w, d.battle, 'verified'))
        shown = record_detail(w, 'stats_capture_of_an_adopted_battle_stays_unverified', ident).get_json()['battle']['runtime']['stats_capture']
        assert shown['status'] == 'captured_unverified' and shown['verified'] is False


def confirm_ad(d, tag, *, confirmed=True, reason=None):
    ad = d.rs.gems.ad_gems
    ad.window_id, ad.offer_id, ad.claim_attempt_id = f'{tag}-window', f'{tag}-offer', f'{tag}-claim'
    ad._attempt_started_at = d.w.clock.monotonic() - 2
    ad._finish_attempt('confirmed' if confirmed else 'not_confirmed',
                       reason or ('offer_disappeared' if confirmed else 'offer_still_visible'))


def confirm_orbit(d, tag, *, confirmed=True, reason=None, observed_at=None):
    d.rs.gems.orbit.last_outcome = dict(confirmed=confirmed, reason=reason or ('balance_increased' if confirmed else 'attempt_timeout'),
                                        observed_at=d.w.clock.time() if observed_at is None else observed_at,
                                        window_id=f'{tag}-window', attempt_id=f'{tag}-attempt')


def history_detail_collection(gen):
    with gen.world('hd_collection') as w:
        d = Driver(w, 'co', wave=100, origin='started').go_live()
        ident = ended_with(d)
        none = record_detail(w, 'collection_none_observed', ident).get_json()['battle']['runtime']['collection_evidence']
        assert none['ad_claim_confirmations'] == 0 and none['orbit_balance_confirmations'] == 0
        next_battle(d, 101)
        confirm_ad(d, 'a1')
        d.publish()
        confirm_ad(d, 'a2')
        d.publish()
        confirm_orbit(d, 'o1')
        d.publish()
        shown = record_detail(w, 'collection_sampled_confirmations_live', d.battle['id']).get_json()['battle']['runtime']['collection_evidence']
        assert (shown['ad_claim_confirmations'], shown['orbit_balance_confirmations'], shown['orbit_recorded_increase']) == (2, 1, 2)
        ident = ended_with(d)
        record_detail(w, 'collection_sampled_confirmations_ended', ident)
        next_battle(d, 102)
        confirm_ad(d, 'a3', confirmed=False)
        confirm_orbit(d, 'o2', confirmed=False)
        d.publish()
        counted = record_detail(w, 'collection_unconfirmed_outcomes_not_counted', d.battle['id']).get_json()['battle']['runtime']['collection_evidence']
        assert counted['ad_claim_confirmations'] == 0 and counted['orbit_balance_confirmations'] == 0
        ended_with(d)
        next_battle(d, 103)
        confirm_ad(d, 'a4', reason='offer_still_visible')
        confirm_orbit(d, 'o3', reason='attempt_timeout')
        d.publish()
        wrong = record_detail(w, 'collection_confirmed_with_the_wrong_reason_not_counted', d.battle['id']).get_json()['battle']['runtime']['collection_evidence']
        assert wrong['ad_claim_confirmations'] == 0 and wrong['orbit_balance_confirmations'] == 0
        ended_with(d)
        next_battle(d, 104)
        confirm_orbit(d, 'o4', observed_at=d.battle['created_at'] - 500)
        d.publish()
        old = record_detail(w, 'collection_confirmation_older_than_the_battle_not_counted', d.battle['id']).get_json()['battle']['runtime']['collection_evidence']
        assert old['orbit_balance_confirmations'] == 0
        ended_with(d)
        next_battle(d, 105)
        confirm_ad(d, 'a5')
        d.publish()
        d.publish()
        once = record_detail(w, 'collection_same_confirmation_published_twice_counted_once', d.battle['id']).get_json()['battle']['runtime']['collection_evidence']
        assert once['ad_claim_confirmations'] == 1
        # evidence of another generation: no code path writes it, the ledger's writer accepts any mapping
        d.store.update_status(d.battle['id'], d.generation, {'collection_evidence': dict(
            generation='an-older-driver', started_at=1.0, coverage='sampled_driver_observations', ad_claim_confirmations=9,
            orbit_balance_confirmations=9, orbit_recorded_increase=18, latest_confirmations={}, observed_through_at=2.0)})
        mismatched = record_detail(w, 'collection_evidence_of_another_generation_written_through_the_ledger', d.battle['id']).get_json()['battle']
        assert mismatched['runtime']['collection_evidence']['generation'] != mismatched['generation']


def history_detail_unknowns(gen):
    with gen.world('hd_interrupted_before_observing') as w:
        d = Driver(w)
        d.session.finish('results_without_observed_entry', status='interrupted')
        shown = record_detail(w, 'interrupted_before_any_observation_everything_unknown', d.battle['id']).get_json()['battle']
        assert shown['started_at'] is None and shown['wave'] is None and shown['final_wave'] is None and shown['result'] == 'unknown'
        assert shown['wave_provenance'] == 'unknown'
    with gen.world('hd_ended_unknowns') as w:
        d = Driver(w, 'en', wave=90, origin='started').go_live()
        ident = d.session.finish('observed_results', evidence={'note': 'no wave was recorded at the boundary'})['id']
        shown = record_detail(w, 'ended_without_a_final_wave', ident).get_json()['battle']
        assert shown['final_wave'] is None and shown['wave_provenance'] == 'unknown' and shown['wave'] == 90
        next_battle(d, 91)
        ident = d.session.finish('driver_surrendered_its_own_run', outcome='surrendered', evidence={'last_observed_wave': 91})['id']
        shown = record_detail(w, 'ended_by_a_surrender_the_driver_started', ident).get_json()['battle']
        assert shown['result'] == 'surrendered' and shown['final_wave'] == 91
        next_battle(d, 92)
        ident = d.session.finish('a_reason_this_version_does_not_know', evidence={'last_observed_wave': 92})['id']
        record_detail(w, 'ended_for_a_reason_the_page_may_not_know', ident)
    with gen.world('hd_gaps') as w:
        d = Driver(w, 'ga', wave=60, origin='started').go_live()
        w.advance(30)
        d.publish(visible=False)
        w.advance(20)
        d.publish(visible=False)
        open_gap = record_detail(w, 'observation_gap_still_open', d.battle['id']).get_json()['battle']['runtime']
        assert open_gap['observation_gap_started_at'] is not None and open_gap['observation_status'] == 'waiting_for_game_state'
        d.publish(visible=True)
        closed = record_detail(w, 'observation_gap_closed_after_the_screen_returned', d.battle['id']).get_json()
        assert closed['battle']['runtime']['observation_gaps'] and closed['battle']['runtime']['observation_gap_started_at'] is None
        assert closed['events'] and all('t' in event for event in closed['events'])
        ident = ended_with(d)
        record_detail(w, 'ended_after_an_observation_gap', ident)
    with gen.world('hd_invalid_requests') as w:
        d = Driver(w, 'iv', wave=20, origin='started').go_live()
        assert record_detail(w, 'unknown_emulator_refused', d.battle['id'], instance='nope').status_code == 400
        assert record_detail(w, 'unknown_record_404', 'no-such-battle').status_code == 404
        assert record_detail(w, 'record_of_another_emulator_404', d.battle['id'], instance='other').status_code == 404


def run_history_detail_cases(gen):
    history_detail_origin_and_setup(gen)
    history_detail_stats_capture(gen)
    history_detail_collection(gen)
    history_detail_unknowns(gen)


def run_history_cases(gen):
    run_history_list_cases(gen)
    run_history_detail_cases(gen)