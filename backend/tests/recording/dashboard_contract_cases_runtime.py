"""Contract cases of the Runtime page (README rows T-01 and T-02).

Families: runtime_state (GET /api/runtime) and runtime_diagnostics (GET /api/diagnostics/events).

/api/runtime reports, as `release`, what the tree it runs from says it is (item 10 p10a, runtime/build_info.release_identity): a checkout
(every sample over a world's own folder is one, with nothing recorded), or an install with the identity its build wrote and the two
comparisons with the files now on disk. The install is a small synthetic tree of the world, never this machine's. The samples of this file
only see the nothing-recorded answer (every field null, no hash); the installs are the family runtime_release (runtime_release_cases), a
new family appended last because the hashes an install carries are aliased in order of appearance and would renumber every family after them.

/api/runtime reports the build of the SERVER process against the source on disk, the capabilities, the live workers and the
operations that persist a file. The server's own build snapshot (`_BUILD`) is replaced by one taken over a small synthetic
source tree (so no machine identity is read), then the real `build_info.compare` runs over that tree: unchanged, changed on
disk, unreadable, and a snapshot that could not be taken at all are each a real answer.

/api/runtime also reports the local `environment` (runtime/environment.py): the operating system, the interpreter, the emulator
locators and the scrcpy files. Those are facts of whichever machine generates the sample, so `pin_environment` fixes every host
input and the committed answers never depend on the machine, its install folders or the build of its web app. The family
runtime_environment (dashboard_contract_cases_environment.py) records the environment itself, state by state.

/api/diagnostics/events reads the raw JSONL events under <root>/logs/<instance>/. The events are written by the real
writer (runtime.diagnostics.write_event) with its clock, ids, process and file names pinned; the bounds the route
applies (files, bytes per file) are lowered for the truncation cases, and the writer's health counters are set for the failure
case. Malformed lines, a half-written last line and an unreadable file are put on disk as such.
"""
import contextlib
import json
import os
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

from control import diagnostics as query_module
from dashboard_contract_pages_world import pin_mtimes
from device.preview import scrcpy_package
from runtime import build_info, diagnostics, environment
from test_build_info import install as release_install
from test_dashboard_contract import raising

RT, DG = 'runtime_state', 'runtime_diagnostics'
FILES = {'backend/app.py': 'print("backend")\n', 'backend/control/ops.py': 'VALUE = 1\n', 'frontend/dashboard.py': 'APP = True\n'}


def source_tree(w, name='source'):
    root = w.root.parent / f'{w.name}-{name}'
    for rel, text in FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    return root


def pin_environment(w, *, system='Windows', mumu=True, bluestacks=False):
    """Every HOST input of runtime.environment.report fixed, so the report is the same on every machine: the operating
    system and interpreter facts (Windows 11 AMD64, Python 3.12.0 outside a portable runtime), the emulator locators (which read
    the machine's Program Files: here they find a synthetic MuMuManager.exe, and BlueStacks' player only when asked), scrcpy's
    server path (a file of the world, absent) and its decoder module (found), the fixed clock for the probe's timestamp and
    deadline, and the stored OCR probe result back at 'not probed'. What the report reads under the repository root (platform-tools,
    the web app, the OCR script, the release identity) is the root's own folder: a world that wants one creates it."""
    w.mp.setattr(environment, 'platform', SimpleNamespace(system=lambda: system, release=lambda: '11', machine=lambda: 'AMD64',
                                                         python_version=lambda: '3.12.0'))
    w.mp.setattr(environment, 'sys', SimpleNamespace(executable=str(w.root.parent / f'{w.name}-python' / 'python.exe')))
    w.mp.setattr(environment, 'time', w.clock)
    w.mp.setattr(environment, 'shutil', SimpleNamespace(which=lambda name: None))     # no adb on a PATH that is not the world's
    w.mp.setattr(environment, '_windows_ocr', dict(state='unknown', basis='not probed', checked_at=None, detail='not probed'))
    w.mp.setattr(scrcpy_package, 'default_server_path', lambda: w.root.parent / f'{w.name}-scrcpy' / 'scrcpy-server')
    w.mp.setattr(scrcpy_package, 'importlib', SimpleNamespace(util=SimpleNamespace(find_spec=lambda name: object() if name == 'av' else None)))
    emulators = w.root.parent / f'{w.name}-emulators'
    emulators.mkdir(parents=True, exist_ok=True)
    found = {}
    for name, wanted, leaf in (('mumu', mumu, 'MuMuManager.exe'), ('bluestacks', bluestacks, 'HD-Player.exe')):
        if wanted:
            (emulators / leaf).write_bytes(b'MZ')
            found[name] = str(emulators / leaf)
    w.mp.setattr(w.dash, '_mumu_manager', lambda: found.get('mumu'))
    w.mp.setattr(w.dash, '_bluestacks_player', lambda: found.get('bluestacks'))


