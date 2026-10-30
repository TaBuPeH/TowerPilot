"""The page contract: what the routes behind the remaining pages of the new app answer.

backend/tests/fixtures/dashboard_contract_pages_v1.json is generated here exactly like dashboard_contract_v1.json (the
Dashboard contract, test_dashboard_contract.py): the REAL Flask routes of backend/dashboard.py driven over
synthetic state in the isolated world of dashboard_contract_world.py - a temporary root, ledger and lease directory, one
fixed clock, an in-memory process table, adb socket and process creation. It never reads or touches the live
backend, device, lease, process table or a running dashboard (127.0.0.1:8630 included). It is a separate file so
the Dashboard contract keeps its hash for the consumers bound to it. The families of this document are generated
in a fixed order and ids are aliased in order of appearance, so a family keeps its content (family_hashes() in
this module gives each family's own hash) when a family is appended after it.

A normal run only compares: any change in a field, a code or a status fails until a developer regenerates it on
purpose and the UI side re-reads it:

    TP_REGENERATE_DASHBOARD_CONTRACT_PAGES=1 python -m pytest backend/tests/unit/test_dashboard_contract_pages.py

TP_DASHBOARD_CONTRACT_PAGES_OUT=<path> writes the generated contract to that path instead of comparing (how a
second tree is generated; the committed fixture is untouched).

Cases are keyed `<family>.<case>` and have the shape of the Dashboard contract: {request: {method, path, body?}, status,
body}; a body that is not JSON is {text}; ids are aliased in order of appearance; the scenario root is `<root>`.

COVERAGE maps every row the page briefs ask for (backend/logs/reviews/ui-pages/README.md "Missing contract cases
for the backend generator" and runs-brief.md section 2) to the cases that satisfy it; a row the backend cannot
produce is in NOT_PRODUCIBLE with the source that shows why. test_every_row_is_covered_or_explained fails when a row
names a case that is not in the fixture and when the fixture holds a case no row names.
"""

import paths as test_paths
import hashlib
import json
import os
from pathlib import Path

import pytest

import test_dashboard_contract as dashboard
from runtime import build_info
from dashboard_contract_cases_accounts import run_accounts_cases, run_profile_files_cases
from dashboard_contract_cases_editors import run_abilities_cases, run_collection_cases, run_equipment_cases
from dashboard_contract_cases_emulators import run_emulator_cases
from dashboard_contract_cases_calibrate import run_calibrate_cases
from dashboard_contract_cases_context import context_fence_cases
from dashboard_contract_cases_profile_target import PT as PTG, profile_target_cases
from dashboard_contract_cases_recognition_fence import RF, recognition_context_fence_cases
from dashboard_contract_cases_shopping import SHOP as SQ, shopping_queue_cases
from dashboard_contract_cases_run_leftovers import (ADD_REFUSALS, ADD_STEPS, DETAILS, ENTRY, ENTRY_DELETE_REFUSALS, ENTRY_DELETES, ENTRY_REFUSALS, ENTRY_SAVES,
                                                    PLAN_DELETE, RESET, TEMPLATES, TEMPLATES_FENCED as TF, run_battle_details_cases, run_controls_live_reset_all_cases,
                                                    run_entry_patch_cases, run_plan_section_delete_cases, run_templates_add_cases, run_templates_add_fenced_cases)
from dashboard_contract_cases_bluestacks import emulator_bluestacks_inventory_cases
from dashboard_contract_cases_first_policy import collection_first_policy_cases
from dashboard_contract_cases_history import run_history_cases
from dashboard_contract_cases_recognition import run_recognition_cases
from dashboard_contract_cases_screens import run_later_cases, run_screens_cases
from dashboard_contract_cases_runtime import REL, WRITER_PID, run_runtime_cases, runtime_release_cases
from dashboard_contract_cases_environment import RE as ENV, runtime_environment_cases
from dashboard_contract_cases_preview import PB as PRB, PF as PRP, PM as PRM, PS as PRS, run_preview_cases
from dashboard_contract_cases_settings_modules import LE, LR, SM, SR, loadout_restore_eligibility_cases, settings_modules_cases, settings_switch_records_cases
from dashboard_contract_cases_template_codes import CODED as TC, REFUSALS as TC_REFUSALS, run_templates_add_coded_cases
from dashboard_contract_cases_shopping_more import OUT_OF_BOUNDS, RM as RMX, SB, run_controls_live_reset_all_mixed_cases, shopping_queue_bounds_cases
from dashboard_contract_cases_run_parity import PARITY as PAR, run_entry_patch_parity_cases
from dashboard_contract_cases_kind_guard import KG, run_kind_guard_cases
from dashboard_contract_cases_profile_fenced import PF as PFD, profile_patch_fenced_cases
from dashboard_contract_cases_ad_gems_poll import AP, ad_gems_failed_attempt_poll_cases
from dashboard_contract_cases_plan_removal import PRE, plan_removal_effects_cases
from dashboard_contract_cases_profile_target_more import PM as PTM, REFUSED_RUN_FIELDS, profile_target_more_cases
from dashboard_contract_cases_settings_modules_more import SMM, settings_modules_more_cases
from dashboard_contract_cases_settings_modules_more_2 import MODULES_ONLY, PAGE_CONTEXT, SM2, settings_modules_more_2_cases
from dashboard_contract_cases_profile_target_more_2 import PM2, STEPS as PM2_STEPS, case_names as pm2_names, profile_target_more_2_cases
from dashboard_contract_cases_action_executions import AX, FAMILY_ROW as AX_ROW, NOTE as AX_NOTE, ROWS as AX_ROWS, action_executions_cases
from dashboard_contract_cases_action_executions_more import AXM, FAMILY_ROW as AXM_ROW, NOTE as AXM_NOTE, ROWS as AXM_ROWS, action_executions_more_cases
from dashboard_contract_cases_action_projection import BP as AP_FAMILY, FAMILY_ROW as AP_ROW, NOTE as AP_NOTE, ROWS as AP_ROWS, battle_actions_projection_cases
from dashboard_contract_cases_profile import run_profile_cases, run_schedule_cases
from dashboard_contract_cases_settings import run_settings_cases
from dashboard_contract_cases_runs import run_controls_live_cases, run_controls_save_cases
from dashboard_contract_pages_world import PagesGenerator

FIXTURE = test_paths.FIXTURES / 'dashboard_contract_pages_v1.json'
REGENERATE = 'TP_REGENERATE_DASHBOARD_CONTRACT_PAGES'
OUTPUT = 'TP_DASHBOARD_CONTRACT_PAGES_OUT'

# Order matters: ids are aliased in order of appearance, so a family keeps its ids (and its hash) when
# families are appended after it. Never insert a family before an existing one.
FAMILIES = [run_controls_save_cases, run_controls_live_cases, run_history_cases, run_profile_cases, run_schedule_cases,
            run_settings_cases, run_equipment_cases, run_collection_cases, run_abilities_cases,
            run_accounts_cases, run_profile_files_cases, run_emulator_cases,
            run_runtime_cases, run_recognition_cases, run_calibrate_cases, run_screens_cases, run_later_cases,
            context_fence_cases, emulator_bluestacks_inventory_cases, collection_first_policy_cases,
            recognition_context_fence_cases, run_controls_live_reset_all_cases, run_entry_patch_cases, run_templates_add_cases,
            run_battle_details_cases, run_plan_section_delete_cases, run_templates_add_fenced_cases, shopping_queue_cases, profile_target_cases,
            runtime_environment_cases, run_preview_cases, settings_modules_cases, loadout_restore_eligibility_cases, settings_switch_records_cases,
            run_templates_add_coded_cases, run_controls_live_reset_all_mixed_cases, run_entry_patch_parity_cases, shopping_queue_bounds_cases,
            run_kind_guard_cases, profile_patch_fenced_cases, ad_gems_failed_attempt_poll_cases, plan_removal_effects_cases, profile_target_more_cases,
            settings_modules_more_cases, settings_modules_more_2_cases, profile_target_more_2_cases, action_executions_cases, battle_actions_projection_cases,
            action_executions_more_cases,
            runtime_release_cases]
UNNAMED_ALLOWED = 49      # cases no row names (the settling reads of one scenario); report.json lists them

NOTES = dict(
    format=('Each case is {request:{method, path, body?}, status, body}. A JSON body is the route\'s exact answer '
            '(keys sorted by Flask); a body that is not JSON (the default 500 page an unhandled exception produces in '
            'production, exception propagation off) is {text}; a request whose body is not JSON records the text that '
            'was sent. Ids are aliased in order of appearance; the scenario root is <root>; time is the fixed clock '
            '1800000000 (2027-01-15 08:00 UTC). A case named read_* is the GET a client makes to settle a write; the '
            'name says which write it follows.'),
    run_controls_save=('POST /api/run-controls saves the SAVED run (the profile file), compare-and-set on the file '
                       'revision. Order of the route\'s checks: no usable active profile (400), starter/draft/other '
                       'account (409), context (409), revision (409), then the values (400). The reply is the read\'s '
                       'shape with the new profile_revision. There is no idempotency key: a request whose answer was '
                       'lost is settled by a read, never by sending it again (the identical resend of a committed save '
                       'is refused as stale). A save that raised after the file was written answers a text 500 and '
                       'the read shows it; one that raised before the write answers the same 500 and the read shows '
                       'the old revision. Non-finite numbers (the JSON words for not-a-number and infinity) cannot be '
                       'written in this strict-JSON fixture; the route refuses them with 400 (asserted by the '
                       'generator, not recorded).'),
    history_list=('GET /api/battles?instance=&limit=&offset=&status=. Newest record first (rowid descending); the filter is '
                  'applied before the page; limit is 1..200 and offset >= 0 (400 "Invalid battle pagination"), a text that is not an '
                  'integer is a 400 carrying Python\'s own message, status is one of prepared|active|ended|interrupted (an empty '
                  'text means all). The list is scoped to the emulator and account the request names; there is no total count. '
                  'Pages here use limit 2 over five records: the semantics (final page, past the end) are the same at limit 21.'),
    history_detail=('GET /api/battles/{id}?instance=. {battle, events, context}; a record of another emulator or account is a 404 '
                    '"Battle does not belong to the selected emulator and account". Records are made by the real BattleSession; '
                    'setup reports by interactions.loadout\'s builders, the stats capture by runtime.runlog.clipboard_provenance, '
                    'collection evidence by BattleSession\'s own counting (what it does not count is a case). Evidence of '
                    'another generation cannot be produced by any driver: collection_evidence_of_another_generation_written_'
                    'through_the_ledger puts it there with the ledger\'s own writer to show the page that the record holds it.'),
    run_controls_live=('POST /api/operations/{id}/live-controls records one revisioned controls command for the tracked '
                       'battle: compare-and-set on control_revision, one unapplied change at a time, idempotent by '
                       'idempotency_key (an identical request is answered from its record after the driver or battle '
                       'ended; the same key with other changes is a 409). 400/404/409 mean nothing was recorded; a '
                       'recorded command whose read-back failed answers 500 command_outcome_unknown. The accepted '
                       'cases also record the battle read that lists the command and the key. The command wire states '
                       '(requested, pending, applied, rejected, expired) are in dashboard_contract_v1.json battles_active.'),
)


NOTES.update(
    context_fence=('Review finding F6PE-P02. POST /api/accounts (select and create), /api/account-tier, /api/profile-activate, /api/profile-copy, '
                   '/api/profile-promote, /api/profile-patch and /api/loadout-patch accept an optional `expected_context` {instance, account, profile, serial} '
                   '(exactly these four keys, each text or null; the values GET /api/config names: active_instance, that instance\'s account and serial, '
                   'active_profile). It is the FIRST check after the body is read (before the owner refusal, the validators and the revision checks) and is '
                   'made under the config write lock, which stays held until the handler has written or refused. A mismatch is 409 {ok:false, code:'
                   '\'context_changed\', error, current_context} (null when the configuration names no context at all: an unknown account, a missing config.yaml - the fence only reads, a refusal never seeds it from the example - or a stored shape that is not a configuration) and nothing is written; a field that is present but malformed (not an object, other keys, a '
                   'value that is not text or null, an explicit null) is 400 {ok:false, code:\'invalid_request\', error}; an absent field is the legacy page\'s '
                   'request, answered exactly as before (no fence). A request resent after a lost answer meets the context its own commit made and is refused '
                   'context_changed: nothing is applied twice. POST /api/loadout-patch also carries its own expected_revision (compare-and-set on config.yaml): the context is judged first, so a stale context with any revision is context_changed and only a current context with a stale revision is revision_conflict. Another process editing config.yaml is excluded by no lock (as before).'),
    recognition_reads=('GET /api/wizard/templates | required | scan-plan[?preset=], /api/catalogue/modules, /api/calibrate/status[?operation_id=]. '
                       'The account-less root holds the digit font (10 files every world is seeded with); an account\'s own listing and plan '
                       'count its images only. The module catalogue normally reads the machine\'s backend/catalogue_local.yaml: the cases point it at a file '
                       'of the world (the learned modules of a real install are not part of the contract). The scan plan always lists all 209 targets with '
                       'their status (Missing | Captured; recognition not yet verified | Verified on this emulator | Needs attention: <reason>); `required` '
                       'marks only the blocking images of the named run; a calibrate_report.json that is not valid JSON, not an object, or whose entries are '
                       'not objects with a `rel` verifies nothing (200). Status of an operation shows only artifacts sealed to it: a state of another '
                       'operation empties state, report and manifests and names the artifact in artifact_errors; a shared file that is JSON but not an object '
                       'reads as {}. Refusals of the status route carry codes operation_unknown | operation_kind_mismatch. Setup evidence (setup battle, '
                       'final screen, report entries per target) is echoed as the calibrator wrote it; the route summarises no target gap and no scan metric '
                       '(see NOT_PRODUCIBLE).'),
    calibrate_writes=('POST /api/calibrate/start | observe | apply | stop. EVERY refusal of these routes is {ok:false, code, error[, operation][, missing]}: '
                      'the message is what the route always said, `code` is the stable word to branch on, `operation` (public form) is present when the refusal '
                      'names the operation as it is now (the Stop contract). Codes: invalid_request (400: a body that is not a JSON object, not valid JSON, '
                      'empty on start), invalid_routes | invalid_phases | invalid_watch (400), context_changed, idempotency_key_reused, operation_active '
                      '(the ledger already holds an operation on the device), calibration_running, taps_disabled, navigation_images_missing (with `missing`, '
                      'sorted), modules_not_done, device_owned | action_lock (unchanged), operation_required | operation_unknown | operation_changed | '
                      'not_a_calibration | observations_tracked | starter_profile | profile_missing | profile_changed | restoration_pending | no_observations | '
                      'artifact_unavailable | artifact_changed | artifact_invalid | apply_failed (apply; the last is 400 for a write that failed and 500 for '
                      'anything unexpected, both return the operation to awaiting_apply). Launch commands carry expected_context and idempotency_key (an '
                      'identical retry answers the stored operation, without `phases`, whatever has changed since). Start checks, in order: the action lock; '
                      'the body (must be a JSON object, whatever the content type); routes (400); the receipt replay; owners (409 device_owned); a running '
                      'calibration; taps (unless allow_navigation); phases; missing navigation images; phase e needs phase 1 modules; then the ledger '
                      '(expected_context, another operation owns the device). Observe needs no taps; a body that does not claim to be JSON (or is empty) is an '
                      'empty command, a body that claims to be JSON must be an object; `watch` is validated AFTER the receipt replay (an identical retry '
                      'answers the stored operation) and before the owner checks, and must be a whole number of seconds from 0 to 600 (absent, null or 0: two frames only), anything else is refused, never clamped. Apply: a repeat of an applied '
                      'revision is answered from the record (200); then owners, revision, original context and configuration revision, starter, profile file, '
                      'sealed artifacts, no account observations or a pending module restoration (400, refused before the apply began: the operation is '
                      'unchanged), write failures (400/500 apply_failed, the operation returns to awaiting_apply). A failed or interrupted capture is applied and '
                      'keeps its failure. Stop is cooperative for calibrations (a marker file; status cancelling, stop.state stopping, nothing signalled), is not '
                      'held back by the action lock, and a worker that cannot be verified is released while the lease is free. A worker proven gone is retired '
                      'by the ledger before the revision is compared, so the Stop is refused operation_changed with the retired operation.'),
    recognition_images=('A route that answers pixels is recorded as {binary:{content_type, width, height, pixels_sha256}}: the PNG\'s bytes depend on the '
                        'encoder, the decoded pixels do not. The device shows a synthetic 1080x2560 frame drawn in the generator (gradient, a button-like '
                        'block of integer-exact bars, one flat grey block); nothing was captured. The artwork text badge is drawn with anti-aliased text, so '
                        'only its size and Cache-Control are recorded. GET /api/frame.png remembers the frame under ?ts= (the last 8 tokens) together with the '
                        'page it was fetched for (instance, account, profile, serial); POST /api/template/<rel> crops that exact frame and refuses 409 '
                        'frame_expired (the token is gone) or 409 frame_context_changed (the page changed since: the token stays valid for the page it was '
                        'fetched for). The cropper\'s other refusals keep {ok:false, error}. The provenance of a crop (report entry with the image hash, '
                        'learned rectangle) is read from the file the route wrote, under the dashboard\'s account; it no longer consults the settings singleton. '
                        'Screen recognition (OCR) is a controlled stand-in.'),
    screen_analysis=('POST /api/screen-analysis/start does not consult the process table (the ledger refuses a second operation: 409 operation_active); a '
                     'body must be a JSON object or empty / not claim to be JSON. Every refusal of start, status and frame carries a code (idempotency_key_reused | '
                     'context_changed | operation_active | operation_unknown | operation_kind_mismatch | analysis_not_finished | artifact_unavailable | '
                     'artifact_changed | artifact_invalid); status and frame follow the requested operation, else the latest of the context, else the shared '
                     'files. A running operation reports status running whatever the file says; a file of another operation is not shown (message "belongs to '
                     'another operation" stays in the reply next to status running); failed or interrupted operations are status error with the operation\'s '
                     'message or "Analysis did not complete"; a finished operation whose result was never written is status unavailable. frame.png ignores the '
                     '`revision` query the page may send; with no operation and no file it is 404 no_screenshot.'),
    recognition_later=('Later tools. POST /api/clicker/step answers {ok:true, result} with the worker\'s own verdict inside: result.ok can be false (refused, '
                       'screen changed, click sent but destination not confirmed) under an outer ok:true; a worker that prints nothing is a nested refusal; one '
                       'that prints no JSON or times out is the unknown outcome {ok:false, error:"Check did not finish..."} (HTTP 200). The body must be a JSON '
                       'object (400 invalid_request; so is an action that is not text); `execute` must be the JSON true or false when present (400 invalid_execute '
                       'otherwise, before any permission check) and only true clicks (409 taps_disabled when taps are off). GET /api/clicker/frame with no preview is 404 '
                       'no_preview. GET /api/manifest/contribution downloads text only (verified geometry, counts of excluded entries, discovery candidates); the '
                       'screen object the analyzer writes ({name, candidates}) is read by its name (a name the manifest does not know is "unclassified"), a plain name '
                       'is read as well, and a report or analysis that is not the shape its producer writes is skipped.'),
)

def generate(dash, base, monkeypatch, families=None):
    gen = PagesGenerator(dash, base, monkeypatch)
    for family in FAMILIES if families is None else families:
        family(gen)
    return gen.cases


PINNED_PROCESS_CREATED = 1_799_990_000.25      # the creation time every recorded worker identity of the dashboard process gets


def pin_process_identity(value):
    """A receipt names the worker that wrote it - this very process: its pid is pinned by dashboard.pin_processes and its
    creation time (the machine's, the moment pytest started) is pinned here."""
    if isinstance(value, dict):
        pinned = {k: pin_process_identity(v) for k, v in value.items()}
        if pinned.get('pid') == 4242 and type(pinned.get('creation_time')) is float:
            pinned['creation_time'] = PINNED_PROCESS_CREATED
        if pinned.get('pid') == WRITER_PID:
            pinned['pid'] = 4242       # the diagnostics writer's pinned pid (dashboard_contract_cases_runtime.WRITER_PID)
        return pinned
    if isinstance(value, list):
        return [pin_process_identity(v) for v in value]
    return value


def build(dash, base, monkeypatch, families=None):
    base = Path(base) / 'contract'
    document = dict(version=1, generated_by='backend/tests/test_dashboard_contract_pages.py', notes=NOTES,
                    cases=dict(generate(dash, base, monkeypatch, families)))
    return dashboard.alias_ids(pin_process_identity(dashboard.pin_processes(dashboard.scrub(document, base))))


def render(document):
    return dashboard.render(document)


