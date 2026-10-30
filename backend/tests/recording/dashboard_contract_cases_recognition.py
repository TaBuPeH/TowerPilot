"""Contract cases of the Recognition page that only read (README rows R-01, with the shared helpers of R-02 to R-06).

Family recognition_reads: the template inventory, the required images, the module catalogue and the scan plan over the evidence an account
holds (images, sealed report entries, learned rectangles). The calibration routes are in dashboard_contract_cases_calibrate.py (family
calibrate_writes and the status reads that settle them, which are recorded in this family too), the images and the analysis in
dashboard_contract_cases_screens.py (recognition_images, screen_analysis, recognition_later).

Everything here is the REAL route over the world of test_dashboard_contract.py: a temporary root, ledger and lease directory, a fixed clock,
a process table and an adb server in memory. Nothing starts, taps, navigates or reads the machine.
"""
import copy
import hashlib
import json
from pathlib import Path

import yaml

from dashboard_contract_cases_editors import equipped
from dashboard_contract_pages_world import populate
from test_dashboard_contract import put_image

REC, CAL = 'recognition_reads', 'calibrate_writes'
DIGITS = 10     # the digit font every world is seeded with is captured in every plan
START, OBSERVE, APPLY, STOP = '/api/calibrate/start', '/api/calibrate/observe', '/api/calibrate/apply', '/api/calibrate/stop'


def expected_context(w):
    context = w.context()
    return {key: context.get(key) for key in ('instance', 'account', 'serial', 'rendering', 'profile')}


def start_body(w, key, **extra):
    return dict(dict(expected_context=expected_context(w), idempotency_key=key), **extra)


def navigation_images(w, phases):
    """Cut the navigation images the scan plan says a non-bootstrap calibration needs (account-local, synthetic)."""
    from player.scan_plan import missing_navigation
    for rel in missing_navigation(w.root, w.cfg, phases, include_wave=False):
        put_image(w, rel if isinstance(rel, str) else rel.get('rel'))


def run_recognition_cases(gen):
    reads_cases(gen)


def local_catalogue(w, learned=None):
    """The module catalogue reads the machine's backend/catalogue_local.yaml (the modules THIS install learned): point it at a
    file of the world, with the modules the case names learned (the real loader runs over it)."""
    from player import catalogue
    path = Path(w.root) / 'catalogue_local.yaml'
    w.mp.setattr(catalogue, 'LOCAL', path)
    w.mp.setattr(catalogue, '_local_cache', {'mtime': None, 'data': {}})
    path.write_text(yaml.safe_dump({'modules': {slug: {'name': name, 'abbrevs': []} for slug, name in (learned or {}).items()}}),
                    encoding='utf-8')
    return path