def pin_build(w, root, *, started=None):
    """The server's snapshot over `root` (its ids and times pinned) and compare() over the same tree, and the environment it reports."""
    pin_environment(w)
    snapshot = build_info.snapshot(root, 'dashboard') if started is None else started
    snapshot.update(pid=os.getpid(), session_id='server-session-0001', captured_at=w.clock.now - 3600)
    real_compare = build_info.compare
    w.mp.setattr(w.dash, '_BUILD', snapshot)
    w.mp.setattr(w.dash.build_info, 'compare', lambda _root, begun: real_compare(root, begun))
    return snapshot


def runtime_state_cases(gen):
    with gen.world('rt_current') as w:
        pin_build(w, source_tree(w))
        got = w.get(RT, 'idle_server_build_matches_the_source_on_disk', '/api/runtime').get_json()
        assert got['server']['source_changed'] is False and got['workers'] == [] and got['persistence_operations'] == []
        assert got['release']['kind'] == 'checkout' and got['release']['matches_source'] is None and got['release']['version'] is None
        assert 'runtime_identity' in got['capabilities'] and got['api_contract'] == 1
    with gen.world('rt_changed') as w:
        root = source_tree(w)
        pin_build(w, root)
        (root / 'backend' / 'control' / 'ops.py').write_text('VALUE = 2\n', encoding='utf-8')
        got = w.get(RT, 'server_started_before_the_source_changed_restart_needed', '/api/runtime').get_json()
        assert got['server']['source_changed'] is True and got['server']['source_fingerprint'] != got['server']['disk_source_fingerprint']
    with gen.world('rt_unreadable') as w:
        root = source_tree(w)
        pin_build(w, root)
        real = build_info.source_fingerprint
        w.mp.setattr(build_info, 'source_fingerprint', raising(PermissionError('backend/app.py is locked')))
        got = w.get(RT, 'disk_source_cannot_be_read_so_freshness_is_unknown', '/api/runtime').get_json()
        assert got['server']['source_changed'] is None and got['server']['error']['type'] == 'PermissionError'
        w.mp.setattr(build_info, 'source_fingerprint', real)
    with gen.world('rt_no_snapshot') as w:
        empty = w.root.parent / f'{w.name}-empty'
        empty.mkdir(parents=True, exist_ok=True)
        pin_build(w, empty)
        got = w.get(RT, 'no_snapshot_could_be_taken_at_start_nothing_to_compare', '/api/runtime').get_json()
        assert got['server']['source_fingerprint'] is None and got['server']['error'] and got['server']['source_changed'] is None
    with gen.world('rt_worker_alive') as w:
        pin_build(w, source_tree(w))
        w.running('gems', {'preset': 'bp_farm'}, args=['--preset', 'bp_farm'])
        got = w.get(RT, 'worker_alive_and_verified', '/api/runtime').get_json()
        assert [x['alive'] for x in got['workers']] == [True] and got['workers'][0]['kind'] == 'gems'
    with gen.world('rt_worker_dead') as w:
        pin_build(w, source_tree(w))
        op = w.running('gems', {'preset': 'bp_farm'}, args=['--preset', 'bp_farm'])
        w.table.exit(op['worker']['pid'])
        got = w.get(RT, 'worker_recorded_but_proven_gone_until_the_refresher_retires_it', '/api/runtime').get_json()
        assert [x['alive'] for x in got['workers']] == [False]
    with gen.world('rt_worker_unverifiable') as w:
        pin_build(w, source_tree(w))
        op = w.running('gems', {'preset': 'bp_farm'}, args=['--preset', 'bp_farm'])
        w.table.denied.add(op['worker']['pid'])
        got = w.get(RT, 'worker_that_cannot_be_verified_is_unknown_not_dead', '/api/runtime').get_json()
        assert [x['alive'] for x in got['workers']] == [None]
    with gen.world('rt_worker_pending') as w:
        pin_build(w, source_tree(w))
        w.store.accept('gems', w.context({'preset': 'bp_farm'}), {'preset': 'bp_farm'}, 'rt-accepted')
        got = w.get(RT, 'operation_accepted_but_no_worker_spawned_yet', '/api/runtime').get_json()
        assert got['workers'][0]['worker'] is None and got['workers'][0]['status'] == 'accepted'
    with gen.world('rt_persistence') as w:
        pin_build(w, source_tree(w))
        op, _ = w.store.accept('profile_promote', w.context(), {}, 'rt-promote')
        w.store.transition(op['id'], op['revision'], 'running')
        got = w.get(RT, 'a_profile_write_is_listed_as_a_persistence_operation_not_a_worker', '/api/runtime').get_json()
        assert got['workers'] == [] and [x['kind'] for x in got['persistence_operations']] == ['profile_promote']
    with gen.world('rt_refresh_stale') as w:
        from types import SimpleNamespace as NS
        pin_build(w, source_tree(w))
        w.worker(4521, 'orchestrator.py', args=['--preset', 'bp_farm'])
        w.dash._procs_refresh()
        w.advance(7.5)
        w.mp.setattr(w.dash, '_PROCS_THREAD', NS(is_alive=lambda: True))
        got = w.get(RT, 'process_discovery_stale', '/api/runtime').get_json()
        assert got['process_refresh']['state'] in ('stale', 'stalled') or got['process_refresh'].get('stale') is not None
    with gen.world('rt_refresh_error') as w:
        from runtime import runners
        pin_build(w, source_tree(w))
        w.worker(4531, 'orchestrator.py', args=['--preset', 'bp_farm'])
        w.dash._procs_refresh()
        w.advance(3)
        w.mp.setattr(runners, 'other_runners', raising(OSError('the process table could not be read')))
        w.dash._procs_refresh()
        w.mp.setattr(w.dash, '_PROCS_THREAD', SimpleNamespace(is_alive=lambda: True))
        got = w.get(RT, 'process_discovery_unreadable', '/api/runtime').get_json()
        assert got['process_refresh']['error']