def family_hashes(document):
    return {family: hashlib.sha256(json.dumps(cases, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest().upper()
            for family, cases in document['cases'].items()}


def case_ids(document):
    return dashboard.case_ids(document)


# ---------------------------------------------------------------- coverage
# row -> the cases that satisfy it ("family.case"). A row id is the brief's own (README.md "Missing contract cases
# for the backend generator": G-01..T-02; runs-brief.md section 1: RUN-nn). Filled per page as the families are added.
def keys(family, *cases):
    return [f'{family}.{case}' for case in cases]


SAVE, LIVE = 'run_controls_save', 'run_controls_live'
HL, HD = 'history_list', 'history_detail'
CFG, PR, PP, SP = 'config_state', 'profile_reads', 'profile_patch', 'schedule_plan'
ST = 'settings'
LO, CO, AB = 'loadout_patch', 'collection_policies', 'ability_policies'
AC, PF = 'accounts_write', 'profile_files_write'
EI, EA, ET = 'emulator_inventory', 'emulator_actions', 'emulator_tools'
RS, RD = 'runtime_state', 'runtime_diagnostics'
RR, CW, RI, SA, RL = 'recognition_reads', 'calibrate_writes', 'recognition_images', 'screen_analysis', 'recognition_later'
CF = 'context_fence'
EBI, CFP = 'emulator_bluestacks_inventory', 'collection_first_policy'
NOTES.update(
    run_templates_add_fenced=('POST /api/run-templates/add gained two OPTIONAL guards (root-approved behaviour change; absent = the legacy request, answered as before, see run_templates_add): '
                              '`expected_revision` (the profile revision as GET /api/profile-src publishes it) is judged under the profile lock BEFORE the recipe: stale, null, empty or not text is 409 revision_conflict with the current revision, nothing written; '
                              'a request that carries it also gets the new `revision` back in the 200 answer. `expected_context` {instance, account, profile, serial} is the fence of the other context-dependent writes (409 context_changed + current_context, '
                              'judged first; malformed or null 400 invalid_request). The resend of a committed add carrying the old revision is 409; without it the run identifier refuses it (400).'),
    shopping_queue=('F6-12: every case of the brief under the brief\'s own names. The saved queue is POST /api/run-controls changes.shopping_plan, the live queue POST /api/operations/{id}/live-controls, manual shopping POST '
                    '/api/operations/{id}/actions, the shared library POST /api/profile-patch on policies.shopping_lists.<id>. The legacy conversion preview is GET /api/run-controls on a run whose policy is a legacy list/mapping '
                    '(`legacy_conversion` {plan, warnings, requires_review}), a write only when the page saves a reviewed plan. A live plan command keeps the plan\'s own enabled/interval; routine overrides sit in effective.routines. '
                    'The saved-only comment and the unsupported-plan case of run_controls_save are not edited (they would change that family\'s hash): the repaired case is shopping_queue.refused_the_shopping_plan_the_run_does_not_support.'),
    profile_target=('F6-14: unbinding is POST /api/profile-activate {name: null} with the optional expected_context (device_owned first only after the context is judged); an inactive target of the same account is edited with '
                    'POST /api/profile-patch naming that profile with expected_context of the ACTIVE context: the route has no idle check and no foreign-profile check; a context fence refuses after an active-profile, instance or serial change.'),
    recognition_context_fence=('POST /api/calibrate/apply takes the optional `expected_context` {instance, account, profile, serial} (the same fence as the Profiles writes): '
                               'a mismatch is 409 context_changed + current_context and writes nothing, judged FIRST under the config write lock - before the owner refusal, the replay of an applied '
                               'operation and the operation checks; a malformed field (null included) is 400 invalid_request; an absent field is the legacy request. An apply by operation_id is '
                               'judged by the fence and then by the operation\'s own context check (409 context_changed without current_context). calibrate start / observe and screen-analysis start '
                               'are refused by the operation\'s frozen five-field expected_context (no current_context); Stop is a revocation and is never refused for a context.'),
    run_controls_live_reset_all=('POST /api/operations/{id}/live-controls with null for several paths in one request resets them together (a null for a path with no override is accepted). After a reset '
                                 '`overrides` keeps the (empty) routine mappings: no override means no LEAF, not an empty object.'),
    run_entry_patch=('POST /api/profile-patch on [blueprints, <run>, <field>] for a coin, a tournament, a shard and a dissonance coin run. The 500 answers here are the route\'s own JSON '
                     '{ok:false, error} (validator unavailable / write failed), not the default error page. A stale revision is judged before the values.'),
    run_templates_add=('POST /api/run-templates/add has no expected_revision and no idempotency key: an identical resend after a committed add is 400 "That run already exists"; a lost answer is settled '
                       'only by reading the profile. Every refusal and failure is 400 except a starter/draft profile (409); a write that fails after the file changed is also 400.'),
    battle_details_runtime=('GET /api/battles/active with a Floating gems last_refusal recorded by the tapper\'s own refusal path and an Ad gems offer that was seen and whose attempt failed '
                            '(visibility visible, first_seen_at, appearance_after_at, last_observed_at, response_due_at, last_failed_attempt, counters, durations).'),
    plan_section_delete=('POST /api/profile-patch {path:[plan], op:delete}: 200 and the profile without `plan`; deleting an absent plan section is also 200 (a new backup); a stale revision is 409.'),
)
NOTES.update(
    emulator_bluestacks_inventory=('GET /api/wizard/emulators over a bluestacks.conf with two instances (one stopped, one running) and POST /api/wizard/bluestacks/prepare for each. '
                                   'Appended as its own family because emulator_inventory (MuMu world) has no BlueStacks row. `running` is an HD-Player.exe with `--instance <key>`; '
                                   '`adb_enabled` is the GLOBAL switch (every instance of one file carries the same value); `display_ok` is the instance own framebuffer at 2560x1080@360. '
                                   'Prepare is refused 409 while ANY HD-Player.exe runs, whichever instance is named (the stopped instance too); with nothing running it writes the ADB switch and the '
                                   'named instance framebuffer, and an instance already prepared answers 200 with nothing changed and no backup.'),
    collection_first_policy=('Finding F6C-R1-04. A profile with no gathering policies takes its first one through the supported path that needs no backend change: GET /api/run-templates publishes the shipped '
                             'defaults at templates[i].policies.gather[<reference>] (farm/tournament/dissonance `all_on`, shard `gems_only`), and POST /api/profile-patch '
                             '{name, path:[policies, gather, <new name>], value, expected_revision} creates it (a missing mapping step is created, so an empty gather map or no gather key both work). '
                             'A name already taken is REPLACED, not refused: the page must not offer a taken name. The empty policy is refused (gem_delay_sec is required); text, an unknown key and a stale revision are refused.'),
)
NOTES.update(
    runtime_environment=('Item 10 p10b. GET /api/runtime carries `environment` (local prerequisites: operating system, interpreter, release kind, platform-tools with the version of a MATCHING install receipt only, '
                         'emulator locators, scrcpy server and decoder, app build, OCR script and the stored OCR probe result); GET /api/stream/backends reports adb and scrcpy; POST /api/environment/probe '
                         'with exactly {"probe": "windows_ocr"} runs the one OCR probe, under the global action lock. Every host input is pinned in the test world and every file the report reads is created under '
                         '<root>/install, so the family is the same on every machine: the operating system, Python, adb (platform-tools) and scrcpy versions in it are the pins of the code or the values the '
                         'world was given, never read from the machine that generated it. `available` is a local prerequisite, not a working emulator connection or capture; a locator that finds nothing is `unknown`; '
                         'an OCR script is present without OCR being known to work until the probe says so. The family runtime_state records the same field (over an empty root) and keeps its place.'),
    runtime_release=('Item 10 p10a. GET /api/runtime carries `release` beside `server` (`server` is this process against the source on disk now). `release` (runtime/build_info.release_identity) is what the tree the server runs from says it is: '
                   'kind `checkout` (no release_identity.json; every other field null) or the `source` / `portable-windows-x64` identity its build wrote (version, source fingerprint, app-build and lock hashes, '
                   'python for the portable build, build time, git revision, working-tree state), always with the same keys, plus `matches_source` (the recorded fingerprint against the source on disk now) and '
                   '`matches_app_build` (every file under frontend/webui/app against BUILD_INVENTORY.txt, and that inventory against the recorded hash), each true, false (edited, added or missing) or null '
                   '(not applicable, or the files cannot be read), and `error` (why the kind is `unknown`). `environment.release` stays the kind alone. The families runtime_state and runtime_environment record the same field over trees '
                   'that recorded nothing (every field null) or whose record says nothing but a kind; this family is the installs, each a small synthetic tree of the world (a source file, a built app with its BUILD_INVENTORY.txt and the '
                   'identity naming both), edited, extended or damaged state by state. It is a new family, appended last, because the hashes an install carries are aliased in order of appearance and would renumber every family after them.'),
)
COVERAGE = {
    'RUN-05 saved save: success (switches, numbers, null reset, several paths, run as preset id, shared policy, shopping plan, run with fewer controls)': (
        keys(SAVE, 'read_before_the_first_save', 'saved_switch_off', 'read_after_the_save_holds_the_new_values',
             'saved_numbers_with_the_revision_of_the_last_save', 'saved_switches_and_numbers_in_one_request',
             'saved_null_returns_a_number_to_its_default', 'saved_run_given_as_the_preset_id',
             'saved_with_the_nested_form_the_run_key_and_the_patch_key', 'read_after_several_saves',
             'saved_shopping_plan_v2', 'saved_shopping_switch_off_keeps_the_plan', 'saved_shopping_switch_on_again_with_a_plan',
             'saved_the_first_run_only', 'read_of_the_second_run_after_the_first_was_saved', 'read_of_a_run_with_fewer_controls',
             'saved_a_control_the_run_supports', 'saved_again_with_the_revision_just_read')),
    'RUN-04/06 saved save: refused values (400, nothing saved, the read after is unchanged)': (
        keys(SAVE, 'invalid_below_the_minimum', 'invalid_above_the_maximum', 'invalid_decimal_below_the_minimum',
             'invalid_text_for_a_number', 'invalid_empty_text_for_a_number', 'invalid_true_for_a_number',
             'invalid_number_for_a_switch', 'invalid_text_for_a_switch', 'one_invalid_path_refuses_the_whole_request',
             'read_after_the_refusals_nothing_changed', 'unknown_control', 'unknown_routine', 'path_without_a_field',
             'path_with_too_many_parts', 'path_outside_routines', 'nested_form_with_an_unknown_field',
             'nested_form_with_an_unknown_routine', 'changes_empty', 'changes_a_list', 'changes_text', 'changes_missing',
             'unknown_run', 'combo_is_no_run', 'run_missing', 'run_not_text', 'body_a_list', 'body_not_json', 'body_missing',
             'refused_a_control_the_run_does_not_support', 'refused_the_shopping_plan_the_run_does_not_support',
             'saved_shopping_plan_null_refused', 'saved_shopping_plan_invalid_refused',
             'save_refused_for_a_problem_in_another_run_of_the_profile', 'save_of_a_profile_with_a_problem')),
    'RUN-06 saved save: revision conflict (409, the read after holds the other save)': (
        keys(SAVE, 'stale_revision_another_client_saved_first', 'read_after_the_stale_refusal_holds_the_other_clients_save',
             'stale_revision_is_judged_before_the_values', 'expected_revision_null', 'expected_revision_a_number',
             'expected_revision_empty_text', 'expected_revision_missing')),
    'RUN-06/SR-7 saved save: context conflict (409)': (
        keys(SAVE, 'context_missing', 'context_other_emulator', 'context_other_profile', 'context_other_account',
             'context_with_a_member_missing', 'context_changed_after_the_read',
             'read_after_the_context_changed_names_the_new_profile', 'context_is_judged_before_the_revision')),
    'RUN-06 saved save: read-only profile (starter, scan draft, another account, none selected, file missing) and the action lock': (
        keys(SAVE, 'read_of_the_shipped_starter_is_allowed', 'starter_profile_is_read_only', 'read_of_a_scan_draft_is_allowed',
             'scan_draft_is_read_only', 'read_of_another_accounts_profile_is_allowed', 'another_accounts_profile_is_read_only',
             'read_without_an_active_profile', 'save_without_an_active_profile', 'read_of_a_profile_whose_file_is_missing',
             'save_of_a_profile_whose_file_is_missing', 'action_lock_another_action_in_progress',
             'read_after_the_action_lock_nothing_saved')),
    'RUN-07 saved save: no answer (a 5xx, a lost answer, a lost request) and the read that settles it': (
        keys(SAVE, 'write_failed_nothing_saved_500', 'read_after_the_failed_write_the_revision_is_unchanged',
             'saved_then_the_answer_failed_500', 'read_after_the_unanswered_save_holds_every_sent_value',
             'identical_resend_after_a_committed_save_is_refused_as_stale', 'read_after_a_lost_answer_the_save_is_there',
             'read_after_a_lost_request_nothing_was_saved', 'read_after_a_lost_answer_a_new_revision_without_the_sent_values')),
    'RUN-08 live save: accepted (switch, numbers, null reset, several paths, nested form) and the battle read that lists the key': (
        keys(LIVE, 'accepted_switch_off', 'read_after_the_change_lists_the_command_and_its_key',
             'read_after_the_driver_applied_the_change', 'accepted_numbers_after_the_previous_change_applied',
             'accepted_several_paths_in_one_command', 'accepted_with_the_nested_form',
             'read_after_several_changes_overrides_and_applied_values', 'identical_retry_answered_from_the_record')),
    'RUN-09 live save: what the driver refuses while it is not ready (prepared battle, a change still unapplied, driver gone, battle ended)': (
        keys(LIVE, 'prepared_battle_lists_no_controls_refused', 'second_change_while_the_first_is_not_applied_refused',
             'new_key_refused_the_driver_is_no_longer_active', 'new_key_refused_the_battle_has_ended',
             'identical_retry_answered_from_the_record_after_the_driver_ended',
             'identical_retry_after_the_battle_ended_answered_from_the_record')),
    'RUN-10 live save: no answer, and the refusals that mean nothing was recorded': (
        keys(LIVE, 'admitted_then_the_readback_failed_unknown', 'retry_while_the_readback_still_fails_unknown',
             'read_after_the_unknown_lists_the_key', 'identical_retry_answered_from_the_record_once_the_readback_works',
             'stale_control_revision_refused', 'same_key_with_other_changes_refused', 'stale_generation_refused',
             'unknown_battle_refused', 'unknown_operation_refused', 'replaced_battle_refused',
             'action_lock_another_action_in_progress', 'retry_after_the_lock_was_released_is_recorded',
             'read_after_a_refusal_lists_no_command', 'read_after_the_refusals_nothing_was_recorded')),
    'RUN-11 live save: the server refuses the values the page checks (ranges, types, unknown controls, malformed body)': (
        keys(LIVE, 'invalid_below_the_minimum', 'invalid_above_the_maximum', 'invalid_decimal_below_the_minimum',
             'invalid_text_for_a_number', 'invalid_true_for_a_number', 'invalid_number_for_a_switch', 'unknown_control',
             'unknown_routine', 'path_without_a_field', 'one_invalid_path_refuses_the_whole_command', 'changes_empty',
             'changes_missing', 'control_the_driver_does_not_support', 'battle_id_missing', 'generation_missing',
             'idempotency_key_missing', 'idempotency_key_empty', 'expected_revision_missing', 'expected_revision_text',
             'expected_revision_true', 'body_a_list', 'body_not_json', 'body_missing')),
    'RUN-15 (Later) live reset of one path to the battle\'s starting value': keys(LIVE, 'accepted_null_resets_a_path_to_the_battles_starting_value'),
    # ---- Run history (README H-01, H-02)
    'H-01 status filters (every status, empty text means all, prepared has none, past the filtered rows)': keys(
        HL, 'status_ended', 'status_interrupted', 'status_active', 'status_prepared_none', 'status_ended_past_its_rows_empty',
        'status_ended_second_page_of_one', 'status_empty_text_means_all'),
    'H-01 offset and limit boundaries, the final page and past the end': keys(
        HL, 'first_page', 'second_page', 'final_page_one_row', 'past_the_end_empty', 'limit_one',
        'limit_larger_than_the_rows_returns_all', 'no_instance_uses_the_active_emulator'),
    'H-01 malformed query (limit, offset, status, emulator)': keys(
        HL, 'limit_zero_refused', 'limit_201_refused', 'limit_negative_refused', 'offset_negative_refused', 'limit_text_refused',
        'limit_empty_refused', 'offset_text_refused', 'limit_decimal_refused', 'status_unknown_refused', 'status_upper_case_refused',
        'emulator_unknown_refused'),
    'H-01 other-account and other-emulator isolation (list and detail)': (
        keys(HL, 'beta_sees_only_its_own_record', 'alpha_sees_only_its_own_record', 'no_account_selected_sees_neither', 'other_emulator_has_no_battles')
        + keys(HD, 'detail_of_the_other_accounts_record_is_a_404', 'detail_of_this_accounts_record', 'record_of_another_emulator_404')),
    'H-01 active and prepared details': keys(HD, 'prepared_nothing_observed', 'active_started_entry_observed', 'active_adopted_partial_observation'),
    'H-01 unavailable reads (ledger unreadable, unknown emulator, unknown record)': (
        keys(HL, 'ledger_unreadable_500') + keys(HD, 'detail_ledger_unreadable_500', 'unknown_emulator_refused', 'unknown_record_404')),
    'H-02 started, adopted and partially adopted origin': keys(
        HD, 'active_started_entry_observed', 'active_adopted_partial_observation', 'partial_adoption_links_the_interrupted_record',
        'interrupted_by_the_adopting_driver'),
    'H-02 setup verified, degraded and unknown': keys(
        HD, 'setup_unknown_not_verified_for_this_battle', 'setup_verified', 'setup_verified_global_preset', 'setup_degraded_refused',
        'setup_degraded_module_not_equipped', 'setup_degraded_optional_failed'),
    'H-02 stats capture verified, unverified, failed, pending and missing': keys(
        HD, 'stats_capture_verified', 'stats_capture_unverified_wrong_tier', 'stats_capture_unverified_not_fresh',
        'stats_capture_unverified_not_structured', 'stats_capture_pending', 'stats_capture_failed_with_artifact',
        'stats_capture_failed_no_artifact', 'stats_capture_missing_while_the_battle_is_live',
        'stats_capture_missing_after_the_battle_ended', 'stats_capture_of_an_adopted_battle_stays_unverified'),
    'H-02 sampled collection coverage, what is not counted, and evidence of another generation': keys(
        HD, 'collection_none_observed', 'collection_sampled_confirmations_live', 'collection_sampled_confirmations_ended',
        'collection_unconfirmed_outcomes_not_counted', 'collection_confirmed_with_the_wrong_reason_not_counted',
        'collection_confirmation_older_than_the_battle_not_counted', 'collection_same_confirmation_published_twice_counted_once',
        'collection_evidence_of_another_generation_written_through_the_ledger'),
    'H-02 null wave, time and outcome; unknown reasons; observation gaps': keys(
        HD, 'interrupted_before_any_observation_everything_unknown', 'ended_without_a_final_wave',
        'ended_by_a_surrender_the_driver_started', 'ended_for_a_reason_the_page_may_not_know', 'observation_gap_still_open',
        'observation_gap_closed_after_the_screen_returned', 'ended_after_an_observation_gap'),
}
# Rows whose cases already are in dashboard_contract_v1.json (the Dashboard contract): not copied, referenced.
V1_REFERENCES = {
    'RUN-09 live save: the report is stale / the battle is only prepared / the last change is unapplied (read side)': [
        'battles_active.stale_report', 'battles_active.prepared', 'battles_active.commands_controls_pending_no_path_applied_action_started'],
    'RUN-08/10 command wire states of a controls command (requested, pending with empty and partial applied_paths, applied, rejected, expired)': [
        'battles_active.commands_controls_requested_action_requested', 'battles_active.commands_controls_pending_no_path_applied_action_started',
        'battles_active.commands_controls_pending_some_paths_applied_action_completed', 'battles_active.commands_controls_applied_with_overrides_action_failed',
        'battles_active.commands_controls_rejected_action_rejected', 'battles_active.commands_controls_expired_action_second_key_joined'],
    'RUN-01/02 the run picker and the saved controls read': ['runs.defined_and_undefined_with_labels', 'run_controls.ad_gems_on_floating_off',
                                                            'run_controls.both_off', 'run_controls.unknown_blueprint', 'run_controls.combo_is_no_blueprint'],
}
COVERAGE.update({
    # ---- shared editor reads and writes (README G-01, G-02, G-03)
    'G-01 populated account/loadout config; missing, re-seeded and corrupt file; save and read-after-save': keys(
        CFG, 'populated_accounts_and_loadouts', 'effective_view_of_the_other_account', 'account_missing_from_the_library_refused',
        'file_missing_and_no_example_500', 'file_missing_is_reseeded_from_the_example', 'file_corrupt', 'read_before_the_save',
        'save_accepted', 'read_after_the_save_holds_the_change', 'save_refused_a_list_is_not_a_config', 'save_refused_body_not_json',
        'read_after_the_refusals_is_unchanged'),
    'G-02 profile source, files and list: valid, draft, starter, other account, warnings, errors, missing and unreadable sources': keys(
        PR, 'profiles_list', 'profile_files_active_and_patchable', 'profile_src_valid', 'profile_src_with_validator_warnings',
        'profile_src_with_problems', 'profile_files_with_a_draft_and_the_starter', 'profiles_list_with_a_draft', 'profile_src_of_a_draft',
        'profile_src_of_the_starter', 'profile_files_hide_the_other_accounts_profile', 'profile_src_of_the_other_accounts_profile_is_not_refused',
        'profile_src_unknown_404', 'profile_src_bad_name_400', 'profile_src_not_a_mapping_400', 'profile_src_unreadable_yaml_400',
        'profile_src_empty_file_400', 'profile_src_validator_unavailable_is_reported_not_hidden', 'no_profile_files', 'profiles_list_empty'),
    'G-02 vocabulary, flows and run templates (ok and unsupported)': keys(
        PR, 'vocab_ok', 'vocab_unavailable_is_http_200_with_ok_false', 'vocab_of_the_wrong_type_is_http_200_with_ok_false', 'flows_ok',
        'flows_unavailable_500', 'run_templates_ok'),
    'G-03 profile patch: whole list, append, delete, weekday mapping, coupled plan, whole policy, one policy field': keys(
        PP, 'read_before_the_first_save', 'saved_a_whole_day_list', 'saved_a_block_appended_at_the_end_of_a_list',
        'saved_one_block_deleted_from_a_list', 'saved_a_weekday_mapped_to_a_day_plan', 'saved_a_weekday_mapping_deleted',
        'saved_the_default_day_plan_changed', 'saved_the_whole_plan_days_and_week_together', 'saved_a_whole_gathering_policy',
        'saved_one_field_of_a_policy', 'read_after_the_saves_holds_every_change', 'saved_with_validator_warnings_returned',
        'saved_without_an_expected_revision_is_accepted'),
    'G-03 profile patch: validation failures (windows, counts, times, kinds, tournament cap, empty day/plan, week, policy values), deletes, paths': keys(
        PP, 'block_references_an_unknown_run', 'window_closes_before_it_opens', 'window_closes_when_it_opens', 'count_zero', 'count_negative',
        'count_fraction', 'count_text', 'time_hour_out_of_range', 'time_text', 'block_kind_does_not_match_the_run', 'unknown_block_name',
        'tournament_count_raised', 'two_tournament_blocks_in_one_day', 'day_emptied', 'plan_emptied', 'week_names_an_unknown_day_plan',
        'week_names_an_unknown_weekday', 'policy_field_of_the_wrong_type', 'policy_range_reversed', 'week_default_deleted_leaves_weekdays_unnamed',
        'policy_deleted_while_a_run_uses_it', 'run_deleted_while_a_block_uses_it', 'path_empty', 'list_index_out_of_range', 'value_missing_writes_null',
        'read_after_the_refusals_nothing_changed'),
    'G-03 profile patch: stale revision, starter and draft refusal, unknown profile, malformed body, lock, unreadable file, validator unavailable': keys(
        PP, 'stale_revision_409', 'stale_revision_is_judged_before_the_values', 'read_after_the_stale_refusal_holds_the_other_clients_save',
        'starter_refused_with_a_flag', 'scan_draft_refused', 'unknown_profile_404', 'name_with_a_path_400', 'name_missing_400', 'body_not_json_400',
        'body_a_list_500', 'profile_file_unreadable_400', 'profile_file_not_a_mapping_400', 'validator_unavailable_500', 'action_lock_another_action_in_progress'),
    'G-03 profile patch: write failures, write-then-error, readback, serialized multi-save with a partial result': keys(
        PP, 'write_failed_nothing_saved_500', 'read_after_the_failed_write_the_revision_is_unchanged', 'written_then_the_answer_failed_500',
        'read_after_an_error_that_followed_the_write_holds_the_change', 'identical_resend_after_a_committed_write_is_refused_as_stale',
        'multi_save_first_request_saved', 'multi_save_second_request_saved', 'multi_save_third_request_refused_by_the_validator',
        'read_after_a_partial_batch_holds_the_first_two'),
    'G-03 cross-context limits of the route (names a profile, not the page\'s context)': keys(
        PP, 'another_accounts_profile_is_not_refused_by_this_route', 'the_route_names_a_profile_so_an_active_profile_switch_is_not_noticed',
        'read_names_the_new_active_profile_after_the_switch'),
    # ---- Schedule (README S-01)
    'S-01 scheduler decisions: overlap, exclusive end, midnight split, tournament, changed profile, no plan': keys(
        SP, 'overlapping_windows_the_first_listed_wins', 'window_closed_at_its_exclusive_end_falls_to_the_next',
        'window_open_one_minute_before_its_end_still_wins', 'window_that_opens_later_is_not_yet_eligible',
        'window_written_in_both_days_the_half_covering_now_wins', 'tournament_window_outranks_the_filler_below_it',
        'observed_under_the_active_profile', 'the_active_profile_changed_after_the_observation', 'schedule_status_without_a_plan'),
    'S-01 plan source and patch: missing vs empty plan, plan added, window across midnight written as two blocks': (
        keys(SP, 'profile_without_a_plan_has_no_plan_member', 'profile_with_an_empty_plan_is_a_validator_problem')
        + keys(PP, 'a_plan_added_to_a_profile_that_has_none', 'a_window_across_midnight_is_written_as_two_blocks_and_accepted')),
})
COVERAGE.update({
    # ---- settings (README G-04)
    'G-04 settings read: every module with scope, apply notice, fields and ranges; read-only (starter, draft, other account, none); no policies; unreadable': keys(
        ST, 'populated_account_every_module', 'starter_profile_active_profile_modules_read_only', 'scan_draft_active_profile_modules_read_only',
        'other_accounts_profile_active_profile_modules_read_only', 'no_active_profile_only_machine_and_emulator_modules',
        'profile_without_gathering_policies_has_no_gather_modules', 'profile_not_a_mapping_400', 'profile_unreadable_yaml_400'),
    'G-04 settings save: every module kind (machine, emulator, profile; range, point, integer, number, boolean, enum, string) with the fresh snapshot as readback': keys(
        ST, 'saved_tap', 'saved_device_preview', 'saved_loop', 'saved_missions', 'saved_fleet', 'saved_logging', 'saved_rewards',
        'saved_global_abilities', 'saved_gather_all_on', 'saved_ad_all_on', 'saved_orbit_all_on', 'read_after_every_module_was_saved',
        'saved_although_the_profile_has_an_older_problem_only_new_problems_refuse', 'starter_profile_active_a_machine_module_still_saves'),
    'G-04 settings refusals: ranges, whole numbers, enums, points, booleans, unknown keys and modules, empty and malformed bodies': keys(
        ST, 'integer_with_a_fraction', 'integer_above_the_maximum', 'number_below_the_minimum', 'number_above_the_maximum', 'range_reversed',
        'range_of_three', 'range_needing_whole_numbers_gets_a_fraction', 'boolean_as_text', 'enum_with_an_unknown_option', 'point_outside_the_screen',
        'point_of_three', 'orbit_interval_below_the_minimum', 'ad_window_below_the_minimum', 'unknown_field_for_the_module', 'unknown_module',
        'values_empty', 'values_missing', 'body_a_list', 'body_not_json', 'read_after_the_refusals_nothing_changed',
        'starter_profile_active_a_profile_module_is_refused'),
    'G-04 settings conflicts: stale revision, stale context, switched emulator, action lock': keys(
        ST, 'context_missing', 'context_other_profile', 'context_other_emulator', 'context_other_account', 'revision_missing',
        'stale_revision_another_client_saved_first', 'stale_revision_is_judged_before_the_values', 'stale_revision_is_judged_before_the_module',
        'read_after_the_stale_refusal_holds_the_other_clients_save', 'saved_again_with_the_revision_just_read', 'the_emulator_was_switched_after_the_read',
        'read_after_the_switch_names_the_new_emulator', 'action_lock_another_action_in_progress'),
    'G-04 settings lost-answer outcomes: write failed (a 400), saved then the answer failed (a 500) and the read that settles it': keys(
        ST, 'machine_write_failed_is_a_400_and_nothing_saved', 'profile_write_failed_is_a_400_and_nothing_saved',
        'read_after_the_failed_writes_the_revision_is_unchanged', 'saved_then_the_answer_failed_500', 'read_after_the_unanswered_save_holds_the_value',
        'identical_resend_after_a_committed_save_is_refused_as_stale'),
})
COVERAGE.update({
    # ---- Equipment (README E-01)
    'E-01 loadout save: global preset, category presets, manual cards/guardians, untouched categories, workshop/bot presets, placeholder, advisory': keys(
        LO, 'read_before_the_first_save', 'global_preset_selected', 'category_presets_selected', 'manual_cards_and_guardians',
        'cards_only_the_other_categories_are_left_untouched', 'workshop_and_bot_presets', 'placeholder_body_for_a_loadout_no_run_uses',
        'read_after_the_saves_holds_every_loadout', 'saved_without_an_expected_revision_is_accepted', 'manual_equipment_returns_the_corruption_advisory'),
    'E-01 loadout refusals: not owned (global/category/workshop/cards/guardian/module), mixed global preset, placeholder in use, missing inventory, empty/malformed/unknown': keys(
        LO, 'global_preset_not_on_the_account', 'global_preset_mixed_with_cards', 'module_preset_not_on_the_account',
        'workshop_preset_not_on_the_account', 'cards_preset_not_on_the_account', 'guardian_not_owned', 'module_not_equipped',
        'placeholder_body_for_the_loadout_a_run_uses', 'inventory_missing_the_account_cannot_verify_what_it_equips',
        'a_selection_of_a_preset_with_no_inventory_is_refused', 'body_empty', 'body_a_list', 'body_missing', 'name_unknown_404',
        'name_blank_400', 'name_not_text_400', 'body_not_json', 'read_after_the_refusals_nothing_changed'),
    'E-01 loadout conflicts and failures: revision conflict, lock, validator unavailable, no active profile, write failure, write-then-error, readback': keys(
        LO, 'stale_revision_409', 'an_empty_body_is_judged_before_the_revision', 'stale_revision_is_judged_before_the_name',
        'validator_unavailable_500', 'action_lock_another_action_in_progress',
        'no_active_profile_the_body_is_not_validated_against_any_run', 'write_failed_nothing_saved_500', 'written_then_the_answer_failed_500',
        'read_after_an_error_that_followed_the_write_holds_the_change', 'identical_resend_after_a_committed_write_is_refused_as_stale'),
    # ---- Collection (README C-01)
    'C-01 gathering policy CRUD, run binding, guild spending, orbit geometry, delay and cooldown bounds, chores': keys(
        CO, 'read_before_the_first_save', 'policy_added', 'a_run_bound_to_the_new_policy', 'guild_store_spending_switched_on',
        'guild_store_spending_switched_off_again', 'orbit_geometry_changed', 'orbit_window_longer_than_its_interval_is_accepted',
        'ad_gem_delay_bounds_whole_seconds', 'ad_gem_cooldown_as_a_range', 'retry_cooldown_separate_from_the_collection_cadence',
        'chores_reordered_and_one_switched_off', 'policy_added_that_no_run_uses', 'policy_nothing_uses_deleted',
        'a_run_rebound_to_another_policy', 'the_policy_its_run_left_deleted', 'read_after_the_saves_holds_every_change'),
    'C-01 gathering refusals: unknown keys, wrong types, delays, ad options, orbit values, missing and in-use policy, chores': keys(
        CO, 'policy_with_an_unknown_key', 'ad_gems_as_text', 'delay_fraction', 'delay_reversed', 'delay_negative',
        'delay_missing_makes_the_policy_unusable', 'ad_cooldown_below_the_minimum', 'ad_cooldown_reversed', 'ad_window_below_the_minimum',
        'ad_window_above_the_maximum', 'ad_edge_inset_fraction', 'ad_option_unknown', 'orbit_interval_zero', 'orbit_window_too_long',
        'orbit_retry_below_the_minimum', 'orbit_center_of_three', 'orbit_taps_as_text', 'orbit_unknown_key',
        'run_bound_to_a_policy_that_does_not_exist', 'chore_unknown', 'chore_without_a_name', 'chores_not_a_list',
        'policy_deleted_while_a_run_uses_it', 'a_run_bound_to_a_missing_policy_is_a_problem_the_page_must_show', 'read_after_the_refusals_nothing_changed'),
    # ---- Abilities (README A-01)
    'A-01 Ultimate Weapon baseline and Chain Lightning modes with ranges; rescue arming/watch/immunity; rules appended, edited, reordered, deleted': keys(
        AB, 'read_before_the_first_save', 'uw_policy_added', 'uw_baseline_weapon_switched_off',
        'uw_weapon_the_account_does_not_own_is_allowed_in_a_baseline', 'chain_lightning_mode_always_on',
        'chain_lightning_fleet_marks_with_ranges', 'chain_lightning_off_until_a_wave_range', 'chain_lightning_off',
        'rescue_arming_watch_and_immunity_changed', 'rescue_end_the_sprint_after_second_wind', 'rescue_rule_appended_at_the_end',
        'rescue_rule_cooldown_and_repeat_changed', 'rescue_burst_retaps_changed', 'rescue_rules_reordered', 'rescue_rule_deleted',
        'rescue_policy_added', 'rescue_policy_bound_to_a_run', 'unused_uw_policy_deleted',
        'global_demon_mode_timing_is_a_settings_module_not_a_policy', 'read_after_the_saves_holds_every_change'),
    'A-01 ability refusals: unknown weapons, modes, ranges, arm, bars, thresholds, triggers, actions, buttons, wall/HP mix, missing and in-use policies': keys(
        AB, 'uw_baseline_weapon_unknown', 'uw_baseline_value_not_a_boolean', 'chain_lightning_unknown_mode', 'chain_lightning_mode_missing',
        'chain_lightning_range_reversed', 'chain_lightning_range_of_three', 'chain_lightning_range_as_text', 'chain_lightning_unknown_key',
        'uw_policy_unknown_key', 'rescue_arm_unknown_trigger', 'rescue_watch_negative', 'rescue_immunity_as_text', 'rescue_rule_unknown_bar',
        'rescue_rule_threshold_above_one', 'rescue_rule_samples_zero', 'rescue_rule_unknown_trigger', 'rescue_rule_without_an_action',
        'rescue_rule_unknown_action', 'rescue_rule_unknown_button', 'rescue_rule_throttle_negative', 'rescue_burst_retaps_zero',
        'rescue_policy_mixes_wall_and_hp_bars', 'rescue_rule_trigger_with_a_bar_and_a_collapse_together', 'rescue_rules_not_a_list',
        'run_bound_to_a_rescue_policy_that_does_not_exist', 'run_bound_to_a_uw_policy_that_does_not_exist',
        'rescue_policy_deleted_while_a_run_uses_it', 'uw_policy_deleted_while_a_run_uses_it', 'read_after_the_refusals_nothing_changed'),
    'A-01 unowned and unverified abilities: advisory (unverified, no wall) vs problem (not owned), and the binding refused': keys(
        AB, 'abilities_not_verified_is_an_advisory_not_a_problem', 'no_wall_confirmed_is_an_advisory_not_a_problem',
        'abilities_the_account_does_not_own_are_bound_to_a_run_is_a_problem', 'binding_another_run_to_an_unowned_ability_is_refused'),
})
COVERAGE.update({
    # ---- Profiles (README P-01, P-02)
    'P-01 accounts: create (fresh and importing the current one), select, labels, read-after': keys(
        AC, 'read_before_any_account', 'create_a_fresh_account', 'read_after_create_names_the_account_active',
        'profile_files_after_create_hold_the_new_profile', 'create_an_account_importing_the_current_one', 'select_an_existing_account',
        'read_after_select', 'create_without_a_label_uses_the_id'),
    'P-01 accounts: duplicate and malformed ids, unknown account, existing profile file, missing current profile, malformed body': keys(
        AC, 'create_a_duplicate_id', 'select_an_unknown_account', 'id_with_a_space', 'id_empty', 'id_missing', 'id_not_text', 'id_with_a_path',
        'id_too_long', 'profile_file_of_the_new_account_already_exists', 'body_not_json', 'import_current_but_the_current_profile_is_missing',
        'read_after_the_refusals_alpha_is_still_the_only_account'),
    'P-01 accounts: owner refusal, action lock, create/save partial failure and readback': keys(
        AC, 'refused_while_a_runner_owns_the_device', 'read_after_the_owner_refusal_nothing_created', 'action_lock_another_action_in_progress',
        'create_then_the_config_save_failed_leaves_the_profile_file', 'read_after_the_failed_create_no_account',
        'profile_files_after_the_failed_create_list_the_orphan', 'retry_with_the_same_id_is_refused_because_the_orphan_exists',
        'a_different_id_creates_normally'),
    'P-01 account tier: operator provenance, bounds and types, starter/none, validator, owner, write failure, other account': keys(
        AC, 'read_before_the_tier', 'tier_saved_by_the_operator', 'read_after_the_tier',
        'profile_source_after_the_tier_records_the_operator_as_provenance', 'tier_lowest', 'tier_highest', 'tier_zero', 'tier_21',
        'tier_fraction', 'tier_text', 'tier_true', 'tier_null', 'tier_missing', 'tier_body_not_json', 'read_after_the_refusals_tier_unchanged',
        'tier_of_the_starter_is_refused', 'tier_with_no_profile_file_is_refused', 'tier_with_no_active_profile_is_refused',
        'tier_refused_because_the_profile_has_a_problem', 'tier_refused_while_a_runner_owns_the_device', 'tier_write_failed_500',
        'read_after_the_failed_tier_write_unchanged', 'tier_of_another_accounts_profile_is_not_refused_by_this_route'),
    'P-02 profile copy: starter and own profiles, collision, overwrite, starter/draft/unknown, bad names, lock; nothing is activated': keys(
        PF, 'files_before_the_copy', 'copy_the_starter_to_a_new_profile', 'files_after_the_copy_list_it_and_copy_activates_nothing',
        'copy_one_of_your_own_profiles', 'copy_onto_an_existing_profile_is_refused', 'copy_onto_an_existing_profile_with_overwrite',
        'copy_onto_the_starter_is_refused', 'copy_from_a_scan_draft_is_refused', 'copy_from_an_unknown_profile', 'copy_to_a_draft_name_is_refused',
        'copy_to_a_bad_name', 'copy_without_a_name', 'copy_body_not_json', 'copy_action_lock_another_action_in_progress'),
    'P-02 profile activation (separate from copy): own, starter, none; draft, invalid, unknown, other account, owner, write failure': keys(
        PF, 'activate_another_profile', 'files_after_the_activation_name_the_active_profile', 'activate_the_starter',
        'activate_none_unbinds_the_profile', 'activate_none_again_says_nothing_was_bound', 'files_after_unbinding_have_no_active_profile',
        'activate_an_unknown_profile', 'activate_a_scan_draft', 'activate_a_profile_with_problems_is_refused', 'activate_a_name_with_a_path',
        'activate_a_name_that_is_not_text', 'activate_body_not_json', 'activate_refused_while_a_runner_owns_the_device',
        'files_after_the_owner_refusal_unchanged', 'activate_another_accounts_profile_is_refused', 'activate_write_failed_500',
        'files_after_the_failed_activation_unchanged'),
    'P-02 draft promotion: success with returned problems, collision and overwrite consent, starter/draft/base refusals, failed write, no activation': keys(
        PF, 'promote_a_draft_over_the_starter', 'profile_source_of_the_promoted_profile_carries_the_draft_player_block',
        'files_after_the_promotion_activate_nothing', 'promote_onto_an_existing_profile_is_refused',
        'promote_onto_an_existing_profile_with_overwrite', 'promote_with_the_draft_given_with_its_suffix', 'promote_to_the_starter_name_is_refused',
        'promote_without_a_draft', 'promote_draft_name_missing', 'promote_name_missing', 'promote_to_a_draft_name',
        'promote_over_a_base_that_does_not_exist', 'promote_over_a_draft_base', 'promote_a_draft_with_an_empty_player_block',
        'promote_body_not_json', 'promote_action_lock_another_action_in_progress', 'promotion_returns_the_problems_it_does_not_enforce_them',
        'promote_write_failed_400_receipt_failed', 'files_after_the_failed_promotion_have_no_new_profile'),
    'P-02 scan-operation promotion: operation revision and context, receipt replay and readback, capture kept after a failed apply, owner refusal': keys(
        PF, 'promote_the_result_of_a_finished_scan', 'identical_retry_is_answered_from_the_receipt',
        'the_same_scan_with_another_name_is_a_different_request', 'the_scan_operation_after_the_promotion', 'stale_expected_revision',
        'operation_unknown', 'operation_id_without_a_revision', 'draft_of_another_account_scan', 'profile_changed_after_scanning',
        'operation_that_is_not_a_scan', 'scan_still_running_is_not_final', 'promotion_refused_while_a_runner_owns_the_device',
        'apply_failed_the_receipt_is_failed', 'the_scan_operation_after_the_failed_apply_still_holds_its_capture', 'retry_after_the_failed_apply'),
})
COVERAGE.update({
    # ---- Emulators (README M-01, M-02, M-03)
    'M-01 emulator inventory: several candidates and no automatic choice, one candidate, configured up, probed adb, token serial, folded console token, daemon-only device, no serial yet': keys(
        EI, 'two_running_vms_the_configured_one_is_down_no_automatic_choice',
        'configured_serial_differs_from_the_live_vm_one_candidate_is_suggested', 'configured_device_is_up_nothing_is_suggested',
        'the_configured_adb_is_probed_and_lists_its_devices', 'token_serial_configured_its_port_cannot_be_probed',
        'one_vm_listed_twice_the_console_token_is_folded_into_also_seen', 'daemon_device_without_an_inventory_is_the_only_candidate',
        'no_serial_configured_yet_placeholder'),
    'M-01 screen resolution: portrait reference, portrait other size, landscape, text instead of a frame, no adb server, bad serial': keys(
        EI, 'resolution_portrait_reference', 'resolution_portrait_not_the_reference_size', 'resolution_landscape_is_not_portrait',
        'resolution_the_device_answered_text_not_a_frame', 'resolution_no_adb_server', 'resolution_serial_not_a_device_serial_400',
        'resolution_serial_missing_400'),
    'M-02 adopt: connected, saved but not connected, attached transport verified or silent, expected-instance and placeholder conflicts, bad serials, owner, lock, write failure': keys(
        EA, 'adopt_a_serial_saved_and_connected', 'wizard_state_after_the_adopt_holds_the_receipt', 'config_after_the_adopt_names_the_serial',
        'adopt_saved_but_not_connected', 'adopt_an_attached_transport_verified_over_the_adb_socket',
        'adopt_an_attached_transport_that_does_not_answer', 'adopt_a_placeholder_instance_that_already_has_a_serial_is_refused',
        'adopt_the_instance_changed_since_the_scan', 'adopt_a_serial_that_is_not_a_serial', 'adopt_without_a_serial',
        'adopt_a_serial_that_is_not_text', 'adopt_body_not_json', 'adopt_the_active_instance_does_not_exist_in_config',
        'adopt_refused_while_a_runner_owns_the_device', 'adopt_action_lock_another_action_in_progress',
        'adopt_the_config_could_not_be_written_500', 'config_after_the_failed_adopt_is_unchanged'),
    'M-02 launch: accepted with the boot pipeline, without a configured instance, on a fresh install; manager failure; unsupported; owner; manager error': keys(
        EA, 'launch_a_mumu_vm_with_a_configured_instance_starts_the_boot_pipeline', 'launch_a_vm_no_instance_uses_skips_the_boot_pipeline',
        'launch_on_a_fresh_install_writes_the_serial_it_launched', 'launch_the_manager_reported_a_failure', 'launch_an_unsupported_emulator',
        'launch_mumu_without_an_index_500', 'launch_mumu_with_an_index_that_is_not_a_number_500', 'launch_bluestacks_with_a_bad_key',
        'launch_bluestacks_that_is_not_installed', 'launch_body_not_json', 'launch_mumu_but_its_manager_is_not_installed',
        'launch_refused_while_a_runner_owns_the_device', 'launch_the_manager_could_not_be_started_500'),
    'M-02 BlueStacks preparation: changed, no-op, running, missing conf, unknown instance, bad key': keys(
        EA, 'prepare_changes_the_adb_switch_and_the_framebuffer', 'prepare_again_changes_nothing', 'prepare_refused_while_bluestacks_is_running',
        'prepare_without_bluestacks_conf', 'prepare_an_unknown_instance', 'prepare_a_bad_instance_key', 'prepare_without_a_key', 'prepare_body_not_json'),
    'M-03 connection tools: idle, license required, 202 accepted, already downloading, progress, ready, error, retry, owner, lock': keys(
        ET, 'tools_idle_before_any_install', 'install_without_the_license_400', 'install_license_must_be_true_not_a_word',
        'install_license_false_400', 'install_body_not_json', 'tools_still_idle_after_the_refusals', 'install_accepted_202_the_download_starts',
        'tools_downloading_at_zero', 'install_again_while_downloading_is_answered_not_restarted', 'tools_downloading_with_progress',
        'install_runs_to_the_end', 'tools_installed_after_the_install', 'install_failed_the_request_was_still_accepted',
        'tools_error_state_after_a_failed_install', 'install_can_be_retried_after_an_error',
        'install_refused_while_a_runner_owns_the_device', 'install_action_lock_another_action_in_progress'),
})
V1_REFERENCES.update({
    'M-02 reconnect (200, offline, error, owned) and M-01/M-03 wizard emulators scan, wizard state sections and tools installed/not installed': [
        'reconnect.accepted_device_state_device', 'reconnect.answered_with_another_device_state', 'reconnect.answered_offline_after_the_retries',
        'reconnect.refused_while_a_runner_is_listed', 'reconnect.adb_connect_raised', 'wizard_emulators.instances_with_names_and_ports',
        'wizard_emulators.none', 'wizard_emulators.scan_error', 'wizard_state.after_scan', 'wizard_state.all_sections', 'wizard_tools.installed',
        'wizard_tools.not_installed'],
})
COVERAGE.update({
    # ---- Runtime (README T-01, T-02)
    'T-01 /api/runtime: build current, changed on disk, unreadable, no snapshot; capabilities': keys(
        RS, 'idle_server_build_matches_the_source_on_disk', 'server_started_before_the_source_changed_restart_needed',
        'disk_source_cannot_be_read_so_freshness_is_unknown', 'no_snapshot_could_be_taken_at_start_nothing_to_compare'),
    'T-01 workers (alive, proven gone, unverifiable, accepted without a worker), persistence operations, stale and unreadable process discovery': keys(
        RS, 'worker_alive_and_verified', 'worker_recorded_but_proven_gone_until_the_refresher_retires_it',
        'worker_that_cannot_be_verified_is_unknown_not_dead', 'operation_accepted_but_no_worker_spawned_yet',
        'a_profile_write_is_listed_as_a_persistence_operation_not_a_worker', 'process_discovery_stale', 'process_discovery_unreadable'),
    'P10A /api/runtime release: an intact install, source or app edited or added after the install, the app inventory missing, unreadable source, an identity that cannot be parsed': keys(
        REL, 'install_whose_files_are_the_ones_the_build_recorded', 'source_edited_after_the_install_matches_source_false',
        'source_file_added_after_the_install_matches_source_false', 'app_file_edited_after_the_install_matches_app_build_false',
        'app_file_added_after_the_install_matches_app_build_false', 'app_inventory_missing_matches_app_build_false',
        'install_source_cannot_be_read_so_matches_source_is_unknown', 'identity_that_cannot_be_parsed_is_unknown_with_the_reason'),
    'T-02 diagnostics events: exact id/kind/instance/time/failure filters, limit, empty': keys(
        RD, 'no_log_folder_yet_nothing_scanned', 'every_event_oldest_first_with_log_coordinates', 'filter_by_kind', 'filter_by_operation',
        'filter_by_battle', 'filter_by_command', 'filter_by_request_and_trace', 'filter_by_attempt_matches_a_claim_attempt_too',
        'filter_by_sweep', 'filter_by_window', 'filter_by_offer', 'filter_by_instance_uses_the_folder_unless_the_record_names_one',
        'filter_by_the_instance_a_record_names', 'filters_combined_none_match', 'failures_only_includes_refusals_and_unconfirmed',
        'since_a_time', 'limit_one_returns_the_newest_and_says_truncated', 'limit_1000_is_accepted'),
    'T-02 diagnostics bounds, malformed and unreadable logs, writer health, invalid queries': keys(
        RD, 'a_log_larger_than_the_per_file_bound_is_read_from_its_tail', 'more_files_than_the_bound_the_newest_are_read_and_truncation_is_reported',
        'malformed_lines_are_counted_and_a_half_written_last_line_is_truncation',
        'one_file_cannot_be_read_the_others_are_returned_and_the_error_is_listed', 'writer_health_reports_failed_writes',
        'unknown_filter_refused', 'limit_zero_refused', 'limit_1001_refused', 'limit_text_refused', 'since_text_refused',
        'since_negative_refused', 'since_not_finite_refused', 'failures_must_be_0_or_1', 'filter_longer_than_200_characters_refused'),
})
NOT_PRODUCIBLE = {
    'T-01 build identity of the machine': (
        'dashboard_contract_v1.json notes exclude it and this contract replaces the server snapshot with one over a synthetic source tree: '
        'the real snapshot is the checkout\'s own fingerprint (backend/runtime/build_info.py snapshot()), a machine identity.'),
    'C-01 collection quota and reset evidence': (
        'No producer: the saved-run and battle projections publish the routines\' cadence and last outcomes, and the daily flags '
        'live in scheduling/daystate; no endpoint returns a per-routine quota or reset time (README C-01: "Quota/reset evidence requires '
        'an explicit producer; do not invent success from daily flags").'),
    'A-01 retired rules': (
        'The validator has no notion of a retired rule; a rule action this version does not know is refused as an unknown action '
        '(case rescue_rule_unknown_action) or listed in the profile problems (a_rule_with_an_action_this_version_does_not_support).'),
    'S-01 per-block remaining quota and next-start values': (
        'No API publishes them: backend/control/schedule_status.py read() returns winner/observed_winner/next_clock_boundary only '
        '(its docstring: "No eligibility is computed here") and the Schedule brief says to fixture them only after a producer exists.'),
}
COVERAGE.update({
    'R-01 template inventory: only the digit font, captured by folder, requested-and-not-found, account isolation': keys(
        RR, 'templates_captured_listed_by_folder_and_name', 'templates_of_the_first_account',
        'templates_of_the_second_account_do_not_include_the_first', 'templates_only_the_digit_font_is_present',
        'templates_requested_and_not_found_are_listed_but_not_completeness'),
    'R-01 required images (none/some captured) and the module catalogue (learned on this install)': keys(
        RR, 'module_catalogue_nothing_learned', 'module_catalogue_one_module_learned_on_this_install',
        'required_images_none_captured', 'required_images_some_captured'),
    "R-01 scan plan mapping/interface, the run's blocking images required, unknown run, one image captured": keys(
        RR, 'scan_plan_after_one_image_was_captured', 'scan_plan_for_a_run',
        'scan_plan_for_an_unknown_run', 'scan_plan_marks_the_blocking_images_of_the_run_required',
        'scan_plan_without_a_run'),
    'R-01 scan plan evidence: verified/stale/unverified copy/other pixels, learned rectangle, corrupt report, cross-account isolation': keys(
        RR, 'scan_plan_of_the_first_account', 'scan_plan_of_the_second_account_shows_nothing_of_the_first',
        'scan_plan_separates_verified_stale_unverified_copy_and_a_report_for_other_pixels', 'scan_plan_shows_a_learned_rectangle_and_makes_the_target_automatic',
        'scan_plan_with_a_corrupt_report_verifies_nothing', 'scan_plan_with_a_report_that_is_valid_json_but_not_an_object_verifies_nothing', 'scan_plan_with_report_entries_that_are_not_what_the_calibrator_writes_verifies_nothing'),
    'R-01 calibrate status: failed evidence, foreign/changed artifacts, unknown and other-workflow operations, corrupt shared files, cross-account, untracked worker, observation, taps': keys(
        RR, 'calibrate_status_corrupt_shared_files_read_as_nothing', 'calibrate_status_shared_files_that_are_json_but_not_objects_read_as_nothing', 'calibrate_status_failed_keeps_the_error_and_the_evidence_that_was_sealed',
        'calibrate_status_of_another_workflow_409', 'calibrate_status_of_the_first_account',
        'calibrate_status_of_the_second_account_shows_nothing_of_the_first', 'calibrate_status_report_changed_after_it_was_sealed_is_unavailable',
        'calibrate_status_running_observation_names_its_mode_and_no_taps', 'calibrate_status_running_worker_that_belongs_to_no_operation',
        'calibrate_status_shared_files_of_an_untracked_calibration', 'calibrate_status_state_of_another_operation_empties_the_read_and_names_the_error',
        'calibrate_status_taps_not_allowed', 'calibrate_status_unknown_operation_409',
        'calibrate_status_without_an_operation_reads_the_latest_of_the_context'),
    'R-02 calibrate status of an awaiting-apply, aborted and restoration-required calibration (partial/awaiting-apply/restoration/abort)': keys(
        RR, 'calibrate_status_aborted_by_a_person_keeps_the_partial_evidence', 'calibrate_status_awaiting_apply_with_the_sealed_evidence',
        'calibrate_status_restoration_required_with_the_restore_file_present'),
    'R-06 retained setup evidence: the setup battle (active, finished) and the final screen': keys(
        RR, 'calibrate_status_setup_battle_active_no_final_screen_yet', 'calibrate_status_setup_battle_finished_and_home_verified_with_target_evidence'),
    'R-02 start: accepted modes (basic, default steps, fresh/overwrite/navigation, bootstrap, flows, battle-only, routes, selected run, equipped)': keys(
        CW, 'start_basic_phases_accepted', 'start_battle_only_without_flows_is_ignored',
        'start_bootstrap_flows_battle_only', 'start_bootstrap_needs_no_navigation_images',
        'start_bootstrap_routes_home_to_cards_and_back', 'start_bootstrap_with_the_action_capture_flows',
        'start_equipped_phase_after_modules_are_done', 'start_fresh_overwrite_and_navigation_consent_reach_the_worker',
        'start_names_the_run_the_learning_is_for', 'start_without_an_expected_context_is_accepted',
        'start_without_phases_uses_the_six_default_steps_and_no_fresh_flag'),
    'R-02 start: receipt, replay, same key other command, retry of a settled or failed calibration': keys(
        CW, 'start_for_a_later_retry', 'start_retry_after_the_calibration_failed_answers_the_failed_operation',
        'start_retry_with_the_same_key_answers_the_same_operation', 'start_same_key_with_other_phases_is_refused'),
    'R-02 start: context, owner, running calibration, consent (taps), missing images, phase and route validation': keys(
        CW, 'start_action_lock_another_action_in_progress', 'start_bootstrap_taps_off_409',
        'start_context_changed_409', 'start_equipped_phase_before_modules_are_done_is_refused',
        'start_expected_context_that_is_not_an_object_409', 'start_only_separators_400',
        'start_phases_list_is_read_as_its_text_400', 'start_phases_that_is_not_text_400',
        'start_profile_changed_409', 'start_routes_empty_400',
        'start_routes_not_a_list_400', 'start_routes_not_contiguous_400',
        'start_routes_not_ending_at_home_400', 'start_routes_repeated_id_400',
        'start_routes_unknown_id_400', 'start_routes_with_battle_only_400',
        'start_routes_with_observe_400', 'start_routes_with_the_flows_capture_400',
        'start_routes_without_bootstrap_400', 'start_taps_off_409',
        'start_taps_off_with_the_navigation_consent_is_accepted', 'start_unknown_phase_400',
        'start_while_a_calibration_is_running_is_refused', 'start_while_a_run_owns_the_device_409',
        'start_while_a_scan_owns_the_device_409', 'start_while_an_observation_is_running_409',
        'start_while_the_collector_owns_the_device_409', 'start_with_only_the_base_images_409_lists_the_rest',
        'start_without_the_navigation_images_409'),
    'R-02 start: malformed bodies': keys(
        CW, 'start_body_is_not_valid_json_400', 'start_empty_body_400', 'start_body_not_json_400', 'start_body_that_is_a_number_400', 'start_body_that_is_null_400',
        'start_body_that_is_a_list_400'),
    'R-02 observe: accepted, watch bounds (30, clamped 600, negative, text, fraction), no taps, selected run, receipt/replay': keys(
        CW, 'observe_accepted', 'observe_names_the_run_it_is_for',
        'observe_negative_watch_is_refused_400', 'observe_watch_601_is_refused_400', 'observe_watch_0_is_no_watch', 'observe_retry_with_the_same_key_answers_the_same_operation',
        'observe_same_key_with_a_watch_is_refused', 'observe_taps_off_is_accepted_it_never_taps',
        'observe_watch_30_seconds', 'observe_watch_above_600_is_refused_400', 'observe_watch_600_is_the_longest_accepted',
        'observe_watch_that_is_a_fraction_is_refused_400', 'observe_watch_that_is_a_boolean_is_refused_400', 'observe_watch_that_is_text_is_refused_400', 'observe_watch_that_is_a_number_in_text_is_refused_400'),
    'R-02 observe: context, owner, running calibration/observation, action lock, malformed bodies': keys(
        CW, 'observe_action_lock_another_action_in_progress', 'observe_body_not_json_is_read_as_an_empty_command',
        'observe_body_is_not_valid_json_400', 'observe_body_that_is_a_list_400', 'observe_context_changed_409',
        'observe_while_a_calibration_is_running_409', 'observe_while_a_run_owns_the_device_409',
        'observe_while_an_observation_is_running_is_refused'),
    'R-02 apply: applied, replay after a lost answer, wall bar, selected run readiness, observation, failed/interrupted capture applied with the failure kept, saved observations': keys(
        CW, 'apply_after_a_failed_capture_keeps_the_failure_on_the_operation', 'apply_after_an_interrupted_capture_keeps_the_interruption',
        'apply_again_after_the_restoration_is_done', 'apply_again_after_the_write_failure',
        'apply_an_observation_is_applied_like_a_calibration', 'apply_awaiting_apply_is_applied',
        'apply_profile_write_fails_400', 'apply_resend_after_a_failed_capture_was_applied',
        'apply_resend_after_a_lost_answer_is_answered_from_the_record', 'apply_saved_observations_of_no_operation',
        'apply_unexpected_failure_500', 'apply_with_a_selected_run_returns_its_readiness',
        'apply_with_the_wall_bar_region_writes_it_to_the_configuration'),
    'R-02 apply: revision, operation, artifact, context, config, profile, starter, owner, restoration conflicts and malformed bodies': keys(
        CW, 'apply_a_cancelled_calibration_409', 'apply_action_lock_another_action_in_progress',
        'apply_after_another_profile_became_active_409', 'apply_after_the_configuration_changed_409',
        'apply_after_the_device_changed_409', 'apply_after_the_profile_was_edited_409',
        'apply_an_operation_that_is_not_a_calibration_409', 'apply_body_not_json_is_read_as_an_empty_command',
        'apply_body_is_not_valid_json_400', 'apply_body_that_is_a_list_400', 'apply_other_revision_after_the_apply_is_refused',
        'apply_revision_that_is_text_409', 'apply_saved_observations_that_belong_to_an_operation_409',
        'apply_saved_observations_while_an_operation_is_tracked_409', 'apply_saved_observations_without_a_saved_file_400',
        'apply_saved_observations_without_account_observations_400', 'apply_stale_revision_409',
        'apply_state_changed_after_it_was_sealed_409', 'apply_state_missing_409',
        'apply_state_of_another_operation_409', 'apply_state_without_account_observations_400',
        'apply_unknown_operation_409', 'apply_while_a_run_owns_the_device_409',
        'apply_while_the_calibration_runs_409', 'apply_while_the_module_restoration_is_pending_400',
        'apply_while_the_original_modules_are_not_restored_409', 'apply_with_no_profile_file_400',
        'apply_with_the_starter_profile_active_409', 'apply_without_naming_an_operation_409',
        'apply_without_the_revision_409'),
    'R-02 stop: asks the worker to stop, repeat, released unverified worker, observation, lock bypass': keys(
        CW, 'stop_a_worker_that_cannot_be_verified_is_released_while_the_lease_is_free', 'stop_again_while_it_is_stopping_signals_nothing',
        'stop_an_observation_like_a_calibration', 'stop_is_not_held_back_by_another_action_in_progress',
        'stop_running_calibration_asks_the_worker_to_stop'),
    'R-02 stop: refusals (no operation, unknown, stale, finished, restoration, worker exited) and malformed bodies': keys(
        CW, 'stop_a_calibration_that_finished_409', 'stop_after_the_worker_exited_the_operation_is_retired_and_the_stop_refused_409',
        'stop_body_not_json_is_read_as_an_empty_command', 'stop_body_that_is_a_list_is_read_as_an_empty_command',
        'stop_stale_revision_409', 'stop_unknown_operation_409',
        'stop_while_the_modules_must_be_restored_409_nothing_is_signalled', 'stop_without_naming_the_operation_409'),
    'R-02 reads that settle the writes: running, stopping, exited, applied, failed, refused, write failed, restoration, latest of the context, wall bar configuration': keys(
        CW, 'read_the_applied_calibration_by_operation', 'read_the_calibration_after_the_refused_apply',
        'read_the_calibration_after_the_refused_apply_nothing_changed', 'read_the_calibration_after_the_write_failed',
        'read_the_calibration_that_needs_its_modules_restored', 'read_the_calibration_that_was_asked_to_stop',
        'read_the_calibration_whose_worker_exited', 'read_the_configuration_after_the_wall_bar_apply',
        'read_the_failed_capture_after_the_apply', 'read_the_latest_calibration_of_the_context_after_a_lost_answer',
        'read_the_running_calibration_by_operation', 'read_the_running_observation_by_operation'),
    'R-03 frame.png: token, no token, display, ignored serial, warning stripped, bad display, not a PNG, capture failed, offline': keys(
        RI, 'frame_png_capture_failed_502', 'frame_png_device_offline_502',
        'frame_png_display_that_is_not_a_number_400', 'frame_png_ignores_a_serial_the_page_names',
        'frame_png_of_a_display_the_page_names', 'frame_png_remembered_under_the_token',
        'frame_png_that_is_not_a_png_502', 'frame_png_with_an_emulator_warning_before_the_pixels_is_stripped',
        'frame_png_without_a_token_is_not_remembered'),
    'R-03 cropper save: crop of the cached frame, truncation, overwrite consent, newest frame, provenance and screen not recognised/recorded': keys(
        RI, 'save_a_box_with_fractions_is_truncated', 'save_a_crop_of_the_cached_frame',
        'save_from_a_frame_fetched_for_another_account_409', 'save_from_a_frame_fetched_for_another_emulator_409', 'save_from_the_same_frame_back_on_the_account_it_was_fetched_for',
        'save_a_name_with_a_backslash_is_read_as_a_folder_separator', 'save_from_the_newest_frame',
        'save_over_an_existing_template_with_overwrite', 'save_over_an_existing_template_without_the_overwrite_flag_409',
        'save_when_the_provenance_cannot_be_recorded_the_file_is_still_written_ok_true', 'save_when_the_screen_cannot_be_recognised_still_saves_with_no_screen'),
    'R-03 cropper refusals: bounds, flat colour, no box, name, frame token expired/unknown/missing, decode, write failure, lock, malformed bodies': keys(
        RI, 'save_a_body_that_is_a_list_400', 'save_a_body_that_is_not_json_400',
        'save_a_box_below_the_frame_400', 'save_a_box_outside_the_frame_400',
        'save_a_box_smaller_than_6_pixels_400', 'save_a_box_that_is_null_400',
        'save_a_box_that_is_text_400', 'save_a_box_with_a_missing_side_400',
        'save_a_box_with_a_negative_corner_400', 'save_a_box_with_no_area_400',
        'save_a_flat_colour_box_400', 'save_a_name_that_climbs_out_of_templates_400',
        'save_a_name_that_is_not_a_png_400', 'save_a_name_with_a_drive_400',
        'save_a_name_without_a_folder_400', 'save_action_lock_another_action_in_progress',
        'save_from_a_cached_frame_that_does_not_decode_500', 'save_from_a_frame_that_left_the_cache_409',
        'save_from_a_frame_token_that_was_never_fetched_409', 'save_when_the_png_cannot_be_written_500',
        'save_without_a_box_400', 'save_without_a_frame_token_409'),
    'R-03 saved template reads and the template list/scan plan after a save': keys(
        RI, 'read_a_captured_template', 'read_a_template_name_that_is_not_allowed_403',
        'read_a_template_that_was_not_captured_404', 'read_a_template_with_another_extension_403',
        'read_the_saved_template', 'read_the_scan_plan_after_a_save_with_no_screen_learns_no_rectangle',
        'read_the_scan_plan_after_the_provenance_failed_nothing_is_verified', 'read_the_scan_plan_after_the_save_shows_the_cut_screen_and_its_verification',
        'read_the_template_list_after_the_names', 'read_the_template_list_after_the_save'),
    'R-03 artwork: asset library, manifest mapping, text badge, nothing known (204)': keys(
        RI, 'artwork_a_text_badge_even_when_a_library_exists', 'artwork_a_text_badge_for_a_control_the_game_draws_as_text',
        'artwork_from_the_asset_library_prefers_the_sprite', 'artwork_from_the_manifest_mapping_wins_over_the_name',
        'artwork_indexed_file_missing_204', 'artwork_library_index_unreadable_204',
        'artwork_nothing_known_for_the_target_204', 'artwork_the_library_has_no_image_of_that_name_204'),
    'R-04 screen analysis start: accepted, replay, receipt conflicts, context, owner, lock, malformed, worker not started': keys(
        SA, 'read_the_operation_of_the_failed_start', 'start_accepted',
        'start_action_lock_another_action_in_progress', 'start_body_not_json_is_read_as_an_empty_command',
        'start_body_is_not_valid_json_400', 'start_body_that_is_a_list_400', 'start_context_changed_409',
        'start_retry_of_a_failed_start_answers_the_failed_operation', 'start_retry_with_the_same_key_answers_the_same_operation',
        'start_same_key_with_another_command_is_refused', 'start_when_the_worker_cannot_be_started_is_a_text_500',
        'start_while_an_analysis_is_running_is_refused', 'start_while_another_operation_owns_the_device_409',
        'start_without_a_key_or_a_context_is_accepted'),
    'R-04 screen analysis status: idle, running with progress, done, no match, ambiguous, unknown screen, error, interrupted, unavailable, retained old result, cross-account': keys(
        SA, 'status_corrupt_shared_file_reads_as_idle', 'status_done_from_the_shared_file_of_an_untracked_analysis',
        'status_done_on_a_screen_that_could_not_be_told_apart', 'status_done_operation_whose_result_was_never_written_is_unavailable',
        'status_done_with_matches_and_an_ambiguous_one', 'status_done_without_any_match',
        'status_error_written_by_the_worker_in_the_shared_file', 'status_failed_operation_is_an_error_with_its_message',
        'status_failed_without_a_message_says_it_did_not_complete', 'status_idle_nothing_was_ever_analysed',
        'status_interrupted_operation_is_an_error_even_with_a_result_in_the_file', 'status_new_operation_running_does_not_show_the_previous_result',
        'status_of_another_workflow_409', 'status_of_the_first_account',
        'status_of_the_second_account_shows_nothing_of_the_first', 'status_result_changed_after_it_was_sealed_is_unavailable',
        'status_running_before_the_worker_wrote_anything', 'status_running_with_the_progress_of_the_matching',
        'status_unknown_operation_409', 'status_without_an_operation_reads_the_latest_of_the_context'),
    'R-04 screen analysis frame: by operation, revision ignored, unfinished, unsealed, changed, unknown/other workflow, shared file, none': keys(
        SA, 'frame_changed_after_it_was_sealed_409', 'frame_ignores_a_revision_the_page_holds_that_is_not_the_operations',
        'frame_of_a_finished_analysis_by_its_operation', 'frame_of_an_analysis_with_no_saved_screenshot_409',
        'frame_of_an_unfinished_analysis_409', 'frame_of_an_unknown_operation_409',
        'frame_of_another_workflow_409', 'frame_of_the_shared_file_when_no_operation_exists',
        'frame_with_no_operation_and_no_file_404', 'frame_without_an_operation_is_the_latest_of_the_context'),
    'R-05 clicker actions and frame preview': keys(
        RL, 'actions_listed', 'frame_preview_that_was_never_written_404',
        'frame_preview_written_by_the_clicker'),
    'R-05 clicker step: dry run, nested refusal under ok:true, uncertain execution, unknown outcome, context/owner/taps/lock refusals, malformed': keys(
        RL, 'step_action_lock_another_action_in_progress', 'step_another_account_400',
        'step_another_instance_400', 'step_body_not_json_is_read_as_an_empty_command_400',
        'step_action_that_is_not_text_400', 'step_body_is_not_valid_json_400', 'step_body_that_is_a_list_400', 'step_dry_run_needs_no_tap_permission',
        'step_dry_run_verifies_the_target_and_clicks_nothing', 'step_empty_body_400',
        'step_execute_clicked_but_the_destination_was_not_confirmed', 'step_execute_clicks_and_confirms_the_destination',
        'step_execute_given_as_a_number_is_refused_400', 'step_execute_given_as_null_is_refused_400', 'step_execute_given_as_text_is_refused_400', 'step_execute_given_as_text_with_taps_off_is_refused_400_before_the_taps_check', 'step_execute_the_screen_changed_before_the_click',
        'step_execute_with_taps_off_409', 'step_position_not_learned_yet_nothing_to_click',
        'step_the_check_refused_is_ok_true_outside_and_ok_false_inside', 'step_the_worker_printed_nothing',
        'step_the_worker_printed_something_that_is_not_json_is_an_unknown_outcome', 'step_the_worker_timed_out_is_an_unknown_outcome',
        'step_unknown_action_400', 'step_while_a_run_owns_the_device_409',
        'step_without_an_action_400'),
    "R-05 manifest contribution: export boundaries, excluded entries, discovery candidates, corrupt inputs, the analyzer's screen object": keys(
        RL, 'manifest_contribution_exports_verified_geometry_and_counts_what_it_excluded', 'manifest_contribution_ignores_an_analysis_that_is_not_done',
        'manifest_contribution_lists_discovery_candidates_from_a_done_analysis_of_a_known_screen', 'manifest_contribution_names_an_unknown_screen_unclassified',
        'manifest_contribution_with_a_corrupt_analysis_has_no_candidates', 'manifest_contribution_with_a_corrupt_report_has_no_targets',
        'manifest_contribution_with_the_screen_object_the_analyzer_writes_lists_candidates', 'manifest_contribution_with_a_screen_the_analyzer_could_not_tell_apart_is_unclassified', 'manifest_contribution_with_an_analysis_that_is_not_an_object_has_no_candidates', 'manifest_contribution_with_a_report_that_is_not_an_object_has_no_targets', 'manifest_contribution_skips_report_entries_that_are_not_what_the_calibrator_writes', 'manifest_contribution_without_a_report_has_no_targets'),
})

V1_REFERENCES.update({
    'R-01 runs and readiness (ready, image row, config row, module restoration, check raised, setup required) and the four calibrate-status states': [
        'runs.ready', 'runs.not_ready_image_row', 'runs.not_ready_config_row', 'runs.not_ready_module_restoration', 'runs.readiness_check_raised',
        'runs.setup_required_edit_run', 'runs.setup_required_apply_scan', 'runs.setup_required_battle_setup', 'runs.combo_entry',
        'runs.defined_and_undefined_with_labels', 'readiness.ready_with_context', 'readiness.not_ready_second_wind', 'readiness.setup_required',
        'readiness.unknown_preset', 'calibrate_status.idle', 'calibrate_status.running_with_steps',
        'calibrate_status.failed_setup_unverified_final_screen', 'calibrate_status.restore_pending'],
})
NOT_PRODUCIBLE.update({
    'R-06 targeted gap evidence for Range, First Perk, module and rare-dialog targets, and comparable scan metrics': (
        'No producer: the calibrator records report entries per target (calibrate_report.json) and the setup battle / final screen in its state '
        '(player/flow_capture.py _save_setup_battle, player/calibrate.py record_final_screen), and GET /api/calibrate/status echoes them (cases '
        'calibrate_status_setup_battle_*, calibrate_status_restoration_required_*, scan_plan_separates_*). Nothing in backend/ names a "gap" for '
        'those targets, summarises targeted results or records a scan duration/metric to compare (grep of backend/player, backend/control: no '
        'first_perk, rare_dialog, scan_metrics or comparable_*); README R-06 says "when their producer exists" and the screens brief keeps '
        'roadmap item 9 open: a UI fixture cannot close the live gate.'),
})
COVERAGE.update({
    # ---- Profiles review finding F6PE-P02: the context fence of the context-dependent writes
    'F6PE-P02 a write composed for the context in force is written (the context the page reads from GET /api/config)': keys(
        CF, 'read_config_the_page_context_comes_from', 'tier_with_the_matching_context_is_written', 'read_after_the_matching_tier',
        'copy_with_a_null_account_in_the_context_is_written'),
    'F6PE-P02 every context-dependent route refuses 409 context_changed after a switch, writes nothing, names the current context': keys(
        CF, 'read_config_after_another_client_switched_the_account', 'accounts_create_composed_for_alpha_is_refused_after_the_switch',
        'accounts_select_composed_for_alpha_is_refused_after_the_switch', 'account_tier_composed_for_alpha_is_refused_after_the_switch',
        'profile_activate_composed_for_alpha_is_refused_after_the_switch', 'profile_copy_composed_for_alpha_is_refused_after_the_switch',
        'profile_promote_composed_for_alpha_is_refused_after_the_switch', 'profile_patch_composed_for_alpha_is_refused_after_the_switch',
        'loadout_patch_composed_for_alpha_is_refused_after_the_switch', 'read_after_the_refusals_beta_tier_unchanged',
        'profile_files_after_the_refusals_nothing_was_created', 'read_after_the_refusals_no_account_was_created',
        'copy_for_an_account_that_is_not_bound_is_refused', 'tier_when_the_configuration_names_an_unknown_account_is_refused',
        'read_config_after_another_client_switched_the_account_for_the_loadout', 'loadout_composed_for_alpha_is_refused_with_its_expected_revision',
        'loadout_with_a_stale_context_and_a_stale_revision_is_told_the_context_changed', 'read_config_after_the_loadout_refusals_nothing_was_written'),
    'F6PE-P02 (R10-P01, R10-P03) a refusal reads and never seeds: a missing config.yaml, an empty or list document, a selected instance that is text each answer 409 context_changed with current_context null and write nothing': keys(
        CF, 'tier_when_config_yaml_is_missing_is_refused_and_creates_nothing', 'tier_when_config_yaml_is_empty_is_refused',
        'tier_when_config_yaml_is_a_list_is_refused', 'tier_when_config_yaml_is_a_selected_instance_that_is_text_is_refused'),
    'F6PE-P02 /api/loadout-patch (Equipment): the matching context is written with its expected_revision and the read shows it': keys(
        CF, 'read_config_before_the_loadout_save', 'loadout_with_the_matching_context_is_written', 'read_config_after_the_matching_loadout_holds_it'),
    'F6PE-P02 /api/loadout-patch: a present but malformed expected_context (explicit null included) is a coded 400 that writes nothing': keys(
        CF, 'loadout_expected_context_null_is_refused_not_read_as_absent', 'loadout_expected_context_not_an_object',
        'loadout_expected_context_missing_the_serial', 'loadout_expected_context_with_an_unknown_key', 'loadout_expected_context_serial_not_text',
        'read_config_after_the_malformed_loadout_requests_unchanged'),
    'F6PE-P02 /api/loadout-patch: the context is judged before the revision (a current context with a stale revision is revision_conflict), and an absent field is the legacy request': keys(
        CF, 'loadout_for_the_current_context_with_a_stale_revision_is_a_revision_conflict', 'loadout_without_the_field_is_not_fenced_the_legacy_page',
        'read_config_after_the_legacy_loadout_beta_holds_it'),
    'F6PE-P02 a present but malformed expected_context is a coded 400 (an explicit null included) and writes nothing': keys(
        CF, 'tier_expected_context_null_is_refused_not_read_as_absent', 'tier_expected_context_not_an_object',
        'tier_expected_context_missing_the_serial', 'tier_expected_context_with_an_unknown_key', 'tier_expected_context_serial_not_text',
        'accounts_expected_context_null_is_refused', 'read_after_the_malformed_requests_beta_tier_unchanged'),
    'F6PE-P02 an absent field is the legacy request (not fenced); the context is judged before the owner refusal; a resent switch is refused': keys(
        CF, 'tier_without_the_field_is_not_fenced_the_legacy_page', 'read_after_the_legacy_tier_beta_holds_it',
        'stale_tier_while_a_runner_owns_the_device_is_told_the_context_changed',
        'current_tier_while_a_runner_owns_the_device_is_refused_by_the_owner', 'account_switch_composed_for_alpha_is_committed',
        'the_same_request_resent_after_a_lost_answer_is_refused', 'read_after_the_retry_beta_is_active'),
})
COVERAGE.update({
    'M-02 BlueStacks inventory with one stopped and one running instance, and the Prepare answers for both (page-author request)': (
        keys(EBI, 'one_stopped_one_running_instance_adb_off_and_a_display_that_is_not_ok', 'prepare_the_running_instance_is_refused',
             'prepare_the_stopped_instance_is_refused_too_while_another_instance_runs', 'read_after_the_refusals_nothing_changed',
             'prepare_the_stopped_instance_once_nothing_runs', 'read_after_the_prepare_display_ok_and_adb_on', 'prepare_the_other_instance_is_already_prepared')),
    'F6C-R1-04 the first gathering policy of a profile with none (supported path: the run-template catalogue and profile-patch)': (
        keys(CFP, 'read_a_profile_with_no_gathering_policies', 'read_the_run_templates_that_carry_the_default_gathering_policies',
             'first_policy_with_a_stale_revision_is_refused', 'first_policy_that_is_empty_is_refused', 'first_policy_as_text_is_refused',
             'first_policy_with_an_unknown_key_is_refused', 'first_policy_without_its_gem_delay_is_refused', 'read_after_the_refusals_nothing_changed',
             'first_policy_created_from_the_farm_template_default', 'read_after_the_first_policy_holds_it_unbound', 'the_first_policy_bound_to_a_run',
             'a_second_policy_from_another_template', 'a_name_that_is_taken_is_replaced_not_refused', 'read_after_the_replacement',
             'read_a_profile_with_no_gather_key_at_all', 'first_policy_creates_the_missing_gather_map', 'read_after_the_gather_map_was_created')),
})
COVERAGE.update({
    'R12-01 Recognition apply of saved observations: a matching expected_context is applied; after a switch it is refused 409 context_changed with current_context and nothing is written; the legacy request still applies to the current profile': keys(
        RF, 'read_config_the_page_context_comes_from', 'apply_saved_with_the_matching_context_is_applied', 'read_the_profile_after_the_matching_apply',
        'read_config_after_another_client_switched_the_account', 'apply_saved_composed_for_alpha_is_refused_after_the_switch',
        'read_the_profile_the_refused_apply_would_have_changed', 'apply_saved_without_the_field_lands_on_the_current_profile_the_legacy_page',
        'read_the_profile_after_the_legacy_apply'),
    'R12-02 a malformed expected_context (null included) is 400 invalid_request and nothing is applied': keys(
        RF, 'apply_expected_context_null_is_refused_not_read_as_absent', 'apply_expected_context_not_an_object', 'apply_expected_context_missing_the_serial',
        'apply_expected_context_with_an_unknown_key_the_five_field_launch_shape', 'apply_expected_context_serial_not_text',
        'read_the_profile_after_the_malformed_requests_nothing_was_applied'),
    'R12-03 the fence is judged before the owner refusal; a resend after the switch is refused, not applied twice; a missing or unreadable configuration names no context': keys(
        RF, 'apply_saved_composed_for_alpha_is_applied_and_the_answer_is_lost', 'the_same_request_resent_after_the_switch_is_refused_not_applied_twice',
        'stale_apply_while_a_runner_owns_the_device_is_told_the_context_changed', 'current_apply_while_a_runner_owns_the_device_is_refused_by_the_owner',
        'apply_saved_when_config_yaml_is_missing_is_refused_and_creates_nothing', 'apply_saved_when_the_configuration_names_an_unknown_account_is_refused'),
    'R12-04 apply by operation: the fence adds to the operation\'s own context check; an applied operation resent after a switch is refused by the fence': keys(
        RF, 'apply_by_operation_with_the_matching_context_is_applied', 'apply_by_operation_composed_for_alpha_is_refused_by_the_fence_after_the_switch',
        'read_the_calibration_after_the_fence_refusal_it_still_awaits_apply', 'apply_by_operation_without_the_field_after_the_switch_is_refused_by_the_operation_check',
        'apply_by_operation_is_applied_and_the_answer_is_lost', 'the_resend_of_an_applied_operation_after_the_switch_is_refused_by_the_fence',
        'read_the_calibration_whose_apply_the_page_must_settle_by_reading'),
    'R12-05 the other Recognition writes: start, observe and screen-analysis start are refused by the frozen five-field context; Stop is a revocation, never refused for a context': keys(
        RF, 'calibrate_start_composed_for_alpha_is_refused_after_the_switch', 'calibrate_observe_composed_for_alpha_is_refused_after_the_switch',
        'screen_analysis_start_composed_for_alpha_is_refused_after_the_switch', 'stop_composed_for_alpha_after_the_switch_is_not_refused_a_stop_is_a_revocation'),
    'RL2 reset every live override of the battle at once (null for several paths in one request); the reads before and after; the identical resend; a partial reset': keys(
        RESET, 'two_overrides_set_before_the_reset', 'read_before_the_reset_two_overrides_are_in_force', 'accepted_null_for_every_override_resets_them_together',
        'read_after_every_override_was_reset', 'identical_resend_of_the_reset_answered_from_the_record', 'reset_of_paths_that_no_longer_hold_an_override',
        'read_after_the_second_reset_nothing_to_remove', 'three_overrides_set', 'reset_two_of_the_three_keeps_the_third', 'read_after_the_partial_reset_one_override_remains'),
    'RL2 refused resets: stale control revision, an unknown path refuses the whole request, no path; the overrides remain': keys(
        RESET, 'overrides_set', 'reset_all_with_a_stale_control_revision_refused', 'reset_all_with_one_unknown_path_refuses_the_whole_request',
        'read_after_the_refusals_both_overrides_remain', 'reset_with_no_path_refused'),
    'RL3 saved entry settings: every field kind of a coin, dissonance, tournament and shard run is accepted, one field per request': keys(
        ENTRY, 'read_before_the_first_save', *[case for _, case, _, _ in ENTRY_SAVES], *[case for _, case, _ in ENTRY_DELETES], 'read_after_the_saves_holds_every_change'),
    'RL3 refused entry values: the validator text, nothing written': keys(
        ENTRY, *[case for _, case, _, _ in ENTRY_REFUSALS], *[case for _, case, _ in ENTRY_DELETE_REFUSALS], 'read_after_the_refusals_nothing_changed'),
    'RL3 stale revision (judged before the values), starter, scan draft, unknown profile and unknown run': keys(
        ENTRY, 'stale_revision_409', 'stale_revision_is_judged_before_the_values', 'read_after_the_stale_refusals_holds_the_other_clients_save',
        'starter_profile_refused_with_a_flag', 'scan_draft_refused', 'unknown_profile_404', 'unknown_run_is_a_validator_problem'),
    'RL3 write failures: validator unavailable, write failed, written then the answer failed; the reads that settle them; the identical resend is stale': keys(
        ENTRY, 'validator_unavailable_nothing_written_500', 'read_after_the_validator_failure_the_revision_is_unchanged', 'write_failed_nothing_saved_500',
        'read_after_the_failed_write_the_revision_is_unchanged', 'written_then_the_answer_failed_500', 'read_after_an_error_that_followed_the_write_holds_the_change',
        'identical_resend_after_a_committed_write_is_refused_as_stale'),
    'RL3 malformed patch paths': keys(
        ENTRY, 'path_to_a_run_that_is_not_a_mapping_step_400', 'path_of_one_step_into_the_blueprints_only_400', 'read_after_the_malformed_paths_nothing_changed'),
    'RL4 add a run from every shipped recipe, with its choices and both optional policies; the reads before and after': keys(
        TEMPLATES, 'read_before_the_first_add', 'read_the_recipes_the_page_offers', *[case for _, case, _, _ in ADD_STEPS],
        'read_after_the_adds_holds_every_new_run_and_its_copied_policies'),
    'RL4 refused adds: identifier, recipe, choices and the validator; nothing written': keys(
        TEMPLATES, *[case for case, _, _, _, _ in ADD_REFUSALS], 'read_after_the_refusals_nothing_changed', 'a_copied_policy_id_already_present_is_refused',
        'read_after_the_policy_clash_nothing_changed'),
    'RL4 malformed requests, starter and draft profiles, an unknown or unreadable profile': keys(
        TEMPLATES, 'body_a_list_400', 'profile_missing_400', 'profile_name_with_a_path_400', 'profile_not_text_400', 'template_missing_400', 'run_id_missing_400',
        'body_not_json_400', 'read_after_the_malformed_requests_nothing_changed', 'starter_profile_refused', 'scan_draft_refused',
        'unknown_profile_is_refused_with_the_operating_system_error', 'profile_file_unreadable_400', 'profile_file_not_a_mapping_400'),
    'RL4 a lost answer is settled by reading the profile; the identical resend is refused (the run exists); write failures': keys(
        TEMPLATES, 'added_a_run_and_the_answer_is_lost', 'identical_resend_after_a_committed_add_is_refused_the_run_exists', 'read_after_the_resend_holds_the_one_run',
        'write_failed_nothing_saved', 'read_after_the_failed_write_the_revision_is_unchanged', 'written_then_the_answer_failed',
        'read_after_an_error_that_followed_the_write_holds_the_run', 'identical_resend_after_the_error_is_refused_the_run_exists'),
    'RL7 battle details: a Floating gems refusal recorded by the tapper and an Ad gems offer that was seen and failed': keys(
        DETAILS, 'floating_gems_refusal_recorded', 'ad_gems_offer_seen_and_failed_attempt'),
    'RL8 remove the whole plan section: saved, the reads, an absent section, a stale revision, no expected revision': keys(
        PLAN_DELETE, 'read_before_the_plan_section_was_deleted', 'saved_the_whole_plan_section_deleted', 'read_after_the_plan_section_was_deleted',
        'plan_section_delete_when_absent', 'read_after_the_delete_of_an_absent_plan_section', 'plan_section_delete_stale_revision_409',
        'read_after_the_stale_refusal_the_plan_section_remains', 'plan_section_delete_without_an_expected_revision_is_accepted'),
})
COVERAGE.update({
    'RTF-01 run-templates/add with the optional expected_revision and expected_context: accepted, the answer carries the new revision (only with the revision), chained adds, one guard alone, the resend after the commit is stale': keys(
        TF, 'read_before_the_first_add', 'read_config_the_page_context_comes_from', 'added_with_the_matching_revision_and_context', 'added_again_with_the_revision_the_answer_returned', 'read_after_the_chained_adds_the_revision_is_the_last_answers', 'added_with_only_the_revision_no_context', 'added_with_only_the_context_answers_without_a_revision', 'resend_of_the_first_request_after_it_committed_is_refused_stale', 'read_after_the_resend_the_profile_holds_each_run_once'),
    'RTF-02 a stale expected_revision (null, not text and empty included) is 409 revision_conflict with the current revision, judged before the recipe and its choices, nothing written': keys(
        TF, 'stale_revision_409_nothing_written', 'stale_revision_is_judged_before_the_recipe_and_its_choices', 'current_revision_with_the_same_bad_recipe_is_refused_by_the_recipe', 'expected_revision_null_is_a_stale_revision', 'expected_revision_not_text_is_a_stale_revision', 'expected_revision_empty_is_a_stale_revision', 'read_after_the_refusals_holds_the_other_clients_save'),
    'RTF-03 expected_context: a switch is 409 context_changed + current_context, judged before the revision; malformed or null is 400 invalid_request; absent is the legacy request; a missing configuration names no context': keys(
        TF, 'read_config_after_another_client_switched_the_account', 'composed_for_alpha_is_refused_after_the_switch', 'the_context_is_judged_before_the_revision', 'the_current_context_with_a_stale_revision_is_a_revision_conflict', 'read_the_profile_the_refused_add_would_have_changed', 'expected_context_null_is_refused_not_read_as_absent', 'expected_context_not_an_object', 'expected_context_missing_the_serial', 'expected_context_serial_not_text', 'without_the_fields_the_legacy_request_adds_to_the_named_profile', 'context_when_config_yaml_is_missing_is_refused_and_creates_nothing'),
    'RTF-04 a lost answer is settled by reading the profile; the resend is stale (or refused by the run identifier without the revision); write failures': keys(
        TF, 'added_and_the_answer_is_lost', 'the_identical_resend_is_refused_stale_not_applied_twice', 'read_settles_the_lost_answer_the_run_is_there_once', 'the_resend_without_the_revision_is_refused_by_the_run_identifier', 'write_failed_nothing_saved', 'read_after_the_failed_write_the_revision_is_unchanged', 'written_then_the_answer_failed', 'read_after_an_error_that_followed_the_write_holds_the_run', 'identical_resend_after_the_error_is_refused_stale'),
    'RTF-05 starter, scan draft and unknown profile with the guards': keys(
        TF, 'starter_profile_refused_with_the_guards', 'scan_draft_refused_with_the_guards', 'unknown_profile_with_the_guards'),
    'F6-12 saved queue (run-controls): empty, weighted/cadence/order, round robin saved; the reads after; another run keeps its own plan': keys(
        SQ, 'shopping_read_before_any_save', 'shopping_weighted_cadence_order_saved', 'shopping_read_after_save', 'shopping_other_run_after_private_save', 'shopping_empty_queue_saved', 'shopping_round_robin_saved'),
    'F6-12 legacy conversion previews (list, mapping, best cost) and the reviewed conversion saved': keys(
        SQ, 'shopping_legacy_list_preview', 'shopping_reviewed_conversion_saved', 'shopping_read_after_the_reviewed_conversion', 'shopping_legacy_mapping_preview', 'shopping_legacy_best_cost_preview'),
    'F6-12 saved queue refused: invalid weight, a run that does not support the plan': keys(
        SQ, 'shopping_invalid_weight_refused', 'shopping_read_after_the_refusal_nothing_changed', 'refused_the_shopping_plan_the_run_does_not_support'),
    'F6-12 saved queue: the reply failed after the write, the read that settles it, the stale resend': keys(
        SQ, 'shopping_read_before_the_unanswered_save', 'shopping_save_reply_failed', 'shopping_read_after_unknown_save', 'shopping_identical_resend_after_the_unknown_save_is_refused_stale'),
    'F6-12 live queue command: accepted, requested and applied reads, routine overrides preserved, reply unknown, identical replay, key conflict': keys(
        SQ, 'shopping_plan_accepted', 'shopping_plan_read_requested', 'shopping_plan_read_applied', 'shopping_routine_overrides_set', 'shopping_plan_preserves_routine_overrides', 'shopping_plan_requested_over_routine_overrides_read', 'shopping_plan_routine_overrides_applied', 'shopping_plan_reply_unknown', 'shopping_plan_unknown_read', 'shopping_plan_identical_replay', 'shopping_plan_key_conflict'),
    'F6-12 manual shopping actions: whole, attack, defense, utility and one upgrade accepted; requested, started and completed-without-purchase reads': keys(
        SQ, 'shopping_whole_accepted', 'shopping_attack_accepted', 'shopping_defense_accepted', 'shopping_utility_accepted', 'shopping_upgrade_accepted', 'shopping_upgrade_requested', 'shopping_upgrade_started', 'shopping_upgrade_completed_without_purchase'),
    'F6-12 manual shopping on a legacy driver is refused; the upgrade reply unknown and its identical replay': keys(
        SQ, 'shopping_legacy_refused', 'shopping_upgrade_reply_unknown', 'shopping_upgrade_identical_replay'),
    'F6-12 shared policy library (profile-patch on policies.shopping_lists): created, copied, v2 updated, legacy list and mapping, invalid clicks, deleted unused, delete of a used list refused': keys(
        SQ, 'shopping_library_read_before_any_edit', 'shopping_policy_created', 'shopping_policy_copied', 'shopping_shared_v2_updated', 'shopping_legacy_list_updated', 'shopping_legacy_mapping_updated', 'shopping_legacy_clicks_invalid', 'shopping_library_read_after_the_edits', 'shopping_policy_deleted_unused', 'shopping_policy_delete_used_refused'),
    'F6-12 shared policy library: revision conflict, context changed, reply failed and the read that settles it': keys(
        SQ, 'shopping_shared_revision_conflict', 'shopping_shared_context_changed', 'shopping_shared_reply_failed', 'shopping_shared_read_after_unknown'),
    'F6-14 unbind the profile (profile-activate name null) with a matching legacy or account context; the config reads after': keys(
        PTG, 'unbind_with_matching_legacy_context', 'read_after_unbind_legacy_context', 'unbind_with_matching_account_context', 'read_after_unbind_account_context'),
    'F6-14 unbind refused: a runner is listed, after an account switch, after an active-profile switch (the context is judged first)': keys(
        PTG, 'unbind_refused_while_runner_listed', 'read_after_unbind_runner_refusal', 'unbind_refused_after_account_switch', 'unbind_refused_after_active_profile_switch'),
    'F6-14 unbind: the save failed before the write, written then the answer failed; the config reads that settle them': keys(
        PTG, 'unbind_failed_before_write', 'read_after_unbind_failed_before_write', 'unbind_written_then_answer_failed', 'read_after_unbind_written_then_answer_failed'),
    'F6-14 an inactive target of the same account: the config, the list and the source it reads': keys(
        PTG, 'inactive_editor_context_before_save', 'same_account_inactive_target_listed', 'same_account_inactive_target_source'),
    'F6-14 inactive target saves (plan, gathering policies, chores, UW and rescue policies, run entry label and equipment) while a runner is listed; the reads after': keys(
        PTG, 'inactive_plan_saved_while_runner_listed', 'inactive_gather_label_saved', 'inactive_gather_copy_saved', 'inactive_gather_template_created', 'inactive_unused_gather_deleted', 'inactive_chores_saved', 'inactive_uw_policy_saved', 'inactive_rescue_policy_saved', 'inactive_run_entry_label_saved', 'inactive_run_entry_loadout_saved', 'inactive_target_read_after_save', 'inactive_editor_context_after_save'),
    'F6-14 inactive target: stale revision, deleted target (read, list, patch), starter and scan draft refused': keys(
        PTG, 'inactive_target_stale_revision', 'inactive_target_after_deletion', 'inactive_target_list_after_deletion', 'inactive_target_deleted_before_patch', 'inactive_target_starter_refused', 'inactive_target_draft_refused'),
    'F6-14 context fences of the inactive target: refused after an active-profile or account switch; the config reads after an active-profile, instance and serial change': keys(
        PTG, 'read_config_after_editor_active_profile_switch', 'inactive_target_patch_refused_after_active_switch', 'inactive_target_patch_refused_after_account_switch', 'read_config_after_editor_instance_switch', 'read_config_after_editor_serial_change'),
    'F6-14 inactive target: write failed, written then the answer failed, the reads that settle them': keys(
        PTG, 'inactive_target_write_failed', 'inactive_target_after_write_failed', 'inactive_target_written_then_answer_failed', 'inactive_target_after_written_then_answer_failed'),
})
COVERAGE.update({
    'P10B-01 environment report: a source checkout with nothing installed, with every prerequisite, a portable release without the decoder, an unreadable or unrecognized release identity, another operating system, the emulator locators': keys(
        ENV, 'source_checkout_nothing_installed', 'source_release_every_prerequisite_present', 'portable_release_scrcpy_server_present_decoder_missing',
        'release_identity_that_cannot_be_read_is_unknown', 'release_identity_of_an_unrecognized_kind_is_unknown',
        'other_operating_system_is_reported_as_it_is', 'emulator_locators_find_bluestacks_not_mumu'),
    'P10B-02 adb: platform-tools with a matching, absent or other-version receipt and an unreadable folder; stream backends with adb missing, from the local tools, configured, gone, and an invalid preview setting': keys(
        ENV, 'platform_tools_installed_without_a_receipt_version_unknown', 'platform_tools_receipt_does_not_match_the_pin_version_unknown',
        'platform_tools_with_the_matching_receipt', 'platform_tools_folder_cannot_be_read_state_unknown', 'stream_backends_nothing_installed_adb_missing',
        'stream_backends_adb_from_the_local_tools_and_scrcpy_ready', 'stream_backends_install_unreadable_adb_unknown', 'stream_backends_configured_adb_executable_found',
        'stream_backends_configured_adb_executable_gone_falls_back_to_the_local_tools', 'stream_backends_configured_adb_executable_gone_and_no_local_tools',
        'stream_backends_invalid_preview_setting_is_refused'),
    'P10B-03 OCR probe before it runs: script present but never probed; invalid requests (no body, array, empty, other probe, extra key, JSON null, text); the action-lock refusal and the read after': keys(
        ENV, 'ocr_script_present_but_never_probed_state_unknown', 'probe_without_a_body', 'probe_with_an_array', 'probe_with_an_empty_object',
        'probe_with_another_probe_name', 'probe_with_an_extra_key', 'probe_with_json_null', 'probe_with_text_that_is_not_json',
        'probe_refused_while_the_action_lock_is_held', 'read_after_the_refused_probe_still_unknown'),
    'P10B-04 OCR probe outcomes and the reads after: success; script missing, text not recognized, nonzero exit, stderr, timeout, script host missing, budget used in preparation, another operating system; success replacing a failure': keys(
        ENV, 'probe_recognizes_the_synthetic_text', 'read_after_the_successful_probe', 'probe_when_the_ocr_script_is_missing', 'read_after_the_missing_script_probe',
        'probe_exit_zero_but_the_text_was_not_recognized', 'probe_nonzero_exit', 'probe_stderr_output_is_not_success', 'probe_exceeds_the_ten_second_budget',
        'probe_cannot_start_the_script_host', 'read_after_the_last_failed_probe', 'probe_preparation_used_the_whole_budget_nothing_is_run',
        'probe_on_another_operating_system_needs_windows', 'probe_succeeds_after_the_failures_and_replaces_the_stored_result',
        'read_after_the_probe_that_replaced_the_failure'),
})
COVERAGE.update({
    'F6-13 preview status: a session that never existed, one that was evicted, one that is reserved and not yet read': keys(
        PRS, 'missing_session', 'evicted_session', 'idle_reserved'),
    'F6-13 preview status per source (MuMu, scrcpy, ADB): first frame, streaming, port closed (error marker), stale, closed': keys(
        PRS, 'closed_adb', 'closed_mumu', 'closed_scrcpy', 'failed_adb', 'failed_mumu', 'failed_scrcpy', 'first_frame_adb', 'first_frame_mumu', 'first_frame_scrcpy', 'stale_adb', 'stale_mumu', 'stale_scrcpy', 'streaming_adb', 'streaming_mumu', 'streaming_scrcpy'),
    'F6-13 preview status: fallbacks (MuMu to scrcpy, MuMu and scrcpy to ADB, scrcpy to ADB, an explicit MuMu falling back) and the cleanup failure': keys(
        PRS, 'fallback_mumu_to_scrcpy', 'fallback_mumu_scrcpy_to_adb', 'fallback_scrcpy_to_adb', 'requested_mumu_falls_back_to_scrcpy', 'cleanup_failed'),
    'F6-13 preview status around an error marker and after the quality presets': keys(
        PRS, 'error_marker_status_after_recovery', 'error_marker_status_before_recovery', 'status_after_balanced', 'status_after_fractional_fps', 'status_after_low'),
    'F6-13 preview backends: all available, ADB transport offline, MuMu unavailable, scrcpy package and/or decoder missing, invalid saved fps, fractional fps, the other emulator': keys(
        PRB, 'adb_available_transport_offline', 'all_available', 'invalid_saved_fps', 'mumu_unavailable', 'other_instance', 'saved_scrcpy_fractional_fps', 'scrcpy_decoder_missing', 'scrcpy_package_and_decoder_missing', 'scrcpy_package_missing'),
    'F6-13 preview stream starts per source (MuMu native, scrcpy, ADB, Auto) with the provenance of every part': keys(
        PRM, 'start_mumu', 'start_scrcpy', 'start_adb', 'start_auto'),
    'F6-13 preview stream fallbacks and the error marker that recovers': keys(
        PRM, 'fallback_mumu_to_scrcpy', 'fallback_mumu_scrcpy_to_adb', 'fallback_scrcpy_to_adb', 'requested_mumu_falls_back_to_scrcpy', 'error_marker_then_recovery'),
    'F6-13 preview stream quality presets (Balanced, Low) and a fractional frame rate': keys(
        PRM, 'balanced', 'low', 'fractional_fps'),
    'F6-13 preview stream refusals (HEAD reserves nothing, wrong emulator, duplicate id, too many streams) and invalid parameters and saved settings': keys(
        PRM, 'head_no_capture', 'wrong_instance', 'duplicate_active', 'capacity_full', 'invalid_backend', 'invalid_display', 'invalid_fps', 'invalid_quality', 'invalid_saved_settings', 'invalid_scale', 'invalid_stream_id'),
    'F6-13 preview stream while automation owns the screen: a normal stream, no input': keys(
        PRM, 'start_while_automation_owns_screen'),
    'F6-13 frame.png with its headers: no token, a display that is not a number, not a PNG, capture failed': keys(
        PRP, 'frame_png_capture_failed_502', 'frame_png_display_that_is_not_a_number_400', 'frame_png_that_is_not_a_png_502', 'frame_png_without_a_token_is_not_remembered'),
})
NOTES.update(
    preview_status=('F6-13: GET /api/stream/status?stream_id=. 200 WITHOUT an `ok` key (the session record: counters, timing, requested and actual backend, backend_failures [{backend, reason, stage}], frame_age_ms, '
                    'active, last_error, last_frame_age_s, actual_fps); a session that never existed or was evicted is 404 {ok:false, error}. Sessions are made by the real stream route and read while its response is open, '
                    'closed, stale or failing; the sources are synthetic (12x24 frames, 10 ms capture, 2 ms JPEG encode on the fixed clock, a scrcpy age of 50 ms when a frame completes) and fail or fall back through the '
                    'real PreviewCapture. A status read between two parts is a snapshot: the part in flight is identified by its own headers, not by this record.'),
    preview_backends=('F6-13: GET /api/stream/backends reports local prerequisites only (MuMu native: a target or a reason; scrcpy: installed, decoder, reason, version, server_path; adb: the p10b report). '
                      'Availability is not a connection: the transport-offline sample equals the available one, and the route talks to adb never. The scrcpy package states are produced from a synthetic server file '
                      'and the real verification (a missing file reads "scrcpy server missing or unreadable: <path>", not the brief\'s proposed text). A saved preview setting that is invalid is 400 {ok:false, error}.'),
    preview_stream=('F6-13: GET /api/stream.mjpg. 200 multipart/x-mixed-replace; boundary=frame, Cache-Control no-cache. Each part carries Content-Type, Content-Length and FOUR provenance headers made with the part\'s own '
                    'pixels (added with this contract): X-Preview-Frame-Kind frame|error, X-Preview-Sequence (successful frames when the part was made; an error marker repeats the last number), X-Preview-Backend '
                    'mumu|scrcpy|adb|unknown, X-Preview-Source-Age-Ms a number|unknown. A recorded stream is its response headers and its first parts, decoded (width, height, encoded_bytes and the sha256 of the decoded pixels; '
                    'termination "recorder_closed" says the recorder ended it). The error marker is drawn text, so only its kind and size are recorded. HEAD validates and reserves nothing (empty body). '
                    'Refusals are JSON {ok:false, error}: 400 invalid parameter or saved setting, 409 another emulator or an active duplicate id, 429 32 active sessions. There is no owner refusal: a stream starts '
                    'while automation owns the screen and sends no input.'),
    preview_frame_png=('F6-13: GET /api/frame.png with the headers it sends (the answers also exist in recognition_images, which records none): Content-Type only, no Cache-Control, and no age or source header; '
                       'a failed capture is a text 502 (Flask\'s default text/html), an invalid display a 400 JSON.'),
)
COVERAGE.update({
    'F6-15 settings modules: instance defaults, selected emulator, run defaults, tournament deck changes, and the loop, missions and fleet machine defaults - saved, stale revision, the read after the save': keys(
        SM, 'saved_instance_defaults', 'stale_revision_instance_defaults', 'read_after_saved_instance_defaults', 'saved_active_instance', 'stale_revision_active_instance',
        'read_after_saved_active_instance', 'saved_run_defaults', 'stale_revision_run_defaults', 'read_after_saved_run_defaults', 'saved_tournament_cards',
        'stale_revision_tournament_cards', 'read_after_saved_tournament_cards', 'saved_loop_defaults', 'stale_revision_loop_defaults', 'read_after_saved_loop_defaults',
        'saved_missions_defaults', 'stale_revision_missions_defaults', 'read_after_saved_missions_defaults', 'saved_fleet_defaults', 'stale_revision_fleet_defaults',
        'read_after_saved_fleet_defaults'),
    'F6-15 the configuration read right after the selected emulator was switched (both accounts and the legacy defaults unchanged)': keys(SM, 'read_config_after_saved_active_instance'),
    'F6-15 invalid values of the new modules: string, enum, boolean, string list, number, range, integer; nothing is written': keys(
        SM, 'invalid_string_instance_defaults', 'invalid_enum_instance_defaults', 'invalid_boolean_instance_defaults', 'invalid_enum_active_instance',
        'invalid_enum_run_defaults', 'invalid_string_list_tournament_cards', 'invalid_number_loop_defaults', 'invalid_boolean_loop_defaults',
        'invalid_range_missions_defaults', 'invalid_integer_fleet_defaults'),
    'F6-15 loadout restore values (cards_restore, modules_restore): saved, refused for a card preset or module the account does not have, and the configuration read after each save': keys(
        LR, 'saved_cards_restore', 'refused_cards_restore', 'read_after_saved_cards_restore', 'saved_modules_restore', 'refused_modules_restore', 'read_after_saved_modules_restore'),
})
NOTES.update(
    settings_modules=('F6-15 / r12b: the cases the brief names settings.<case> and loadout_patch.<case> are in the NEW families settings_modules and loadout_restore, under the brief\'s own case names: appended families keep every published family\'s ids and hash. '
                      'GET/POST /api/settings gained seven typed modules, so EVERY snapshot in the `settings` family now lists them (that is why the family `settings` changed): instance-defaults (label, default preset, autostart of the selected emulator), active-instance (the selected emulator), run-defaults (the machine fallback preset), '
                      'tournament-cards (the standing deck drop/add lists; a `string-list` field: ordered, non-blank strings, `maxItems` 50, nothing coerced or deduplicated) and loop-defaults, missions-defaults, fleet-defaults '
                      '(the ROOT blocks, apart from the selected emulator\'s override, which the modules loop, missions and fleet keep editing). Preset choices are the runnable presets (a placeholder is not offered) and the '
                      'instance default also offers the empty value "Use machine fallback". A bound account\'s tournament lists are saved under that account. Saving active-instance is a selection-only write: the stored configuration differs from before in `active_instance` and nothing else, so no account record changes, whatever it stores (a sparse record is not filled in; settings_switch_records); '
                      'an emulator whose account is unknown is refused before anything is written. A stale revision or context is 409 whatever the values; one invalid field writes none. The restore values cards_restore and modules_restore are keys of a loadout body '
                      'validated like `cards` and `modules` (the card preset must be on the account, the module owned), and are editable only on a loadout a run of the ACTIVE profile names (loadout_restore_eligibility).'),
    loadout_restore=('POST /api/loadout-patch with cards_restore and modules_restore (see settings_modules): the whole loadout body, config expected_revision and the reviewed expected_context; the config read after each save.'),
    loadout_restore_eligibility=('r12b2 (review R1): POST /api/loadout-patch refuses a restore edit it cannot check. The profile validator reads the loadouts a run of the ACTIVE profile names and no others, so a body that sets, changes or clears `cards_restore` or '
                                 '`modules_restore` of any other loadout - or any loadout when there is no active profile - is 409 {ok:false, code:"restore_loadout_unused", loadout, restore_keys, active_profile, error} and writes nothing, whether the value looks valid or not '
                                 '(ownership is the point, not the value). Order of the checks: context, revision, the loadout exists (404), this guard, then the validator\'s problems (400). A body whose restore keys are as stored is not a restore edit and is judged as it always was.'),
    settings_switch_records=('r12b2 (review R2): saving the active-instance module writes only `active_instance`. Over accounts that store only what they were given (no loadouts, no tweaks, an unsorted profile list, no profile list) every record is exactly as it was, '
                             'in the file and in the configuration read afterwards; the answer is the snapshot of the new context.'),
)
COVERAGE.update({
    'F6-15 r12b2 R1: a restore value (cards_restore, modules_restore) of a loadout no run of the active profile names is refused with 409 restore_loadout_unused, nothing written - unknown or owned value, cards or modules, both together': keys(
        LE, 'refused_unknown_cards_restore_on_a_loadout_no_run_uses', 'refused_owned_cards_restore_on_a_loadout_no_run_uses', 'refused_unknown_modules_restore_on_a_loadout_no_run_uses',
        'refused_owned_modules_restore_on_a_loadout_no_run_uses', 'refused_both_restore_values_on_a_loadout_no_run_uses', 'read_after_the_refusals_nothing_changed'),
    'F6-15 r12b2 R1: the same refusal when only an inactive profile names the loadout, when there is no active profile, and when a stored restore value would be cleared': keys(
        LE, 'refused_restore_when_only_an_inactive_profile_names_the_loadout', 'refused_restore_with_no_active_profile_nothing_to_check_it_against', 'read_after_the_no_profile_refusal_nothing_changed',
        'refused_clearing_the_stored_restore_value_of_a_loadout_no_run_uses', 'read_after_the_clearing_refusal_the_stored_value_remains'),
    'F6-15 r12b2 R1 controls: an unused loadout still saves without restore edits or with its restore values as stored; the revision is judged first; the loadout a run uses still saves its restore value': keys(
        LE, 'unused_loadout_saved_without_restore_values_is_unchanged_behaviour', 'unused_loadout_saved_with_its_stored_restore_value_unchanged', 'stale_revision_is_judged_before_the_restore_guard',
        'owned_cards_restore_saved_on_the_loadout_the_active_profile_uses'),
    'F6-15 r12b2 R2: switching the selected emulator over sparse accounts writes only the selection (every account record as stored), and back': keys(
        SR, 'saved_active_instance_between_sparse_accounts', 'read_after_the_sparse_switch', 'read_config_after_the_sparse_switch', 'saved_active_instance_back_to_the_first_sparse_account',
        'read_config_after_switching_back_every_record_as_it_was'),
})
COVERAGE.update({
    'F6-11 r12d: a run\'s kind is fixed - a change by the kind field, by replacing the whole run, or by replacing the whole blueprints mapping is 400 kind_change_refused (the changes are named, sorted by run), nothing written': keys(
        KG, 'kind_changed_on_a_coin_run_is_refused', 'whole_run_replaced_by_a_run_of_another_kind_is_refused', 'whole_blueprints_mapping_with_two_kinds_changed_names_both',
        'read_before_the_kind_attempts', 'read_after_the_attempts_every_kind_is_as_it_was', 'other_field_of_the_run_still_saves_after_the_refusal'),
    'F6-11 r12d: a kind change the validator already refuses keeps the validator\'s answer (no code, problems)': keys(
        KG, 'kind_deleted_keeps_the_validators_answer', 'kind_unknown_keeps_the_validators_answer', 'kind_changed_on_a_coin_run_with_coin_only_fields_keeps_the_validators_answer',
        'kind_changed_on_a_shard_run_keeps_the_validators_answer'),
    'F6-11 r12d: what is not a kind change - the kind a run already has, a whole run of the same kind, a run added, a run removed, removed and added again with another kind': keys(
        KG, 'kind_set_to_the_kind_the_run_already_has_is_accepted', 'whole_run_replaced_by_a_run_of_the_same_kind_is_accepted', 'run_added_with_a_kind_of_its_own_is_accepted',
        'run_removed_is_accepted', 'run_added_again_with_another_kind_after_it_was_removed_is_accepted', 'read_before_the_changes_that_are_not_kind_changes',
        'read_after_the_changes_that_are_not_kind_changes'),
    'F6-11 r12d: the revision and the context are judged before the guard, and a matching context does not let a kind change through': keys(
        KG, 'stale_revision_is_judged_before_the_guard', 'another_context_is_judged_before_the_guard', 'matching_context_and_revision_do_not_let_a_kind_change_through',
        'read_after_the_ordered_refusals_nothing_changed'),
    'F6-14b Schedule: the day plan of the ACTIVE profile saved and removed with expected_context and expected_revision, each followed by the read': keys(
        PFD, 'active_plan_saved', 'read_after_active_plan_saved', 'active_plan_deleted', 'read_after_active_plan_deleted'),
    'F6-14b Collection: the active profile\'s gathering policy label, a copy, one created from the shipped template, an unused one deleted, and the chores list (fenced), each followed by the read': keys(
        PFD, 'active_gather_label_saved', 'read_after_active_gather_label_saved', 'active_gather_copy_saved', 'read_after_active_gather_copy_saved',
        'active_gather_template_created', 'read_after_active_gather_template_created', 'active_unused_gather_deleted', 'read_after_active_unused_gather_deleted',
        'active_chores_saved', 'read_after_active_chores_saved'),
    'F6-14b Abilities: an Ultimate Weapon policy and a rescue policy of the active profile saved, then deleted while unused (fenced), each followed by the read': keys(
        PFD, 'active_uw_policy_saved', 'read_after_active_uw_policy_saved', 'active_uw_policy_deleted', 'read_after_active_uw_policy_deleted',
        'active_rescue_policy_saved', 'read_after_active_rescue_policy_saved', 'active_rescue_policy_deleted', 'read_after_active_rescue_policy_deleted'),
    'F6-14b Runs: a run\'s label saved and its perk bans deleted in the active profile (fenced), each followed by the read': keys(
        PFD, 'active_run_entry_label_saved', 'read_after_active_run_entry_label_saved', 'active_run_entry_field_deleted', 'read_after_active_run_entry_field_deleted'),
    'F6-14b: a fenced write to the active profile after the account was switched is 409 context_changed, nothing written, and the read shows mine unchanged': keys(
        PFD, 'active_patch_refused_after_account_switch', 'read_after_active_patch_refused_after_account_switch'),
})
COVERAGE.update({
    'F6-11 r12c (a): id_taken - the run identifier or a policy copy exists - with both guards, only the revision, only the context': keys(
        TC, 'id_taken_a_copied_policy_exists', 'id_taken_an_existing_dissonance_run', 'id_taken_the_run_exists', 'id_taken_with_only_the_context',
        'id_taken_with_only_the_revision'),
    'F6-11 r12c (a): recipe_refused - not an identifier, unknown template, an unsupported or malformed choice': keys(
        TC, 'recipe_refused_choices_not_an_object', 'recipe_refused_count_is_a_shard_only_choice', 'recipe_refused_dissonant_tab_is_a_dissonance_only_choice',
        'recipe_refused_dissonant_tab_unknown', 'recipe_refused_enable_rescue_on_a_recipe_without_rescue_rules', 'recipe_refused_enable_uw_not_true_or_false',
        'recipe_refused_enable_uw_on_a_recipe_without_uw_rules', 'recipe_refused_gem_cap_is_a_tournament_only_choice', 'recipe_refused_identifier_empty',
        'recipe_refused_identifier_longer_than_64_characters', 'recipe_refused_identifier_not_text', 'recipe_refused_identifier_starting_with_a_digit',
        'recipe_refused_identifier_with_a_capital', 'recipe_refused_identifier_with_a_space', 'recipe_refused_perk_bans_not_a_list',
        'recipe_refused_perk_bans_with_a_non_text_entry', 'recipe_refused_unknown_template', 'recipe_refused_unsupported_choice', 'recipe_refused_with_only_the_context',
        'recipe_refused_with_only_the_revision'),
    'F6-11 r12c (a): validation_failed - the profile validator refused the result (problems kept)': keys(
        TC, 'validation_failed_default_label_clashes_with_an_existing_run', 'validation_failed_dissonance_default_label_clashes',
        'validation_failed_dissonance_default_loadout_the_account_does_not_define', 'validation_failed_gem_cap_negative', 'validation_failed_shard_count_negative',
        'validation_failed_shard_default_loadout_the_account_does_not_define', 'validation_failed_tier_text', 'validation_failed_tier_zero',
        'validation_failed_tournament_default_loadout_the_account_does_not_define', 'validation_failed_unknown_loadout', 'validation_failed_with_only_the_context',
        'validation_failed_with_only_the_revision'),
    'F6-11 r12c (a): profile_unavailable - the starter and a scan draft (409), no such profile, an unreadable, non-UTF-8, empty or non-mapping file, a bad name': keys(
        TC, 'profile_unavailable_a_scan_draft', 'profile_unavailable_empty_file', 'profile_unavailable_file_that_is_a_list', 'profile_unavailable_file_that_is_not_utf8',
        'profile_unavailable_file_that_is_not_yaml', 'profile_unavailable_name_missing', 'profile_unavailable_name_not_text', 'profile_unavailable_name_with_a_path',
        'profile_unavailable_no_such_profile', 'profile_unavailable_no_such_profile_with_only_the_context', 'profile_unavailable_the_starter'),
    'F6-11 r12c (a): write_failed_nothing_saved only when the file is byte-identical; a write that changed the file has no code; the resend, and a clean add': keys(
        TC, 'added_with_both_guards_has_no_code', 'identical_resend_after_the_error_is_revision_conflict', 'identical_resend_with_only_the_context_is_id_taken',
        'write_failed_nothing_saved', 'write_failed_nothing_saved_with_only_the_context', 'written_then_the_answer_failed_has_no_code'),
    'F6-11 r12c (a): the guards are judged first (a stale revision with a bad recipe is revision_conflict)': keys(
        TC, 'stale_revision_with_a_bad_recipe_is_revision_conflict_not_recipe_refused'),
    'F6-11 r12c (a): without a guard the answer is as it always was, with no code': keys(
        TC, 'unguarded_body_not_an_object_has_no_code', 'unguarded_id_taken_has_no_code', 'unguarded_recipe_refused_has_no_code', 'unguarded_unknown_profile_has_no_code',
        'unguarded_validation_failed_has_no_code', 'unguarded_write_failed_has_no_code'),
    'F6-11 r12c (a): the profile read before an add and after the refusals, the policy clash and the failed write': keys(
        TC, 'read_after_an_error_that_followed_the_write_holds_the_run', 'read_after_the_failed_write_the_revision_is_unchanged',
        'read_after_the_policy_clash_nothing_changed', 'read_after_the_profile_refusals_nothing_changed', 'read_after_the_refusals_nothing_changed', 'read_before_any_add'),
    'F6-11 r12c (b): the routine overrides and the shopping plan are set and read before the reset': keys(
        RMX, 'read_before_the_reset_the_mixed_overrides_are_in_force', 'routine_overrides_set_before_the_reset', 'shopping_plan_set_before_the_reset'),
    'F6-11 r12c (b): one request with null for shopping_plan and the routine overrides - accepted, requested, applied, replayed': keys(
        RMX, 'accepted_null_for_shopping_plan_and_routine_overrides', 'identical_resend_of_the_mixed_reset_answered_from_the_record', 'read_after_the_mixed_reset_applied',
        'read_after_the_reset_was_accepted_before_it_applies'),
    'F6-11 r12c (b): only the plan, or only the routines, is reset and the other override stays': keys(
        RMX, 'accepted_null_for_the_routine_overrides_only_keeps_the_plan_override', 'accepted_null_for_the_shopping_plan_only_keeps_the_routine_overrides',
        'read_after_the_plan_only_reset_the_routine_overrides_remain', 'read_after_the_routines_only_reset_the_plan_override_remains'),
    'F6-11 r12c (b): a stale control revision refuses the mixed reset and keeps both kinds of override': keys(
        RMX, 'mixed_reset_with_a_stale_control_revision_refused', 'read_after_the_refusal_both_kinds_of_override_remain'),
    "F6-11 RL9 (R-25): a run's label set, deleted so the run id shows, deleted when there is none, and the refusals (blank, not text, a name that repeats another run's)": keys(
        PAR, 'label_delete_refused_the_identifier_title_cased_is_another_runs_label', 'label_delete_when_the_run_has_no_label', 'label_deleted_the_run_id_shows',
        'label_empty_text_refused', 'label_not_text_refused', 'label_only_spaces_refused', 'label_the_same_as_another_runs_identifier_title_cased_refused',
        'label_the_same_as_another_runs_label_refused', 'read_after_the_label_refusals_nothing_changed', 'read_after_the_label_was_deleted',
        'read_before_the_label_edits', 'saved_label'),
    'F6-11 RL12 (R-26): a coin run switched to dissonance and back (set, retarget, delete, null), unknown tabs refused, the reads around them': keys(
        PAR, 'coin_run_switched_to_dissonance', 'coin_run_switched_to_dissonance_again', 'dissonance_run_retargeted_to_another_tab',
        'dissonance_run_switched_back_to_coin_by_deleting_the_tab', 'dissonance_run_switched_back_to_coin_the_last_time', 'dissonant_tab_a_list_refused',
        'dissonant_tab_a_number_refused', 'dissonant_tab_empty_text_refused', 'dissonant_tab_set_to_null_is_stored_as_null_not_deleted',
        'dissonant_tab_text_not_a_tab_refused', 'dissonant_tab_ultimate_weapons_accepted', 'read_after_the_switch_back_to_coin', 'read_after_the_switch_to_dissonance',
        'read_after_the_tab_was_set_to_null', 'read_before_the_kind_switches'),
    'F6-11 RL12 (R-26): the `kind` field itself - refused by the validator while the run keeps a field of the old kind, and (r12d) by the route\'s own guard for a coin run that keeps none; the run stays editable': keys(
        PAR, 'kind_changed_from_coin_to_tournament_refused_by_the_equipment', 'kind_changed_on_a_coin_run_that_keeps_no_coin_only_field_is_refused',
        'saved_tier_of_the_run_whose_kind_change_was_refused', 'kind_changed_to_dissonance_which_is_not_a_kind_refused', 'read_after_the_kind_attempts'),
    'F6-11 RL13 (R-30): rides of a grant quest and cycles of a cycle quest - saved, rides deleted, cycles required': keys(
        PAR, 'cycles_deleted_refused_a_cycle_quest_needs_its_cycles', 'read_after_the_limit_saves', 'read_before_the_limit_edits',
        'rides_deleted_the_quest_uses_its_own_default', 'saved_cycles', 'saved_cycles_one', 'saved_rides', 'saved_rides_one'),
    "F6-11 RL13 (R-30): rides and cycles refused (zero, negative, fraction, text), the other kind's field, count naming rides/cycles; nothing changed": keys(
        PAR, 'count_on_a_cycle_quest_run_names_cycles_refused', 'count_on_a_grant_quest_run_names_rides_refused', 'cycles_fraction_refused', 'cycles_on_a_coin_run_refused',
        'cycles_on_a_grant_quest_run_refused', 'cycles_text_refused', 'cycles_zero_refused', 'read_after_the_limit_refusals_nothing_changed', 'rides_fraction_refused',
        'rides_negative_refused', 'rides_on_a_coin_run_refused', 'rides_on_a_cycle_quest_run_refused', 'rides_text_refused', 'rides_zero_refused'),
    'F6-12 SQ5: the saved run save refuses a negative, fractional, text or boolean tap cap; nothing changes': keys(
        SB, 'read_before_the_refusals', 'read_after_the_refusals_nothing_changed', 'saved_tap_cap_fraction_refused', 'saved_tap_cap_negative_refused', 'saved_tap_cap_text_refused', 'saved_tap_cap_true_refused'),
    'F6-12 SQ5: the saved run save refuses a weight of 0, 101, a fraction or text': keys(
        SB, 'saved_weight_above_100_refused', 'saved_weight_fraction_refused', 'saved_weight_text_refused', 'saved_weight_zero_refused'),
    'F6-12 SQ5: the saved run save refuses a reversed, empty or non-whole wave range': keys(
        SB, 'saved_from_wave_fraction_refused', 'saved_from_wave_zero_refused', 'saved_until_wave_below_two_refused', 'saved_until_wave_fraction_refused',
        'saved_wave_range_empty_refused', 'saved_wave_range_reversed_refused'),
    'F6-12 SQ5: the smallest and largest accepted values, saved and read': keys(
        SB, 'read_after_the_boundary_values_were_saved', 'saved_boundary_values_accepted'),
    'F6-12 SQ5: the live save refuses the same bounds and records nothing; the boundary values are accepted and applied': keys(
        SB, 'live_boundary_values_accepted', 'live_tap_cap_fraction_refused', 'live_tap_cap_negative_refused', 'live_wave_range_reversed_refused', 'live_weight_zero_refused',
        'read_after_the_live_boundary_values_applied', 'read_after_the_live_refusals_nothing_was_recorded'),
})
NOTES.update(
    run_templates_add_coded=('r12c (F6-11 part 2 / RL4): POST /api/run-templates/add with a guard (`expected_revision` and/or `expected_context`) carries a stable `code` on every refusal the route can PROVE happened before anything was written, so a page may show it as Refused: '
                             'id_taken (400: the run identifier, or a policy that identifier would create, exists), recipe_refused (400: not an identifier, unknown template, an unsupported or malformed choice), validation_failed (400: the profile validator refused the result; `problems` stays), '
                             'profile_unavailable (400: no such profile, unreadable, not UTF-8, not a mapping, a name that is not a profile name; 409: the starter and a scan draft) and write_failed_nothing_saved (400: the write raised AND the profile file is byte-identical to what it was before the attempt). '
                             'revision_conflict (409), context_changed (409) and invalid_request (400) are the guards\' own, unchanged, and are judged first. A failure after which the file differs, or cannot be read back, has NO code (written_then_the_answer_failed_has_no_code): its outcome is unknown and a read of the profile settles it. '
                             'A request WITHOUT a guard is answered exactly as before, without a code (the unguarded_* cases). The same requests are in run_templates_add (unguarded, unchanged) and run_templates_add_fenced, where six answers gained the code.'),
    run_controls_live_reset_all_mixed=('r12c (F6-11 RL2): ONE live-controls request that sends null for `shopping_plan` AND for the routine overrides applied together, from the applied mixed state of shopping_queue.shopping_plan_routine_overrides_applied (routine overrides shopping off / every 60 s at revision 1, the plan PW at revision 2). '
                                       'A null removes the override: the plan returns to the battle\'s starting plan and the routines to their starting values, the three paths in one command; `overrides` keeps the (empty) routines mapping and no `shopping_plan` key. '
                                       'The same request with only the plan keeps the routine overrides, with only the routines keeps the plan override; a stale control revision is a 409 that keeps both; the identical resend is answered from the record.'),
    run_entry_patch_parity=('r12c (F6-11 RL9, RL12, RL13; legacy rows R-25, R-26, R-30): POST /api/profile-patch on [blueprints, <run>, <field>], the way the legacy page wrote them. label: a delete (op:"delete") makes the run identifier show again and is accepted even when there is no label; an empty, blank or non-text label is refused; '
                            'a label or an identifier that title-cased is another run\'s name is refused, and so is deleting a label when the identifier would then repeat another run\'s label. kind: a dissonance run IS a coin run with `dissonant_tab` - set, retargeted, deleted (op:"delete") to go back; '
                            'a bare value null is STORED as null (the key stays; the page deletes instead); an unknown tab is refused. `kind` itself is refused: by the validator while the run keeps a field of the old kind (coin-only fields, the as_is equipment) and, since r12d, by the route\'s own guard (400 kind_change_refused) for a coin run that keeps none - it used to be saved (family run_kind_guard has the full set). '
                            'limits: `rides` of an Ultimate Weapon grant quest and `cycles` of a cycle quest are integers >= 1; rides may be deleted, cycles may not (required); the other kind\'s field and `count` are refused (count names rides / cycles).'),
    shopping_queue_bounds=('r12c (F6-12 SQ5): what the SERVER answers to a shopping queue whose per-entry numbers are out of bounds, through the saved run save (POST /api/run-controls) and the live save (POST /api/operations/{id}/live-controls); both validate the plan with the same rules and refuse with 400 {ok:false, error}, '
                           'recording nothing (a read after them shows the saved run unchanged and no command). tap_limit: an integer >= 0 (negative, fraction, text and booleans are refused); weight: an integer from 1 to 100 ("expected integer >= 1" for 0, a fraction or text; "from 1 to 100" above 100); '
                           'from_wave an integer >= 1, until_wave an integer >= 2 and greater than from_wave (a reversed or empty range is "<id>: empty wave range"). The smallest and largest accepted values are saved and applied in boundary_values_accepted.'),
)
COVERAGE.update({
    'F6-11 6.4 r12d: an Ad gems offer that appeared after an absence and waits for its window and response delay (through the collector own poll): response_due_at, scheduling and response timing set, no failed attempt yet': keys(
        AP, 'offer_seen_after_an_absence_and_its_response_scheduled'),
    'F6-11 6.4 r12d: an Ad gems attempt that was sent and not confirmed (offer_still_visible) with every timing field set (scheduling_wait_sec, response_delay_sec, response_wait_sec, response_overdue_sec, verification_sec)': keys(
        AP, 'failed_attempt_unconfirmed_the_offer_was_still_visible_after_the_tap'),
    'F6-11 6.4 r12d: an Ad gems attempt skipped before the tap - the offer moved, another app in front of the game, the offer gone on the fresh frame - with the skip fields of last_failed_attempt': keys(
        AP, 'failed_attempt_skipped_the_offer_moved_before_the_tap', 'failed_attempt_skipped_another_app_was_in_front_of_the_game',
        'failed_attempt_skipped_the_offer_was_gone_on_the_fresh_frame'),
    'F6-11 6.5 r12d: removing the plan section removes the day-plan run from GET /api/runs (runs and readiness), adding it back lists it again, the blueprint runs never change; each write and its profile read': keys(
        PRE, 'read_runs_before_the_removal_the_day_plan_run_is_listed', 'read_profile_before_the_removal', 'plan_section_removed', 'read_runs_after_the_removal_the_day_plan_run_is_gone',
        'read_profile_after_the_removal', 'plan_section_added_back', 'read_runs_after_the_plan_was_added_back_the_day_plan_run_is_listed'),
    'F6-11 6.5 r12d: deleting plan.week or plan.days is 400 (a plan with no blocks schedules nothing), also with the section gone; nothing is written and the run list does not change': keys(
        PRE, 'plan_week_deleted_is_refused_a_plan_with_no_blocks', 'plan_days_deleted_is_refused_a_plan_with_no_blocks', 'read_runs_after_the_refusals_nothing_changed',
        'plan_week_deleted_when_the_plan_is_gone_is_refused_the_same_way'),
})
COVERAGE.update({
    'F6-14 r12e Schedule: the whole day plan of the INACTIVE profile removed (200, no `value`, fenced) with the read that follows, the delete of an already absent section (200, same revision), one half of the plan refused (400 `schedules nothing`), and the active profile still holding its plan': keys(
        PTM, 'read_before_the_inactive_plan_is_removed', 'inactive_plan_week_deleted_is_refused_a_plan_with_no_blocks', 'inactive_plan_deleted', 'read_after_the_inactive_plan_was_removed',
        'inactive_plan_delete_when_the_section_is_already_gone', 'read_the_active_profile_after_the_inactive_plan_was_removed'),
    'F6-14 r12e Schedule: the delete of the inactive plan after another client saved (409 revision_conflict, plan remains) and after the account was switched (409 context_changed)': keys(
        PTM, 'inactive_plan_delete_stale_revision_409', 'read_after_the_stale_delete_the_plan_remains', 'inactive_plan_delete_refused_after_account_switch'),
    'F6-14 r12e Abilities: an unused Ultimate Weapon policy and an unused rescue policy of the inactive profile deleted (200, no `value`), each state read back': keys(
        PTM, 'read_before_the_inactive_policies_are_deleted', 'inactive_unused_uw_policy_deleted', 'inactive_unused_rescue_policy_deleted', 'read_after_the_unused_policies_were_deleted'),
    'F6-14 r12e Abilities: the same deletes refused while a run of the inactive profile uses the policy (400 `unknown policy`), nothing written': keys(
        PTM, 'inactive_uw_policy_deleted_while_a_run_uses_it_is_refused', 'inactive_rescue_policy_deleted_while_a_run_uses_it_is_refused', 'read_after_the_in_use_deletes_were_refused_nothing_changed'),
    'F6-14 r12e Abilities: a rescue policy of the inactive profile saved with its rules in another order (the whole policy, as the page sends it) and the rules put in another order by the rules subpath, each read back': keys(
        PTM, 'inactive_rescue_policy_saved_with_its_rules_in_another_order', 'read_after_the_inactive_rescue_rules_were_reordered_as_a_whole_policy',
        'inactive_rescue_rules_reordered_by_the_rules_subpath', 'read_after_the_inactive_rescue_rules_were_reordered_by_the_subpath'),
    'F6-14 r12e Runs entry: every field of a run the Runs page can save or delete, for the inactive profile (the lists of run_entry_patch replayed with expected_context), before and after': keys(
        PTM, 'read_before_the_first_inactive_run_entry_save', *[f'inactive_{case}' for _, case, _, _ in ENTRY_SAVES], *[f'inactive_{case}' for _, case, _ in ENTRY_DELETES],
        'read_after_the_inactive_run_entry_saves_holds_every_change'),
    'F6-14 r12e Runs entry: one refusal of each kind of the run-entry validator for the inactive profile (400 with problems, no code), nothing written': keys(
        PTM, *[f'inactive_{case}_is_refused' for case in REFUSED_RUN_FIELDS], *[f'inactive_{case}_is_refused' for _, case, _ in ENTRY_DELETE_REFUSALS],
        'read_after_the_inactive_run_entry_refusals_nothing_changed'),
    'F6-14 r12e Runs entry: a run field of the inactive profile after another client saved (409, judged before the values) and after the account was switched (409 context_changed)': keys(
        PTM, 'inactive_run_entry_stale_revision_409', 'inactive_run_entry_stale_revision_is_judged_before_the_values', 'read_after_the_stale_run_entry_refusals_holds_the_other_clients_save',
        'inactive_run_entry_refused_after_account_switch'),
    'F6-14 r12e read failure: GET /api/profile-files that fails (500, no JSON) and answers again afterwards': keys(
        PTM, 'profile_files_read_failed_500', 'profile_files_read_after_the_failure_answers_again'),
    'F6-14 r12e: an inactive target whose source cannot be read - the list still names it patchable, the source read and a patch of it (with its true revision) are 400 with the parser text, or "is not a mapping"': keys(
        PTM, 'inactive_target_source_that_cannot_be_parsed_400', 'profile_files_still_lists_the_target_whose_source_cannot_be_parsed', 'inactive_patch_of_a_target_that_cannot_be_parsed_is_the_parser_error_400',
        'inactive_target_source_that_is_not_a_mapping_400', 'inactive_patch_of_a_target_that_is_not_a_mapping_400'),
})
NOTES.update(
    run_kind_guard=('r12d (F6-11 RL12, the r12c review): a run\'s kind picks its runner and decides which fields it may carry, so POST /api/profile-patch refuses a patch that changes the kind of a run that exists before and after it. '
                    'A change by the `kind` field, by replacing the whole run, or by replacing the whole `blueprints` mapping is 400 {ok:false, code:"kind_change_refused", error, changes:[{blueprint, from_kind, to_kind}]} (sorted by run name; a deleted kind would be to_kind null), nothing written. '
                    'It is judged after the revision and context checks (409 revision_conflict / context_changed come first) and after the validator: a change the validator already refuses (an unknown or deleted kind, a field the new kind may not carry, the as_is equipment on a non-coin run, a shard-only count) '
                    'keeps the validator\'s own answer, uncoded, with `problems`. Not kind changes, and accepted: setting the kind a run already has, replacing a whole run by one of the same kind, adding a run (any kind), removing one - '
                    'and removing a run and adding it again as another kind, which are two requests (a plan block that names the run blocks the first). Before r12d the same patch of a coin run that kept no coin-only field to a tournament run was saved (run_entry_patch_parity recorded it).'),
    profile_patch_fenced=('r12d (F6-14b, root decision D4): every write of the Schedule, Collection, Abilities and Runs pages to the ACTIVE profile sends expected_context (the runtime context, as GET /api/config publishes it) and expected_revision (the source revision of `mine` it read). '
                          'The route already honoured both; what was missing were recordings of those bodies for the active profile. Each success is followed by `read_after_<case>` (GET /api/profile-src/mine): the revision and the profile equal the write\'s answer, no problem. '
                          'The refusal after the account switch is 409 {code:"context_changed", current_context: beta\'s} with nothing written (the read after it equals the read before). The inactive profile `second`, `theirs` and config.yaml are untouched by every case (asserted by the generator). '
                          'The brief\'s table names 14 cases; 13 are successes and one the refusal; the 14 reads are extra.'),
)
NOTES.update(
    ad_gems_failed_attempt_poll=('r12d (gap of F6-11 6.4): GET /api/battles/active runtime.routines.ad_gems when the failed attempt was made by the collector own poll(frame) on the world clock (only the device boundary is faked: frames, the recognizer rectangle, the window list, capture and tap). '
                                 'battle_details_runtime finished its attempt by calling _finish_attempt directly, so its scheduling_wait_sec / response_delay_sec are null and response_wait_sec / response_overdue_sec absent; here all four are set (6.0 / 15.0 / 15.0 / 0), '
                                 'appearance_after_at is set because the HUD showed no offer on the first checks, and response_due_at / response_started_at are set while the response waits (the first case) and null once the attempt started (the offer is forgotten when an attempt begins). '
                                 'Recorded failures: not_confirmed / offer_still_visible after one tap (verification_before_tap_sec, verification_sec), and three skips with no tap: offer_moved (center_displacement_px, observed_rect, fresh_rect), in_front:<package> (visible_windows) and fresh_offer_missing (visibility absent). '
                                 'A refused tap or a capture error also fails an attempt but carries a Python traceback with this machine paths, so it is not recorded.'),
    plan_removal_effects=('r12d (gap of F6-11 6.5): what POST /api/profile-patch {path:["plan"], op:"delete"} changes beyond the profile: GET /api/runs lists the config combo run (the day-plan run) only while the profile has a plan section - the run leaves `runs` and `readiness` when the section is removed and returns when it is added back; '
                          'the blueprint runs are identical before, after and after the add-back. Deleting plan.week or plan.days (either half) leaves a plan with no blocks and is 400 {ok:false, error, problems} with the validator text (uncoded), also when the section is already gone (the route deletes from an empty plan); nothing is written. '
                          'GET /api/schedule/status was probed and not recorded: it reports the running scheduler last observation and stayed available after the removal in the synthetic world.'),
)
NOTES.update(
    profile_target_more=('r12e (the open recording requests of the F6-14 reports): what profile_target did not record about editing an INACTIVE profile (`second`, same account, `mine` in force). Every write carries {name:\'second\', path, value | op:\'delete\', expected_context, expected_revision}; a delete has no `value`. '
                         'The active profile and config.yaml are byte-identical after every group (asserted). Schedule: removing the plan section is 200 and the read shows no `plan`; deleting what is absent is 200 with the same revision (no rewrite); deleting plan.week alone is 400 "a plan with no blocks schedules nothing" (uncoded, `problems`); a stale revision is 409 revision_conflict, a changed account 409 context_changed. '
                         'Abilities: an unused UW or rescue policy deletes (200); while a run of the profile uses it the delete is 400 "unknown policy \'<id>\' (defined in ...: <the others>)", nothing written; the rules of a rescue policy are reordered by saving the whole policy (what the page sends) or by the rules subpath, both 200. '
                         'Runs entry: ENTRY_SAVES / ENTRY_DELETES of run_entry_patch replayed for the inactive profile (the answers equal the active profile\'s except for the profile they describe) and one refusal of each kind. '
                         'Read failure: GET /api/profile-files raises when config.yaml cannot be read (500, the default page, no JSON). A target whose file cannot be parsed is still LISTED, `patchable: true` (the list looks at names, not content), GET /api/profile-src/<name> is 400 {ok:false, error: the parser text}, '
                         'and a patch is that same 400 even with the file\'s true revision (the file is left as it is); a file that is not a mapping (a list, a scalar, empty) is 400 "<name>.yaml is not a mapping" for the read and the patch.'),
)
COVERAGE.update({
    'F6-15 C-03 Emulators (1)(3): the read just before the published saved_instance_defaults, and the machine fallback (empty preset) with autostart off saved from the state that save leaves, with the read after': keys(
        SMM, 'read_before_saved_instance_defaults', 'saved_instance_defaults_with_fallback_and_autostart_off', 'read_after_saved_instance_defaults_with_fallback_and_autostart_off'),
    'F6-15 C-03 Emulators (2): a hand-edited emulator preset the options do not offer - the read, the form that sends it back (refused), a save without the field (kept), the fallback (replaces it)': keys(
        SMM, 'read_with_an_unlisted_saved_instance_defaults_preset', 'instance_defaults_form_sent_with_the_unlisted_preset_is_refused', 'read_after_the_refused_unlisted_instance_preset_nothing_changed',
        'instance_defaults_saved_without_the_preset_field_keeps_the_unlisted_preset', 'read_after_the_partial_instance_save_the_unlisted_preset_remains',
        'instance_defaults_choosing_the_machine_fallback_replaces_the_unlisted_preset', 'read_after_the_unlisted_instance_preset_was_replaced'),
    'F6-15 C-03 Runtime (4): run-defaults, loop-defaults, missions-defaults and fleet-defaults saved while the selected emulator carries its own value that differs from the machine default - read before, save, read after (the override unchanged)': keys(
        SMM, 'read_before_saved_run_defaults_with_overrides', 'saved_run_defaults_with_overrides', 'read_after_saved_run_defaults_with_overrides', 'read_before_saved_loop_defaults_with_overrides', 'saved_loop_defaults_with_overrides', 'read_after_saved_loop_defaults_with_overrides', 'read_before_saved_missions_defaults_with_overrides', 'saved_missions_defaults_with_overrides', 'read_after_saved_missions_defaults_with_overrides', 'read_before_saved_fleet_defaults_with_overrides', 'saved_fleet_defaults_with_overrides', 'read_after_saved_fleet_defaults_with_overrides'),
    'F6-15 C-03 Runtime (5): run-defaults with a stored preset the options do not offer (read, refused, replaced by a listed one), and with no runnable preset - the empty options list, every value refused': keys(
        SMM, 'read_with_an_unlisted_saved_run_defaults_preset', 'run_defaults_saving_back_the_unlisted_preset_is_refused', 'read_after_the_refused_unlisted_run_preset_nothing_changed',
        'run_defaults_choosing_a_listed_preset_replaces_the_unlisted_one', 'read_after_the_unlisted_run_preset_was_replaced', 'read_with_no_runnable_run_defaults_preset_options',
        'run_defaults_with_no_runnable_preset_refuses_the_value_it_shows', 'run_defaults_with_no_runnable_preset_refuses_the_empty_value', 'instance_defaults_with_no_runnable_preset_refuses_a_named_preset',
        'read_after_the_empty_options_refusals_nothing_changed', 'instance_defaults_with_no_runnable_preset_still_saves_the_empty_value', 'read_after_the_empty_value_was_saved_with_no_runnable_preset',
        'read_with_no_presets_in_the_configuration_the_options_are_empty', 'run_defaults_with_no_presets_in_the_configuration_is_refused'),
    'F6-15 r12e: a placeholder preset (`defined: false`) and the empty value on run-defaults refused (400 `expected enum`), nothing written': keys(
        SMM, 'read_with_a_placeholder_preset_in_the_configuration', 'run_defaults_with_a_placeholder_preset_is_refused', 'run_defaults_with_the_empty_value_is_refused',
        'instance_defaults_with_a_placeholder_preset_is_refused', 'instance_defaults_with_a_placeholder_and_a_valid_label_writes_neither', 'read_after_the_refused_preset_saves_nothing_changed'),
    'F6-15 C-03 Equipment (6): tournament-cards saved with every string list empty from the state the published save leaves, the reads, and the same save again (200, same revision)': keys(
        SMM, 'read_before_saved_tournament_cards_empty_lists', 'saved_tournament_cards_empty_lists', 'read_after_saved_tournament_cards_empty_lists',
        'config_read_after_saved_tournament_cards_empty_lists', 'saved_the_same_empty_lists_again_keeps_the_revision'),
    'F6-15 r12e: tournament-cards with only one list emptied (the other is kept), and with no account bound (the root block) both emptied': keys(
        SMM, 'read_before_only_the_drop_list_is_emptied', 'saved_tournament_cards_with_only_the_drop_list_empty_the_add_list_is_kept', 'read_after_only_the_drop_list_was_emptied',
        'read_before_only_the_add_list_is_emptied', 'saved_tournament_cards_with_only_the_add_list_empty_the_drop_list_is_kept', 'read_after_only_the_add_list_was_emptied',
        'read_before_the_unbound_tournament_lists_are_emptied', 'saved_tournament_cards_empty_lists_and_no_account', 'read_after_the_unbound_tournament_lists_were_emptied'),
    'F6-15 C-03 Equipment (7)(8): GET /api/config just before the published saved_modules_restore, and just before the published clearing refusal of loadout_restore_eligibility': keys(
        SMM, 'read_before_saved_modules_restore', 'read_before_the_clearing_refusal'),
})
NOTES.update(
    settings_modules_more=('r12e (the open recording requests of F6-15, compliance C-03): what settings_modules and loadout_restore did not record. Every request is POST /api/settings {context, revision, module, values} (the reads are GET /api/settings, or GET /api/config where the name says so) over the worlds of settings_modules (alpha on main, beta on other; presets normal_run, coin_farm and the placeholder `later`). '
                           'The published families are unchanged: a read the page needs "just before" a published save (read_before_saved_instance_defaults, read_before_saved_modules_restore, read_before_the_clearing_refusal, and the state the published saved_tournament_cards leaves) is recorded here in a world that reaches the same bytes, and test_the_reads_of_settings_modules_more_are_the_states_the_published_saves_start_from compares its revision with the published request. '
                           'saved_instance_defaults_with_fallback_and_autostart_off is {preset:"", autostart:false} sent from the state the published save leaves: the catalogue has no boolean `fallback`; "fallback" is the explicit option "Use machine fallback" (the empty preset), stored as the empty string, and autostart is stored as an explicit false. '
                           'Machine defaults over an override: run-defaults, loop-defaults, missions-defaults and fleet-defaults write the ROOT value only; the selected emulator\'s own values (its default preset, instances.main.loop|missions|fleet) and the other emulator are byte-identical afterwards, and the emulator module (`loop`, `missions`, `fleet`) still shows its override - '
                           'a key the override does not carry (loop.dry_run, fleet.post_window) follows the new default. Preset choices: an enum offers exactly the catalogue options - run-defaults the runnable presets (a `defined: false` placeholder is not one), instance-defaults those plus the empty value "Use machine fallback" - '
                           'and any other value is 400 {ok:false, error:"<label>: expected enum"} (run-defaults: "Fallback preset: expected enum", instance-defaults: "Default preset: expected enum"; uncoded), whatever its type, with nothing written, also the valid fields of the same request. '
                           'Only the fields a request names are validated: a stored preset that is no longer offered (a hand-edited name) stays, and is shown with an options list that lacks it, until a request names it, so a form that sends every field back is refused while a save without the preset field is 200. '
                           'With no runnable preset (all placeholders, or no `presets` in the configuration) run-defaults offers an empty list and refuses every value, the one it shows included; instance-defaults offers only the empty value and saves it. '
                           'Tournament lists: tournament-cards stores the lists as sent, [] included (saved_tournament_cards_empty_lists sets every string list of the module to []); with an account bound to the selected emulator they are the account\'s (the root block, the legacy default, and the other account are untouched), with none they are the root block; '
                           'a save that changes nothing is 200 with the same revision. The two GET /api/config reads are the configuration the loadout restore requests start from; the request that follows each is replayed unrecorded by the generator to prove the read admits it.'),
)
COVERAGE.update({
    'F6-15 r12f (1): POST /api/loadout-patch with a modules_restore and no `modules` for the loadout "plain" (request exactly {name, expected_revision, expected_context {account null, instance, profile, serial}, body}), saved from the state settings_modules_more.read_before_saved_modules_restore records, with the GET /api/config read after': keys(
        SM2, 'saved_modules_restore_only', 'read_after_saved_modules_restore_only'),
    'F6-15 r12f (2): GET /api/profile-src/mine for a profile with no `blueprints` mapping (absent, null, a list, an empty mapping) and the restore edit the server refuses for it (409 restore_loadout_unused); a non-restore loadout save is refused with the profile problems (400), nothing written': keys(
        SM2, 'profile_src_without_blueprints', 'restore_refused_for_a_profile_without_blueprints', 'loadout_save_refused_for_a_profile_without_blueprints', 'read_config_after_the_refusals_nothing_changed',
        'profile_src_with_blueprints_null', 'restore_refused_for_a_profile_with_blueprints_null', 'profile_src_with_blueprints_a_list', 'restore_refused_for_a_profile_with_blueprints_a_list',
        'profile_src_with_an_empty_blueprints_mapping', 'restore_refused_for_a_profile_with_an_empty_blueprints_mapping'),
    'F6-15 r12f (3): GET /api/settings where a tournament-cards list holds [false, 7, "keep"] (hand-edited YAML): the read, the save that sends it back (400, entry 1 must be a non-blank string), a save of the other list (200, the malformed list stays), the save that repairs it (200) and the reads': keys(
        SM2, 'read_with_malformed_tournament_cards', 'tournament_cards_sent_back_with_the_malformed_entries_is_refused', 'read_after_the_refused_send_back_nothing_changed',
        'saved_tournament_cards_add_list_only_the_malformed_drop_list_stays', 'read_after_the_add_list_was_saved_the_malformed_drop_list_remains', 'saved_tournament_cards_repairing_the_malformed_drop_list',
        'read_after_the_malformed_drop_list_was_repaired', 'saved_the_repaired_lists_again_keeps_the_revision'),
})
NOTES.update(
    settings_modules_more_2=('r12f (the follow-up of F6-15): three recordings the settings and equipment pages still needed. (1) saved_modules_restore_only is POST /api/loadout-patch {name:"plain", expected_revision, expected_context {account:null, instance:"main", profile:"mine", serial:"fake:1"}, body {cards:"main_farm", cards_restore:"no_card", modules_restore:[["black_hole_digestor","assist"]]}} from the state '
                             'settings_modules_more.read_before_saved_modules_restore records (the cards restore is saved; the guard test compares the revision of that read with the request). The body has NO `modules`: the route judges and stores the whole body it is given (nothing is merged with the stored loadout), so the answer holds exactly those three keys, plus a warning that the loadout hand-equips modules without a modules preset; the other loadouts are untouched. '
                             '(2) A profile with no `blueprints` mapping (the key absent, null, a list or {}): GET /api/profile-src/mine is 200 {ok:true, name, profile, problems, warnings, revision, validator_error}, `profile` as the file has it and problems naming the missing, wrong or empty section (and the plan blueprints that no longer exist). playerprofile.loadouts_in_use finds no loadout, so a body that sets or changes a restore key is refused before the validator runs: 409 {ok:false, code:"restore_loadout_unused", loadout, restore_keys, active_profile, error}; '
                             'any other loadout save is judged by the profile validator and refused 400 {ok:false, error: the first problem, problems} (uncoded). Nothing is written (the configuration read afterwards has the same revision). (3) A tournament list holding [false, 7, "keep"] from hand-edited YAML is shown by GET /api/settings as stored (raw); saving it back is 400 "Cards to drop: entry 1 must be a non-blank string" (uncoded, nothing written); '
                             'a save that names only the other list is 200 and the malformed list stays as stored (only the fields a request names are validated); a save that names the list with valid entries repairs it. The same lists saved again are 200 with the same revision.'),
)
COVERAGE.update({
    f"F6-14 r12f ({number}): {'delete' if delete else 'save ' + repr(value)} {'.'.join(path)} of the INACTIVE profile `second` (expected_revision from the profile-src read before it, expected_context the page's), with the profile-src read before and after": keys(PM2, *pm2_names(stem, delete))
    for number, (stem, path, value, delete) in enumerate(PM2_STEPS, 1)})
COVERAGE.update({
    'F6-14 r12f: the same delete of the rescue policy again, once the run has none (200, the same revision: the file is not rewritten)': keys(
        PM2, 'read_before_the_farm_rescue_policy_is_deleted_again', 'inactive_farm_rescue_policy_deleted_again_when_the_run_has_none'),
})
NOTES.update(
    profile_target_more_2=('r12f (the follow-up of F6-14): seven more run-entry edits of the INACTIVE profile `second` (the world of profile_target: alpha on main, `mine` in force), each POST /api/profile-patch {name:"second", path, value | op:"delete", expected_revision, expected_context {instance:"main", account:"alpha", profile:"mine", serial:"fake:1"}} with the revision of the GET /api/profile-src/second recorded just before it and one more GET just after. '
                           'In the order requested, in one world, each from the state the one before left: delete blueprints.farm.label; delete blueprints.farm.policies.gather; delete blueprints.farm.policies.rescue; delete blueprints.dissonance.dissonant_tab; save 2 at blueprints.quest_sm.rides; save 2 at blueprints.quest_ilm.cycles; delete blueprints.quest_sm.rides. '
                           'The world adds to the profile every copy starts from the dissonance coin run, the grant quest quest_sm (rides 1), the cycle quest quest_ilm (cycles 40) and a rescue policy on the coin run farm (the populated profile gives it none), so each edit changes something. All seven are 200 {ok, backup, profile, warnings, revision}: the answer\'s profile and revision are the next read\'s, the validator has no problem before or after, '
                           'and the profile read afterwards is the one before with exactly that path changed. Deleting the gathering policy leaves `policies` as a mapping without it; deleting dissonant_tab leaves a plain coin run (its kind is not touched, so the kind guard does not apply). No refusal occurs for these seven in this world; the refusals of the same fields are in run_entry_patch and run_entry_patch_parity. '
                           'A last request repeats the delete of the rescue policy once it is gone: 200 with the SAME revision (an absent key is popped, the file keeps its bytes). The active profile `mine` and config.yaml are byte-identical after the whole sequence (asserted).'),
)
NOTES.update(action_executions=AX_NOTE.strip(), battle_actions_projection=AP_NOTE, action_executions_more=AXM_NOTE)
COVERAGE.update({row: keys(AX, *cases) for row, cases in AX_ROWS.items()})
COVERAGE.update({row: keys(AP_FAMILY, *cases) for row, cases in AP_ROWS.items()})
COVERAGE.update({row: keys(AXM, *cases) for row, cases in AXM_ROWS.items()})
FAMILY_ROWS = {AXM: AXM_ROW, AP_FAMILY: AP_ROW, AX: AX_ROW, PM2: 'F6-14 follow-up recordings (r12f: seven more run-entry edits of an inactive profile)', SM2: 'F6-15 follow-up recordings (r12f: a modules restore alone, a profile with no blueprints, a malformed tournament list)', SMM: 'F6-15 open recordings (r12e: more of the settings modules)', PTM: 'F6-14 open recordings (r12e: more of an inactive profile)', AP: 'F6-11 6.4 (r12d: the failed Ad gems attempt through the collector own poll)', PRE: 'F6-11 6.5 (r12d: what removing the plan changes)', KG: 'F6-11 RL12 (r12d: a run kind is fixed)', PFD: 'F6-14b (brief section 7: fenced writes to the active profile)', TC: 'F6-11 part 2 / RL4 (stable pre-write codes of run-templates/add)', RMX: 'F6-11 RL2 (mixed shopping_plan + routine reset-all)', PAR: 'F6-11 RL9, RL12, RL13 (label delete, coin<->dissonance, rides/cycles)', SB: 'F6-12 SQ5 (server bounds of the queue)', SM: 'F6-15 (brief section 6)', LR: 'F6-15 (brief section 6, restore values)', LE: 'F6-15 r12b2 (review R1: who may edit a restore value)', SR: 'F6-15 r12b2 (review R2: a selection writes only the selection)', PRS: 'F6-13 (brief section 7)', PRB: 'F6-13 (brief section 7)', PRM: 'F6-13 (brief section 7)', PRP: 'F6-13 (brief section 7, frame.png headers)', ENV: 'P10B (item 10 p10b: the runtime environment report and the OCR probe; root request)', REL: 'P10A (item 10 p10a: /api/runtime.release, the identity of the tree the server runs from; root request)', CF: 'F6PE-P02', EBI: 'M-02 (BlueStacks, page-author request)', CFP: 'F6C-R1-04', RF: 'R12-01..05 (frontend-f6-recognition fixes_round_1.open_items[0])',
               RESET: 'F6-11 RL2 (6.1)', TF: 'F6-11 RL4 guarded (root decision: run-templates/add gains expected_revision and expected_context)', SQ: 'F6-12 (brief section 8)', PTG: 'F6-14 (brief section 6)', ENTRY: 'F6-11 RL3 (6.2)', TEMPLATES: 'F6-11 RL4 (6.3)', DETAILS: 'F6-11 RL7 (6.4)', PLAN_DELETE: 'F6-11 RL8 (6.5)', SAVE: 'RUN-04..07', LIVE: 'RUN-08..11', HL: 'H-01', HD: 'H-01, H-02', CFG: 'G-01', PR: 'G-02', PP: 'G-03', SP: 'S-01', ST: 'G-04', LO: 'E-01', CO: 'C-01', AB: 'A-01', AC: 'P-01', PF: 'P-02', EI: 'M-01', EA: 'M-02', ET: 'M-03', RS: 'T-01', RD: 'T-02', RR: 'R-01, R-02, R-06', CW: 'R-02', RI: 'R-03', SA: 'R-04', RL: 'R-05'}


def fixture_document():
    assert FIXTURE.exists(), f'run with {REGENERATE}=1 to publish it'
    return json.loads(FIXTURE.read_text(encoding='utf-8'))


def test_every_row_is_covered_or_explained():
    document = fixture_document()
    present = case_ids(document)
    required = {case for cases in COVERAGE.values() for case in cases}
    assert not required - present, f'rows name cases that are not in the fixture: {sorted(required - present)}'
    assert not set(NOT_PRODUCIBLE) & set(COVERAGE), 'a row is both covered and declared not producible'
    for row, cases in COVERAGE.items():
        assert cases, f'row {row!r} names no case'
        assert len(cases) == len(set(cases)), f'row {row!r} names a case twice'
    # every family of the fixture belongs to a row group (a family nobody asked for is a documented decision, not drift)
    assert set(document['cases']) == set(FAMILY_ROWS), set(document['cases']) ^ set(FAMILY_ROWS)
    # the cases no row names are the scenario's own settling reads and sibling refusals (listed in the report)
    unnamed = sorted(present - required)
    assert len(unnamed) <= UNNAMED_ALLOWED, f'{len(unnamed)} cases are named by no row: {unnamed[:12]}'


def test_the_rows_that_live_in_the_dashboard_contract_are_there():
    v1 = json.loads(dashboard.FIXTURE.read_text(encoding='utf-8'))
    present = case_ids(v1)
    missing = {case for cases in V1_REFERENCES.values() for case in cases} - present
    assert not missing, f'dashboard_contract_v1.json lacks: {sorted(missing)}'


# The families published so far, in generation order. Ids are aliased in order of appearance, so inserting or
# reordering a family changes the ids (and the hash) of every family after it: new families are appended here.
PUBLISHED_ORDER = ['run_controls_save_cases', 'run_controls_live_cases', 'run_history_cases', 'run_profile_cases', 'run_schedule_cases', 'run_settings_cases',
                   'run_equipment_cases', 'run_collection_cases', 'run_abilities_cases',
                   'run_accounts_cases', 'run_profile_files_cases', 'run_emulator_cases', 'run_runtime_cases',
                   'run_recognition_cases', 'run_calibrate_cases', 'run_screens_cases', 'run_later_cases', 'context_fence_cases',
                   'emulator_bluestacks_inventory_cases', 'collection_first_policy_cases', 'recognition_context_fence_cases',
                   'run_controls_live_reset_all_cases', 'run_entry_patch_cases', 'run_templates_add_cases', 'run_battle_details_cases',
                   'run_plan_section_delete_cases', 'run_templates_add_fenced_cases', 'shopping_queue_cases', 'profile_target_cases',
                   'runtime_environment_cases', 'run_preview_cases', 'settings_modules_cases', 'loadout_restore_eligibility_cases', 'settings_switch_records_cases',
                   'run_templates_add_coded_cases', 'run_controls_live_reset_all_mixed_cases', 'run_entry_patch_parity_cases', 'shopping_queue_bounds_cases',
                   'run_kind_guard_cases', 'profile_patch_fenced_cases', 'ad_gems_failed_attempt_poll_cases', 'plan_removal_effects_cases', 'profile_target_more_cases',
                   'settings_modules_more_cases', 'settings_modules_more_2_cases', 'profile_target_more_2_cases', 'action_executions_cases', 'battle_actions_projection_cases',
                   'action_executions_more_cases',
                   'runtime_release_cases']


CODED_PATHS = frozenset({'/api/calibrate/start', '/api/calibrate/observe', '/api/calibrate/apply', '/api/calibrate/stop', '/api/calibrate/status',
                          '/api/screen-analysis/start', '/api/screen-analysis/status', '/api/screen-analysis/frame.png'})


def test_every_refusal_of_the_recognition_routes_carries_a_code():
    """A client branches on `code`: a 4xx/5xx JSON answer of these routes without one is a defect in the route, not a case to record.
    (A body that is {text} is the default error page an unhandled exception produces; none of the cases of these routes is one for a
    refusal.)"""
    missing = [f'{family}.{name}' for family, cases in fixture_document()['cases'].items() for name, case in cases.items()
               if case['request']['path'].split('?')[0] in CODED_PATHS and case['status'] >= 400 and 'text' not in case['body'] and 'code' not in case['body']]
    assert not missing, missing


def test_the_diagnostics_writer_is_pinned_to_one_pid_while_the_family_is_generated(dash, tmp_path, monkeypatch):
    """runtime_diagnostics publishes byte offsets into log files whose records carry the writer's pid. A pid with another number of digits moved
    every offset, so the committed family only matched when the process that regenerated it had a pid as long as the one that made it. The
    writer's pid is therefore pinned while the records are written (a record under the real pid would differ in length from one under this)."""
    from dashboard_contract_cases_runtime import WRITER_PID, emit, pin_writer
    gen = PagesGenerator(dash, tmp_path / 'pid', monkeypatch)
    with gen.world('pid') as w:
        pin_writer(w)
        emit(w, 'main', 'probe')
        written = [json.loads(line) for line in (w.root / 'logs' / 'main' / 'events_20270115_080000_4242_pppppppp.jsonl').read_text(encoding='utf-8').splitlines()]
    assert [record['pid'] for record in written] == [WRITER_PID]


def test_the_environment_the_samples_report_does_not_depend_on_the_machine(dash, tmp_path, monkeypatch):
    """GET /api/runtime reports the local environment (item 10 p10b): the operating system, the interpreter, the emulator locators that read
    Program Files, the scrcpy files. The runtime_state and runtime_environment samples must be the same on every machine, so every host input
    is pinned in the test world. Generate them here, then again as if this were another machine (another operating system, release,
    architecture and Python, a portable interpreter, no PyAV, MuMu and BlueStacks installed) and compare: a sample that records the machine differs."""
    import importlib.util
    import platform
    import sys
    chosen = [run_runtime_cases, runtime_environment_cases, runtime_release_cases]
    here = render(build(dash, tmp_path / 'here', monkeypatch, chosen))
    programs = tmp_path / 'Program Files'
    for leaf in ('Netease/MuMuPlayer/nx_main/MuMuManager.exe', 'BlueStacks_nxt/HD-Player.exe'):
        (programs / leaf).parent.mkdir(parents=True, exist_ok=True)
        (programs / leaf).write_bytes(b'MZ')
    portable = tmp_path / 'portable' / 'python.exe'
    portable.parent.mkdir()
    portable.write_bytes(b'MZ')
    portable.with_name('python312._pth').write_text('Lib/site-packages')
    with monkeypatch.context() as elsewhere:
        for name, value in (('system', 'Linux'), ('release', '6.8.0'), ('machine', 'aarch64'), ('python_version', '3.13.7')):
            elsewhere.setattr(platform, name, lambda value=value: value)
        elsewhere.setattr(sys, 'executable', str(portable))
        elsewhere.setattr(importlib.util, 'find_spec', lambda name, package=None: None)     # PyAV (scrcpy's decoder) is not installed there
        elsewhere.setenv('ProgramFiles', str(programs))
        elsewhere.setenv('ProgramFiles(x86)', str(programs))
        there = render(build(dash, tmp_path / 'there', monkeypatch, chosen))
    assert there == here, 'a sample of the runtime families recorded a fact of the machine that generated it'
    document = json.loads(here)['cases']['runtime_state']
    first = next(iter(document.values()))['body']['environment']
    assert first['os'] == dict(system='Windows', release='11', architecture='AMD64') and first['python'] == dict(version='3.12.0', kind='source')


def test_the_r12c_families_are_made_by_the_real_routes_and_are_the_same_twice(dash, tmp_path, monkeypatch):
    """The four families r12c added (the stable codes of run-templates/add, the mixed reset-all, the run-entry parity, the queue bounds) on their own: every assertion
    of their generators runs (a refusal that is not refused, a code that is missing, an override that is not reset fails here), and two runs are byte-identical.
    The full contract below also compares them with the committed fixture, but needs minutes; this is the fast discriminating test."""
    chosen = [run_templates_add_coded_cases, run_controls_live_reset_all_mixed_cases, run_entry_patch_parity_cases, shopping_queue_bounds_cases]
    first = render(build(dash, tmp_path / 'one', monkeypatch, chosen))
    assert render(build(dash, tmp_path / 'two', monkeypatch, chosen)) == first
    families = json.loads(first)['cases']
    assert {name: len(cases) for name, cases in families.items()} == {TC: 67, RMX: 13, PAR: 54, SB: 25}


def test_the_r12d_families_are_made_by_the_real_routes_and_are_the_same_twice(dash, tmp_path, monkeypatch):
    """The four families r12d added (the guard on a run's kind, the fenced writes to the active profile, the failed Ad gems attempt through the collector's own poll, what removing the plan changes) on their own: every assertion of their generators runs, and two
    runs are byte-identical. The parity family, whose kind case changed, is generated with them. The full contract below also compares everything with the committed fixture."""
    chosen = [run_entry_patch_parity_cases, run_kind_guard_cases, profile_patch_fenced_cases, ad_gems_failed_attempt_poll_cases, plan_removal_effects_cases]
    first = render(build(dash, tmp_path / 'one', monkeypatch, chosen))
    assert render(build(dash, tmp_path / 'two', monkeypatch, chosen)) == first
    families = json.loads(first)['cases']
    assert {name: len(cases) for name, cases in families.items()} == {PAR: 54, KG: 21, PFD: 28, AP: 5, PRE: 11}


def test_the_r12e_family_is_made_by_the_real_routes_and_is_the_same_twice(dash, tmp_path, monkeypatch):
    """The family r12e added (more of an inactive profile) on its own: every assertion of its generator runs (a delete that is not refused, a revision that moved, a file that was touched fails here),
    and two runs are byte-identical (a file modification time that was not pinned shows here). The full contract below also compares it with the committed fixture."""
    first = render(build(dash, tmp_path / 'one', monkeypatch, [profile_target_more_cases]))
    assert render(build(dash, tmp_path / 'two', monkeypatch, [profile_target_more_cases])) == first
    families = json.loads(first)['cases']
    assert {name: len(cases) for name, cases in families.items()} == {PTM: 72}


def test_the_r12e_settings_family_is_made_by_the_real_routes_and_is_the_same_twice(dash, tmp_path, monkeypatch):
    """The second family r12e added (more of the settings modules: a machine default over an emulator override, a preset the options list does not offer, empty tournament lists) on its own:
    every assertion of its generator runs (an override that changed, a refusal that wrote, a list that was not stored as sent fails here), and two runs are byte-identical."""
    first = render(build(dash, tmp_path / 'one', monkeypatch, [settings_modules_more_cases]))
    assert render(build(dash, tmp_path / 'two', monkeypatch, [settings_modules_more_cases])) == first
    families = json.loads(first)['cases']
    assert {name: len(cases) for name, cases in families.items()} == {SMM: 58}


def test_the_reads_of_settings_modules_more_are_the_states_the_published_saves_start_from(dash, tmp_path, monkeypatch):
    """A read recorded in settings_modules_more "just before" a save of a published family must be the state that save starts from: the revision the published request carries is the revision of the read (the cases are
    compared before the ids are aliased, so they are the routes' own values), and the world that reaches it differs from the published one only by its directory."""
    cases = generate(dash, tmp_path / 'guard', monkeypatch, [settings_modules_cases, loadout_restore_eligibility_cases, settings_modules_more_cases])
    more, published, eligibility, restore = cases[SMM], cases[SM], cases[LE], cases[LR]
    assert more['read_before_saved_instance_defaults']['body']['revision'] == published['saved_instance_defaults']['request']['body']['revision']
    assert more['read_before_saved_instance_defaults']['body']['context'] == published['saved_instance_defaults']['request']['body']['context']
    assert more['saved_instance_defaults_with_fallback_and_autostart_off']['request']['body']['revision'] == published['saved_instance_defaults']['body']['revision']
    assert more['read_before_saved_tournament_cards_empty_lists']['body']['revision'] == published['read_after_saved_tournament_cards']['body']['revision']
    assert more['read_before_saved_modules_restore']['body']['config_revision'] == restore['saved_modules_restore']['request']['body']['expected_revision']
    assert more['read_before_saved_modules_restore']['body']['loadouts']['plain'] == restore['saved_cards_restore']['request']['body']['body']
    assert more['read_before_the_clearing_refusal']['body']['config_revision'] == eligibility['refused_clearing_the_stored_restore_value_of_a_loadout_no_run_uses']['request']['body']['expected_revision']


def test_the_r12f_family_is_made_by_the_real_routes_and_is_the_same_twice(dash, tmp_path, monkeypatch):
    """The family r12f added (a modules restore alone, a profile with no blueprints, a malformed tournament list) on its own: every assertion of its generator runs, and two runs are byte-identical."""
    first = render(build(dash, tmp_path / 'one', monkeypatch, [settings_modules_more_2_cases]))
    assert render(build(dash, tmp_path / 'two', monkeypatch, [settings_modules_more_2_cases])) == first
    families = json.loads(first)['cases']
    assert {name: len(cases) for name, cases in families.items()} == {SM2: 20}


def test_the_restore_only_save_starts_from_the_recorded_read(dash, tmp_path, monkeypatch):
    """saved_modules_restore_only is saved "from that predecessor": its expected_revision is the revision of settings_modules_more.read_before_saved_modules_restore (compared on the generator's own
    values, before the ids are aliased), and the request is exactly the one the page sends - the context with a null account, the body with a modules_restore and no `modules`."""
    cases = generate(dash, tmp_path / 'guard', monkeypatch, [settings_modules_more_cases, settings_modules_more_2_cases])
    read, save, after = cases[SMM]['read_before_saved_modules_restore'], cases[SM2]['saved_modules_restore_only'], cases[SM2]['read_after_saved_modules_restore_only']
    assert save['request']['body'] == dict(name='plain', expected_revision=read['body']['config_revision'], expected_context=PAGE_CONTEXT, body=MODULES_ONLY)
    assert set(save['request']['body']['body']) == {'cards', 'cards_restore', 'modules_restore'}
    assert after['body']['loadouts']['plain'] == MODULES_ONLY and after['body']['config_revision'] == save['body']['config_revision'] != read['body']['config_revision']


def test_the_r12f_profile_family_is_made_by_the_real_routes_and_is_the_same_twice(dash, tmp_path, monkeypatch):
    """The second family r12f added (seven run-entry edits of an inactive profile) on its own: every assertion of its generator runs (an edit that changes more than its path, a read that is not the answer, a touched active profile fails here), and two runs are byte-identical."""
    first = render(build(dash, tmp_path / 'one', monkeypatch, [profile_target_more_2_cases]))
    assert render(build(dash, tmp_path / 'two', monkeypatch, [profile_target_more_2_cases])) == first
    families = json.loads(first)['cases']
    assert {name: len(cases) for name, cases in families.items()} == {PM2: 23}


def test_the_families_are_generated_in_a_fixed_order():
    assert [family.__name__ for family in FAMILIES][:len(PUBLISHED_ORDER)] == PUBLISHED_ORDER


def test_the_published_page_contract_matches_what_the_routes_answer(dash, tmp_path, monkeypatch):
    generated = build(dash, tmp_path / 'first', monkeypatch)
    text = render(generated)
    for leak in (str(tmp_path), str(tmp_path).replace('\\', '\\\\'), tmp_path.as_posix(),
                 str(Path(dash.__file__).parents[1]), str(Path(dash.__file__).parents[1]).replace('\\', '\\\\')):
        assert leak not in text, 'a machine path leaked into the contract'
    assert 'NaN' not in text and 'Infinity' not in text
    assert render(build(dash, tmp_path / 'second', monkeypatch)) == text, 'the contract differs between two runs'
    target = os.environ.get(OUTPUT)
    if target:
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_bytes(text.encode('utf-8'))
        pytest.skip(f'contract written to {target}; the committed fixture was not compared')
    if os.environ.get(REGENERATE):
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_bytes(text.encode('utf-8'))
        pytest.skip('fixture regenerated: review the diff and tell the UI author')
    assert FIXTURE.exists(), f'run with {REGENERATE}=1 to publish it'
    assert json.loads(FIXTURE.read_text(encoding='utf-8')) == generated


def test_the_r17a_family_is_made_by_the_real_routes_and_is_the_same_twice(dash, tmp_path, monkeypatch):
    """The family r17a added (the generic executions history: list, cursor, filters, refusals, damaged rows, newer schema, the battle detail) on its own: every assertion of its generator runs,
    and two runs are byte-identical."""
    first = render(build(dash, tmp_path / 'one', monkeypatch, [action_executions_cases]))
    assert render(build(dash, tmp_path / 'two', monkeypatch, [action_executions_cases])) == first
    families = json.loads(first)['cases']
    assert {name: len(cases) for name, cases in families.items()} == {AX: 35}


def test_the_r17a_projection_family_is_made_by_the_real_routes_and_is_the_same_twice(dash, tmp_path, monkeypatch):
    """The projection family r17a added (runtime.actions of a run driver) on its own: every assertion of its generator runs, and two runs are byte-identical."""
    first = render(build(dash, tmp_path / 'one', monkeypatch, [battle_actions_projection_cases]))
    assert render(build(dash, tmp_path / 'two', monkeypatch, [battle_actions_projection_cases])) == first
    families = json.loads(first)['cases']
    assert {name: len(cases) for name, cases in families.items()} == {AP_FAMILY: 14}


def test_the_r17b_executions_family_is_made_by_the_real_routes_and_is_the_same_twice(dash, tmp_path, monkeypatch):
    """The family r17b added (the Run now execution, a cut-off battle, a settled row, the harvest launch attempts) on its own: every assertion of its generator runs, and two runs are byte-identical."""
    first = render(build(dash, tmp_path / 'one', monkeypatch, [action_executions_more_cases]))
    assert render(build(dash, tmp_path / 'two', monkeypatch, [action_executions_more_cases])) == first
    families = json.loads(first)['cases']
    assert {name: len(cases) for name, cases in families.items()} == {AXM: 9}


def test_the_p10a_family_is_made_by_the_real_routes_and_is_the_same_twice(dash, tmp_path, monkeypatch):
    """The family p10a added (/api/runtime.release over synthetic installs: intact, source or app edited, added or missing, unreadable, an identity that cannot be parsed) on its own: every assertion of
    its generator runs, and two runs are byte-identical (a hash, a path or a time of the machine would differ). The full contract above also compares it with the committed fixture."""
    first = render(build(dash, tmp_path / 'one', monkeypatch, [runtime_release_cases]))
    assert render(build(dash, tmp_path / 'two', monkeypatch, [runtime_release_cases])) == first
    families = json.loads(first)['cases']
    assert {name: len(cases) for name, cases in families.items()} == {REL: 8}
    for case in families[REL].values():
        assert case['status'] == 200 and set(case['body']['release']) == {*build_info.IDENTITY_FIELDS, 'matches_source', 'matches_app_build', 'error'}