def reads_cases(gen):
    with gen.world('rec_templates') as w:
        got = w.get(REC, 'templates_only_the_digit_font_is_present', '/api/wizard/templates').get_json()
        digits = [f'digits/{d}.png' for d in range(10)]
        assert got == {'have': digits, 'missing_seen': []}
        for rel in ('buttons/exit_battle.png', 'home/end_round_yes.png', 'floaters/second_wind.png'):
            put_image(w, rel)
        got = w.get(REC, 'templates_captured_listed_by_folder_and_name', '/api/wizard/templates').get_json()
        assert got['have'] == ['buttons/exit_battle.png'] + digits[:0] + ['digits/%d.png' % d for d in range(10)] + ['floaters/second_wind.png', 'home/end_round_yes.png']
    with gen.world('rec_templates_missing_seen') as w:
        put_image(w, 'buttons/exit_battle.png')
        w.mp.setattr(w.dash, '_newest_events', lambda n=2000: [{'kind': 'template_missing', 'template': 'buttons/exit_battle.png'},
                                                                {'kind': 'template_missing', 'template': 'icons/game_stats.png'},
                                                                {'kind': 'tick'}])
        got = w.get(REC, 'templates_requested_and_not_found_are_listed_but_not_completeness', '/api/wizard/templates').get_json()
        assert got['missing_seen'] == ['icons/game_stats.png']
    with gen.world('rec_accounts') as w:
        library = {'alpha': {'label': 'Alpha', 'active_profile': 'mine', 'loadouts': {}}, 'beta': {'label': 'Beta', 'active_profile': 'mine', 'loadouts': {}}}

        def as_account(account):
            def change(cfg):
                cfg['accounts'] = copy.deepcopy(library)
                cfg['instances']['main']['account'] = account
            w.edit_config(change)
        as_account('alpha')
        put_image(w, 'buttons/exit_battle.png')
        assert 'buttons/exit_battle.png' in w.get(REC, 'templates_of_the_first_account', '/api/wizard/templates').get_json()['have']
        as_account('beta')
        assert 'buttons/exit_battle.png' not in w.get(REC, 'templates_of_the_second_account_do_not_include_the_first', '/api/wizard/templates').get_json()['have']
    with gen.world('rec_required') as w:
        equipped(w, loadouts={'plain': {'cards': 'main_farm', 'guardians': ['bounty', 'fetch'], 'module_preset': 'Farm'}})
        got = w.get(REC, 'required_images_none_captured', '/api/wizard/required').get_json()
        assert got['required'] and got['missing'] and len(got['missing']) == len([r for r in got['required'] if not r['have']])
        for rel in got['missing'][:3]:
            put_image(w, rel)
        after = w.get(REC, 'required_images_some_captured', '/api/wizard/required').get_json()
        assert len(after['missing']) == len(got['missing']) - 3
    with gen.world('rec_modules') as w:
        local_catalogue(w)
        got = w.get(REC, 'module_catalogue_nothing_learned', '/api/catalogue/modules').get_json()
        assert got['modules'] and not any(m['learned'] for m in got['modules'])
        local_catalogue(w, {'rapidreach_blitzer': 'Rapidreach Blitzer'})
        learned = w.get(REC, 'module_catalogue_one_module_learned_on_this_install', '/api/catalogue/modules').get_json()['modules']
        assert [m['slug'] for m in learned if m['learned']] == ['rapidreach_blitzer'] and len(learned) == len(got['modules']) + 1
    with gen.world('rec_plan') as w:
        populate(w)
        plan = w.get(REC, 'scan_plan_without_a_run', '/api/wizard/scan-plan').get_json()
        assert plan['steps'] and 'captured' in plan and 'total' in plan
        w.get(REC, 'scan_plan_for_a_run', '/api/wizard/scan-plan?preset=bp_farm')
        w.get(REC, 'scan_plan_for_an_unknown_run', '/api/wizard/scan-plan?preset=bp_ghost')
        put_image(w, 'buttons/exit_battle.png')
        after = w.get(REC, 'scan_plan_after_one_image_was_captured', '/api/wizard/scan-plan?preset=bp_farm').get_json()
        assert after['captured'] == plan['captured'] + 1
    scan_plan_cases(gen)

def calibration_folder(w):
    from player import accounts
    folder = accounts.calibration_dir(w.root, w.cfg)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def write_report(w, entries=None, *, text=None):
    """calibrate_report.json of the account: the entries the calibrator sealed, or the text a case gives."""
    (calibration_folder(w) / 'calibrate_report.json').write_text(text if text is not None else json.dumps({'entries': entries}), encoding='utf-8')


def proof(w, rel, **fields):
    """A report entry for a captured image: it carries the sha256 of the file as it is on disk."""
    from player import accounts
    digest = hashlib.sha256(accounts.template_path(w.root, w.cfg, rel).read_bytes()).hexdigest()
    return dict(rel=rel, image_sha256=digest, **fields)


def targets_of(got):
    return {t['rel']: t for step in got['steps'] for t in step['targets']}