REL = 'runtime_release'


@contextlib.contextmanager
def release_world(gen, name, **record):
    """A world that runs from an install of its own: <root>/install holds a source file, a built app with its inventory and the
    identity naming both (test_build_info.install), and the dashboard's repository root is that folder."""
    with gen.world(name) as w:
        install = w.root / 'install'
        release_install(install, record=record)
        w.mp.setattr(w.dash, 'ROOT', str(install / 'backend'))
        pin_build(w, install)
        yield w, install


def runtime_release_cases(gen):
    """Family runtime_release: `release` for an install - what the build recorded, and whether the files on disk still are those."""
    app = Path(*build_info.APP_BUILD)
    with release_world(gen, 'rt_release_intact', kind='portable-windows-x64', python=dict(version='3.12.10', sha256='f' * 64)) as (w, install):
        got = w.get(REL, 'install_whose_files_are_the_ones_the_build_recorded', '/api/runtime').get_json()
        release = got['release']
        assert (release['kind'], release['matches_source'], release['matches_app_build'], release['error']) == ('portable-windows-x64', True, True, None)
        assert release['source_fingerprint'] == got['server']['source_fingerprint'] and release['python']['version'] == '3.12.10'
        assert got['environment']['release'] == dict(kind='portable-windows-x64')
    with release_world(gen, 'rt_release_source_edited') as (w, install):
        (install / 'backend' / 'example.py').write_text('x = 2', encoding='utf-8')
        release = w.get(REL, 'source_edited_after_the_install_matches_source_false', '/api/runtime').get_json()['release']
        assert (release['matches_source'], release['matches_app_build']) == (False, True)
        (install / 'backend' / 'example.py').write_text('x = 1', encoding='utf-8')
        (install / 'backend' / 'added.py').write_text('y = 1', encoding='utf-8')
        release = w.get(REL, 'source_file_added_after_the_install_matches_source_false', '/api/runtime').get_json()['release']
        assert (release['matches_source'], release['matches_app_build']) == (False, True)
    with release_world(gen, 'rt_release_app_edited') as (w, install):
        (install / app / 'assets' / 'main.js').write_bytes(b'console.log(2)')
        release = w.get(REL, 'app_file_edited_after_the_install_matches_app_build_false', '/api/runtime').get_json()['release']
        assert (release['matches_source'], release['matches_app_build']) == (True, False)
        (install / app / 'assets' / 'main.js').write_bytes(b'console.log(1)')
        (install / app / 'assets' / 'extra.js').write_bytes(b'extra')
        release = w.get(REL, 'app_file_added_after_the_install_matches_app_build_false', '/api/runtime').get_json()['release']
        assert (release['matches_source'], release['matches_app_build']) == (True, False)
        (install / app / 'assets' / 'extra.js').unlink()
        (install / app / build_info.APP_INVENTORY).unlink()
        release = w.get(REL, 'app_inventory_missing_matches_app_build_false', '/api/runtime').get_json()['release']
        assert (release['matches_source'], release['matches_app_build']) == (True, False)
    with release_world(gen, 'rt_release_unreadable') as (w, install):
        real = build_info.source_fingerprint
        w.mp.setattr(build_info, 'source_fingerprint', raising(PermissionError('backend/example.py is locked')))
        release = w.get(REL, 'install_source_cannot_be_read_so_matches_source_is_unknown', '/api/runtime').get_json()['release']
        assert (release['kind'], release['matches_source'], release['matches_app_build']) == ('source', None, True)
        w.mp.setattr(build_info, 'source_fingerprint', real)
        (install / build_info.RELEASE_IDENTITY).write_text('{broken', encoding='utf-8')
        release = w.get(REL, 'identity_that_cannot_be_parsed_is_unknown_with_the_reason', '/api/runtime').get_json()['release']
        assert (release['kind'], release['error'], release['matches_source'], release['version']) == ('unknown', 'not valid JSON', None, None)


# ----------------------------------------------------------------- diagnostics (T-02)
STRFTIME = time.strftime


class Clock:
    """runtime.diagnostics' view of time: the world's fixed clock."""

    def __init__(self, w):
        self.w = w

    def time(self):
        return self.w.clock.now

    def monotonic_ns(self):
        return int(self.w.clock.now * 1e9)

    strftime = staticmethod(STRFTIME)


WRITER_PID = 44242      # five digits, as the committed byte offsets were measured; the contract publishes it as 4242 (pin_process_identity)


class _FixedPid:
    """`os` as runtime.diagnostics sees it, with ONE process id. The writer puts os.getpid() into every record and every file name, and
    this contract publishes byte offsets into those files, so a pid with another number of digits moved every offset after it: the family
    only matched when the process that ran the test had a pid as long as the one that generated the fixture (a regeneration under a
    4-digit pid shifted each offset by one byte per record against the committed 5-digit run). The contract rewrites the pid to 4242 afterwards
    (dashboard.pin_processes); this pins it while the records are WRITTEN, which is what the offsets measure."""
    getpid = staticmethod(lambda: WRITER_PID)

    def __getattr__(self, name):
        return getattr(os, name)


def pin_writer(w):
    counter = [0]
    w.mp.setattr(diagnostics, 'os', _FixedPid())

    def uuid4():
        counter[0] += 1
        return uuid.UUID(int=(0x7B000000 | counter[0]) << 96 | 0x5EED, version=4)
    w.mp.setattr(diagnostics, 'time', Clock(w))
    w.mp.setattr(diagnostics, 'uuid', SimpleNamespace(uuid4=uuid4))
    for name, value in (('_started', '20270115_080000'), ('_process_id', 'p' * 32), ('_session_id', 'session-diag'), ('_sequence', 0),
                        ('_write_errors', 0), ('_last_write_error', None)):
        w.mp.setattr(diagnostics, name, value)
    w.mp.setattr(diagnostics, 'file_name', lambda: 'events_20270115_080000_4242_pppppppp.jsonl')