def scan_plan_cases(gen):
    """GET /api/wizard/scan-plan over the evidence an account holds: images, sealed report entries, learned rectangles."""
    from test_dashboard_contract import END_ROUND_ROW, IMAGE_ROW, requirements
    rows = {'Farm': [IMAGE_ROW, END_ROUND_ROW, {'alternatives': ['floaters/second_wind.png'], 'reasons': ['Optional rare appearance'], 'blocking': False}]}
    with gen.world('rec_plan_required') as w:
        populate(w)
        requirements(w, rows)
        got = w.get(REC, 'scan_plan_marks_the_blocking_images_of_the_run_required', '/api/wizard/scan-plan?preset=bp_farm').get_json()
        marked = {rel for rel, target in targets_of(got).items() if target['required']}
        assert marked == {'buttons/exit_battle.png', 'home/end_round_yes.png'}, marked
        assert got['captured'] == DIGITS and got['total'] == 209
    with gen.world('rec_plan_evidence') as w:
        populate(w)
        for rel in ('buttons/exit_battle.png', 'home/end_round_yes.png', 'floaters/second_wind.png', 'buttons/retry.png', 'home/battle_btn.png'):
            put_image(w, rel)
        write_report(w, [proof(w, 'buttons/exit_battle.png', verified=True, screen='results'),
                         proof(w, 'home/end_round_yes.png', verified=False, status='stale', reason='The saved image no longer matches the screen'),
                         proof(w, 'floaters/second_wind.png', verified=False, status='unverified_copy', reason='This copy was not cut from this emulator'),
                         dict(proof(w, 'buttons/retry.png', verified=True, screen='results'), image_sha256='0' * 64),
                         proof(w, 'home/battle_btn.png', verified=True, screen='home')])
        got = w.get(REC, 'scan_plan_separates_verified_stale_unverified_copy_and_a_report_for_other_pixels', '/api/wizard/scan-plan').get_json()
        status = {rel: target['status'] for rel, target in targets_of(got).items()}
        assert status['buttons/exit_battle.png'] == 'Verified on this emulator'
        assert status['home/end_round_yes.png'].startswith('Needs attention: ') and status['floaters/second_wind.png'].startswith('Needs attention: ')
        assert status['buttons/retry.png'] == 'Captured; recognition not yet verified'
        assert got['captured'] == DIGITS + 5
        interface = {c['template']: c['status'] for screen in got['interface']['screens'] for c in screen['controls'] if c['template']}
        assert interface['home/battle_btn.png'] == 'verified' and interface['screens/hdr_battle.png'] == 'documented'
    with gen.world('rec_plan_learned') as w:
        populate(w)
        put_image(w, 'buttons/exit_battle.png')
        (calibration_folder(w) / 'learned_manifest.json').write_text(json.dumps({'version': 1, 'targets': {
            'buttons/exit_battle.png': {'screen': 'results', 'rect': [100, 2200, 300, 90], 'frame_size': [1080, 2560],
                                        'relative': [0.0926, 0.8594, 0.2778, 0.0352], 'source': 'cut', 't': 1799990000.0},
            'buttons/retry.png': {'screen': 'results', 'rect': [400, 2200, 280, 90], 'frame_size': [1080, 2560],
                                  'relative': [0.3704, 0.8594, 0.2593, 0.0352], 'source': 'cut', 't': 1799990100.0}}}), encoding='utf-8')
        got = w.get(REC, 'scan_plan_shows_a_learned_rectangle_and_makes_the_target_automatic', '/api/wizard/scan-plan').get_json()
        retry = targets_of(got)['buttons/retry.png']
        assert retry['learned']['rect'] == [400, 2200, 280, 90] and retry['automatic'] is True and retry['status'] == 'Missing'
    with gen.world('rec_plan_corrupt') as w:
        populate(w)
        put_image(w, 'buttons/exit_battle.png')
        write_report(w, text='{"entries": [')
        got = w.get(REC, 'scan_plan_with_a_corrupt_report_verifies_nothing', '/api/wizard/scan-plan').get_json()
        assert targets_of(got)['buttons/exit_battle.png']['status'] == 'Captured; recognition not yet verified'
        write_report(w, text='[]')
        got = w.get(REC, 'scan_plan_with_a_report_that_is_valid_json_but_not_an_object_verifies_nothing', '/api/wizard/scan-plan')
        assert got.status_code == 200 and targets_of(got.get_json())['buttons/exit_battle.png']['status'] == 'Captured; recognition not yet verified'
        write_report(w, text=json.dumps({'entries': [1, None, 'x', {'rel': 5}, {'rel': 'buttons/exit_battle.png', 'verified': True}]}))
        got = w.get(REC, 'scan_plan_with_report_entries_that_are_not_what_the_calibrator_writes_verifies_nothing', '/api/wizard/scan-plan')
        assert got.status_code == 200 and targets_of(got.get_json())['buttons/exit_battle.png']['status'] == 'Captured; recognition not yet verified'
    with gen.world('rec_plan_accounts') as w:
        library = {'alpha': {'label': 'Alpha', 'active_profile': 'mine', 'loadouts': {}}, 'beta': {'label': 'Beta', 'active_profile': 'mine', 'loadouts': {}}}

        def as_account(account):
            def change(cfg):
                cfg['accounts'] = copy.deepcopy(library)
                cfg['instances']['main']['account'] = account
            w.edit_config(change)
        as_account('alpha')
        put_image(w, 'buttons/exit_battle.png')
        write_report(w, [proof(w, 'buttons/exit_battle.png', verified=True, screen='results')])
        assert w.get(REC, 'scan_plan_of_the_first_account', '/api/wizard/scan-plan').get_json()['captured'] == 1
        as_account('beta')
        second = w.get(REC, 'scan_plan_of_the_second_account_shows_nothing_of_the_first', '/api/wizard/scan-plan').get_json()
        assert second['captured'] == 0     # the digit font belongs to no account: an account's plan counts its own images only