def emit(w, folder, kind, **fields):
    """One event through the real writer into <root>/logs/<folder>/, the clock moving a second per event."""
    w.advance(1)
    return diagnostics.event_to(query_module.log_root(w.root) / folder, kind, **fields)


def events(w, case, query=''):
    return w.get(DG, case, '/api/diagnostics/events' + query)


def runtime_diagnostics_cases(gen):
    with gen.world('dg_empty') as w:
        pin_writer(w)
        got = events(w, 'no_log_folder_yet_nothing_scanned').get_json()
        assert got['events'] == [] and got['coverage']['files_scanned'] == 0 and got['writer_health']['write_errors'] == 0
    with gen.world('dg_filters') as w:
        pin_writer(w)
        emit(w, 'main', 'battle_command_accepted', operation_id='op-1', battle_id='b-1', command_id='c-1', request_id='req-1', trace_id='t-1', outcome='accepted')
        emit(w, 'main', 'ad_gem_attempt_end', operation_id='op-1', battle_id='b-1', claim_attempt_id='claim-1', outcome='unconfirmed', reason='timeout')
        emit(w, 'main', 'orbit_sweep', operation_id='op-1', sweep_id='sweep-1', window_id='win-1', outcome='completed')
        emit(w, 'other', 'harvest_tick', instance='main', offer_id='offer-1', outcome='skipped')
        emit(w, 'other', 'runner_error', operation_id='op-2', error='the device went away', level='error')
        all_events = events(w, 'every_event_oldest_first_with_log_coordinates').get_json()
        assert [e['kind'] for e in all_events['events']] == ['battle_command_accepted', 'ad_gem_attempt_end', 'orbit_sweep', 'harvest_tick', 'runner_error']
        assert all_events['coverage'] == dict(all_events['coverage'], files_scanned=2, matched_in_scan=5, returned=5, truncated=False, malformed_records=0)
        for case, query, count in (('filter_by_kind', '?kind=orbit_sweep', 1), ('filter_by_operation', '?operation_id=op-1', 3),
                                   ('filter_by_battle', '?battle_id=b-1', 2), ('filter_by_command', '?command_id=c-1', 1),
                                   ('filter_by_request_and_trace', '?request_id=req-1&trace_id=t-1', 1),
                                   ('filter_by_attempt_matches_a_claim_attempt_too', '?attempt_id=claim-1', 1),
                                   ('filter_by_sweep', '?sweep_id=sweep-1', 1), ('filter_by_window', '?window_id=win-1', 1),
                                   ('filter_by_offer', '?offer_id=offer-1', 1),
                                   ('filter_by_instance_uses_the_folder_unless_the_record_names_one', '?instance=other', 1),
                                   ('filter_by_the_instance_a_record_names', '?instance=main', 4),
                                   ('filters_combined_none_match', '?operation_id=op-1&kind=runner_error', 0),
                                   ('failures_only_includes_refusals_and_unconfirmed', '?failures=1', 3),
                                   ('since_a_time', '?since=%s' % (w.clock.now - 2.5), 3),
                                   ('limit_one_returns_the_newest_and_says_truncated', '?limit=1', 1)):
            got = events(w, case, query).get_json()
            assert len(got['events']) == count, (case, len(got['events']))
        assert w.client.get('/api/diagnostics/events?limit=1').get_json()['coverage']['truncated'] is True
        assert all(e['_log']['failure'] for e in w.client.get('/api/diagnostics/events?failures=1').get_json()['events'])
    with gen.world('dg_bounds') as w:
        pin_writer(w)
        w.mp.setattr(query_module, 'MAX_FILE_BYTES', 3000)
        for number in range(12):
            emit(w, 'main', 'tick', operation_id='op-1', number=number, padding='x' * 200)
        got = events(w, 'a_log_larger_than_the_per_file_bound_is_read_from_its_tail', '').get_json()
        assert got['coverage']['truncated'] is True and 0 < len(got['events']) < 12 and got['events'][-1]['number'] == 11
    with gen.world('dg_files') as w:
        pin_writer(w)
        w.mp.setattr(query_module, 'MAX_FILES', 2)
        for instance in ('a', 'b', 'c'):
            emit(w, instance, 'tick', number=1)
            pin_mtimes(w, 'logs/' + instance)
        base = w.root / 'logs'
        for number, instance in enumerate(('a', 'b', 'c')):
            stamp = w.clock.now - 100 + number
            os.utime(base / instance / 'events_20270115_080000_4242_pppppppp.jsonl', (stamp, stamp))
        got = events(w, 'more_files_than_the_bound_the_newest_are_read_and_truncation_is_reported', '').get_json()
        assert got['coverage']['files_scanned'] == 2 and got['coverage']['truncated'] is True
    with gen.world('dg_malformed') as w:
        pin_writer(w)
        emit(w, 'main', 'tick', number=1)
        path = w.root / 'logs' / 'main' / 'events_20270115_080000_4242_pppppppp.jsonl'
        with path.open('a', encoding='utf-8') as handle:
            handle.write('this is not json\n[1, 2, 3]\n{"t": "soon", "kind": "bad_time"}\n{"t": 1e999, "kind": "infinite_time"}\n')
            handle.write('{"t": 1800000100.0, "kind": "half_written"')
        got = events(w, 'malformed_lines_are_counted_and_a_half_written_last_line_is_truncation', '').get_json()
        assert [e['kind'] for e in got['events']] == ['tick'] and got['coverage']['malformed_records'] == 4 and got['coverage']['truncated'] is True
    with gen.world('dg_unreadable') as w:
        pin_writer(w)
        emit(w, 'main', 'tick', number=1)
        emit(w, 'locked', 'tick', number=2)
        pin_mtimes(w, 'logs/main')
        real_open = Path.open

        def guarded(self, *args, **kwargs):
            if 'locked' in self.parts and self.name.startswith('events_'):
                raise PermissionError('the file is locked by another process')
            return real_open(self, *args, **kwargs)
        w.mp.setattr(Path, 'open', guarded)
        got = events(w, 'one_file_cannot_be_read_the_others_are_returned_and_the_error_is_listed', '').get_json()
        w.mp.setattr(Path, 'open', real_open)
        assert [e['number'] for e in got['events']] == [1] and got['coverage']['errors'][0]['file'].startswith('locked/')
    with gen.world('dg_health') as w:
        pin_writer(w)
        w.mp.setattr(diagnostics, '_write_errors', 3)
        w.mp.setattr(diagnostics, '_last_write_error', {'exception_type': 'PermissionError', 'message': 'the log file is locked', 't': 1800000000.0, 'event_id': 'e' * 32})
        got = events(w, 'writer_health_reports_failed_writes', '').get_json()
        assert got['writer_health']['write_errors'] == 3 and got['writer_health']['last_error']['exception_type'] == 'PermissionError'
    with gen.world('dg_invalid') as w:
        pin_writer(w)
        for case, query in (('unknown_filter_refused', '?colour=red'), ('limit_zero_refused', '?limit=0'), ('limit_1001_refused', '?limit=1001'),
                            ('limit_text_refused', '?limit=abc'), ('since_text_refused', '?since=abc'), ('since_negative_refused', '?since=-1'),
                            ('since_not_finite_refused', '?since=inf'), ('failures_must_be_0_or_1', '?failures=2'),
                            ('filter_longer_than_200_characters_refused', '?kind=' + 'k' * 201)):
            reply = events(w, case, query)
            assert reply.status_code == 400 and reply.get_json()['ok'] is False, case
        assert events(w, 'limit_1000_is_accepted', '?limit=1000').status_code == 200


def run_runtime_cases(gen):
    runtime_state_cases(gen)
    runtime_diagnostics_cases(gen)