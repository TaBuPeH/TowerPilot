"""Contract cases of the Emulators page (README rows M-01, M-02, M-03).

Families: emulator_inventory (GET /api/wizard/emulators, /api/wizard/resolution), emulator_actions (POST /api/wizard/adopt,
launch, bluestacks/prepare, reconnect refusals) and emulator_tools (GET/POST /api/wizard/tools[/install]).

The emulators are the wizard_emulators world of test_dashboard_contract.py: install folders under a temporary
ProgramFiles/ProgramData, a process list the scenario supplies, the manager's JSON inventory, the adb server and the
emulator ports in memory (dashboard_contract_world.FakeAdb). Two additions here: the adb stand-in also answers the
transport + exec conversation (a raw screencap header, so GET /api/wizard/resolution sees a frame) and the tools installer's
thread runs inline (a download never starts). Nothing launches, connects or installs anything.
"""
import json
import struct
from pathlib import Path
from types import SimpleNamespace

from dashboard_contract_pages_world import raw_post
from dashboard_contract_world import okay
from test_dashboard_contract import completed, emulator_world, raising

INV, ACT, TOOLS = 'emulator_inventory', 'emulator_actions', 'emulator_tools'
TWO_VMS = {'0': {'index': 0, 'name': 'Main Tower', 'is_android_started': True, 'is_process_started': True, 'adb_port': 16384},
           '1': {'index': 1, 'name': 'Second Tower', 'is_android_started': True, 'is_process_started': True, 'adb_port': 16416}}
ONE_VM = {'0': TWO_VMS['0'], '3': {'index': 3, 'name': 'Stopped Tower', 'is_android_started': False, 'is_process_started': False}}


def manager(inventory):
    return lambda args, **kw: completed(stdout=json.dumps(inventory))


def serve_screen(w, width=1080, height=2560, *, flat=None):
    """The adb stand-in answers host:transport: and exec: (a screencap header `width x height`), and 'echo ok'."""
    original = w.adb.reply

    def reply(payload):
        if payload.startswith('host:transport:'):
            w.adb.requests.append(payload)
            return b'OKAY'
        if payload.startswith('exec:'):
            w.adb.requests.append(payload)
            return b'OKAY' + (flat if flat is not None else struct.pack('<II', width, height) + b'\0' * 16)
        return original(payload)
    w.adb.reply = reply


def configure(w, serial, *, adb=None):
    def change(cfg):
        cfg['instances']['main']['serial'] = serial
        if adb:
            cfg['adb'] = {'exe': adb}
    w.edit_config(change)


def emulator_inventory_cases(gen):
    with emulator_world(gen, 'emi_two', run=manager(TWO_VMS)) as w:
        w.adb.state, w.adb.listing = 'missing', ['127.0.0.1:16384', '127.0.0.1:16416']
        got = w.get(INV, 'two_running_vms_the_configured_one_is_down_no_automatic_choice', '/api/wizard/emulators').get_json()
        assert got['suggestion'] is None and len(got['instances']) == 2
    with emulator_world(gen, 'emi_mismatch', run=manager(ONE_VM)) as w:
        configure(w, '127.0.0.1:16480')
        w.adb.state, w.adb.listing, w.adb.port = 'missing', ['127.0.0.1:16384'], False
        got = w.get(INV, 'configured_serial_differs_from_the_live_vm_one_candidate_is_suggested', '/api/wizard/emulators').get_json()
        assert got['suggestion']['serial'] == '127.0.0.1:16384' and got['configured'][0]['port_open'] is False
    with emulator_world(gen, 'emi_up', run=manager(ONE_VM)) as w:
        configure(w, '127.0.0.1:16384')
        w.adb.listing = ['127.0.0.1:16384']
        got = w.get(INV, 'configured_device_is_up_nothing_is_suggested', '/api/wizard/emulators').get_json()
        assert got['suggestion'] is None and got['configured'][0]['port_open'] is True
    with emulator_world(gen, 'emi_probed', run=manager(ONE_VM)) as w:
        mumu_adb = str(w.root / 'program-files' / 'Netease' / 'MuMuPlayer' / 'nx_main' / 'adb.exe')
        configure(w, '127.0.0.1:16384', adb=mumu_adb)
        w.adb.listing = ['127.0.0.1:16384', 'emulator-5556']
        got = w.get(INV, 'the_configured_adb_is_probed_and_lists_its_devices', '/api/wizard/emulators').get_json()
        assert got['scanned'][0]['devices'] == ['127.0.0.1:16384', 'emulator-5556']
    with emulator_world(gen, 'emi_token', run=manager(ONE_VM)) as w:
        configure(w, 'emulator-5554')
        w.adb.state, w.adb.listing = 'missing', ['127.0.0.1:16384', 'emulator-5554x']
        got = w.get(INV, 'token_serial_configured_its_port_cannot_be_probed', '/api/wizard/emulators').get_json()
        assert got['configured'][0]['port_open'] is None
    with emulator_world(gen, 'emi_also_seen', run=manager(ONE_VM)) as w:
        w.adb.state, w.adb.listing = 'missing', ['127.0.0.1:16384', 'emulator-5554']
        mumu_adb = str(w.root / 'program-files' / 'Netease' / 'MuMuPlayer' / 'nx_main' / 'adb.exe')
        configure(w, '127.0.0.1:16480', adb=mumu_adb)
        got = w.get(INV, 'one_vm_listed_twice_the_console_token_is_folded_into_also_seen', '/api/wizard/emulators').get_json()
        assert got['suggestion']['also_seen'] == ['emulator-5554']
    with emulator_world(gen, 'emi_daemon_only', manager=False) as w:
        w.adb.state, w.adb.listing = 'missing', ['emulator-5556']
        mumu_adb = None
        configure(w, '127.0.0.1:16480', adb='adb')
        got = w.get(INV, 'daemon_device_without_an_inventory_is_the_only_candidate', '/api/wizard/emulators').get_json()
        assert got['suggestion']['serial'] == 'emulator-5556'
    with emulator_world(gen, 'emi_placeholder', run=manager(ONE_VM)) as w:
        configure(w, '')
        w.adb.state, w.adb.listing = 'missing', ['127.0.0.1:16384']
        got = w.get(INV, 'no_serial_configured_yet_placeholder', '/api/wizard/emulators').get_json()
        assert got['config_serial_placeholder'] is True
    with emulator_world(gen, 'emi_resolution') as w:
        serve_screen(w)
        got = w.get(INV, 'resolution_portrait_reference', '/api/wizard/resolution?serial=fake:1').get_json()
        assert got['ok'] and got['reference'] is True and got['expected'] is True
    with emulator_world(gen, 'emi_resolution_other') as w:
        serve_screen(w, 1080, 2400)
        assert w.get(INV, 'resolution_portrait_not_the_reference_size', '/api/wizard/resolution?serial=fake:1').get_json()['reference'] is False
    with emulator_world(gen, 'emi_resolution_landscape') as w:
        serve_screen(w, 2560, 1080)
        got = w.get(INV, 'resolution_landscape_is_not_portrait', '/api/wizard/resolution?serial=fake:1').get_json()
        assert got['ok'] and got['expected'] is False
    with emulator_world(gen, 'emi_resolution_text') as w:
        serve_screen(w, flat=b'[Warning] Multiple displays were found, but no display id was specified' + b'\n' * 0)
        got = w.get(INV, 'resolution_the_device_answered_text_not_a_frame', '/api/wizard/resolution?serial=fake:1').get_json()
        assert got['ok'] is False
    with emulator_world(gen, 'emi_resolution_down') as w:
        w.adb.server = ConnectionRefusedError
        got = w.get(INV, 'resolution_no_adb_server', '/api/wizard/resolution?serial=fake:1').get_json()
        assert got['ok'] is False and 'no adb server' in got['error']
    with emulator_world(gen, 'emi_resolution_bad') as w:
        assert w.get(INV, 'resolution_serial_not_a_device_serial_400', '/api/wizard/resolution?serial=not%20a%20serial').status_code == 400
        assert w.get(INV, 'resolution_serial_missing_400', '/api/wizard/resolution').status_code == 400

# ------------------------------------------------------------------ actions (M-02)
def act(w, case, path, body, *, expect=None):
    reply = w.post(ACT, case, path, body)
    if expect is not None:
        assert reply.status_code == expect, (case, reply.status_code, reply.get_json())
    return reply


def mumu_adb(w):
    return str(w.root / 'program-files' / 'Netease' / 'MuMuPlayer' / 'nx_main' / 'adb.exe')


def emulator_adopt_cases(gen):
    url = '/api/wizard/adopt'
    with emulator_world(gen, 'ema_adopt', run=lambda args, **kw: completed(stdout='connected to 127.0.0.1:16384')) as w:
        got = act(w, 'adopt_a_serial_saved_and_connected', url, dict(serial='127.0.0.1:16384', adb=mumu_adb(w), expect_instance='main'), expect=200).get_json()
        assert got['ok'] and got['connected'] is True and got['instance'] == 'main'
        w.get(ACT, 'wizard_state_after_the_adopt_holds_the_receipt', '/api/wizard/state')
        w.get(ACT, 'config_after_the_adopt_names_the_serial', '/api/config')
        act(w, 'adopt_a_placeholder_instance_that_already_has_a_serial_is_refused', url,
            dict(serial='127.0.0.1:16416', expect_placeholder=True), expect=409)
        act(w, 'adopt_the_instance_changed_since_the_scan', url, dict(serial='127.0.0.1:16416', expect_instance='other'), expect=409)
        act(w, 'adopt_a_serial_that_is_not_a_serial', url, dict(serial='not a serial'), expect=400)
        act(w, 'adopt_without_a_serial', url, {}, expect=400)
        act(w, 'adopt_a_serial_that_is_not_text', url, dict(serial=5), expect=400)
        raw_post(w, ACT, 'adopt_body_not_json', url, 'serial=127.0.0.1:16384', content_type='text/plain')
        assert w.dash._ACTION_LOCK.acquire(blocking=False)
        try:
            assert act(w, 'adopt_action_lock_another_action_in_progress', url, dict(serial='127.0.0.1:16384'), expect=409).get_json()['code'] == 'action_lock'
        finally:
            w.dash._ACTION_LOCK.release()
    with emulator_world(gen, 'ema_adopt_unconnected', run=lambda args, **kw: completed(stdout='cannot connect to 127.0.0.1:16384: refused')) as w:
        got = act(w, 'adopt_saved_but_not_connected', url, dict(serial='127.0.0.1:16384', adb=mumu_adb(w)), expect=200).get_json()
        assert got['ok'] is True and got['connected'] is False
    with emulator_world(gen, 'ema_adopt_token') as w:
        serve_screen(w)
        w.adb.reply_attached = True
        got = act(w, 'adopt_an_attached_transport_verified_over_the_adb_socket', url, dict(serial='emulator-5554'), expect=200).get_json()
        assert got['connected'] is True and 'verified' in got['message']
    with emulator_world(gen, 'ema_adopt_token_down') as w:
        got = act(w, 'adopt_an_attached_transport_that_does_not_answer', url, dict(serial='emulator-5554'), expect=200).get_json()
        assert got['connected'] is False and 'did not answer' in got['message']
    with emulator_world(gen, 'ema_adopt_noinst') as w:
        w.edit_config(lambda cfg: cfg['instances'].pop('main'))
        act(w, 'adopt_the_active_instance_does_not_exist_in_config', url, dict(serial='127.0.0.1:16384'), expect=400)
    with emulator_world(gen, 'ema_adopt_owner') as w:
        w.worker(4631, 'orchestrator.py', args=['--preset', 'bp_farm'])
        assert act(w, 'adopt_refused_while_a_runner_owns_the_device', url, dict(serial='127.0.0.1:16384'), expect=409).get_json()['code'] == 'device_owned'
    with emulator_world(gen, 'ema_adopt_write') as w:
        w.mp.setattr(w.dash, 'save_config', raising(OSError('config.yaml could not be written')))
        act(w, 'adopt_the_config_could_not_be_written_500', url, dict(serial='127.0.0.1:16384'), expect=500)
        w.get(ACT, 'config_after_the_failed_adopt_is_unchanged', '/api/config')


def emulator_launch_cases(gen):
    url = '/api/wizard/launch'
    with emulator_world(gen, 'ema_launch', run=lambda args, **kw: completed(stdout='launch ok')) as w:
        configure(w, '127.0.0.1:16384')
        got = act(w, 'launch_a_mumu_vm_with_a_configured_instance_starts_the_boot_pipeline', url, dict(emulator='MuMu', index=0), expect=200).get_json()
        assert got['ok'] and 'boot pipeline started' in got['message'] and w.spawn.calls
    with emulator_world(gen, 'ema_launch_unconfigured', run=lambda args, **kw: completed(stdout='launch ok')) as w:
        configure(w, '127.0.0.1:16384')       # the instance drives VM 0; VM 3 (port 16480) is driven by none
        got = act(w, 'launch_a_vm_no_instance_uses_skips_the_boot_pipeline', url, dict(emulator='MuMu', index=3), expect=200).get_json()
        assert 'no configured instance uses' in got['message'] and not w.spawn.calls
    with emulator_world(gen, 'ema_launch_placeholder', run=lambda args, **kw: completed(stdout='launch ok')) as w:
        configure(w, '')
        got = act(w, 'launch_on_a_fresh_install_writes_the_serial_it_launched', url, dict(emulator='MuMu', index=0), expect=200).get_json()
        assert 'now points at' in got['message']
    with emulator_world(gen, 'ema_launch_failed', run=lambda args, **kw: completed(stdout='', stderr='instance 9 not found', returncode=2)) as w:
        got = act(w, 'launch_the_manager_reported_a_failure', url, dict(emulator='MuMu', index=9), expect=200).get_json()
        assert got['ok'] is False and 'not found' in got['message']
    with emulator_world(gen, 'ema_launch_refused') as w:
        act(w, 'launch_an_unsupported_emulator', url, dict(emulator='LDPlayer', index=0), expect=400)
        act(w, 'launch_mumu_without_an_index_500', url, dict(emulator='MuMu'), expect=500)
        act(w, 'launch_mumu_with_an_index_that_is_not_a_number_500', url, dict(emulator='MuMu', index='x'), expect=500)
        act(w, 'launch_bluestacks_with_a_bad_key', url, dict(emulator='BlueStacks', index='../x'), expect=400)
        act(w, 'launch_bluestacks_that_is_not_installed', url, dict(emulator='BlueStacks', index='Pie64'), expect=400)
        raw_post(w, ACT, 'launch_body_not_json', url, 'emulator=MuMu', content_type='text/plain')
    with emulator_world(gen, 'ema_launch_nomanager', manager=False) as w:
        act(w, 'launch_mumu_but_its_manager_is_not_installed', url, dict(emulator='MuMu', index=0), expect=400)
    with emulator_world(gen, 'ema_launch_owner') as w:
        w.worker(4632, 'orchestrator.py', args=['--preset', 'bp_farm'])
        assert act(w, 'launch_refused_while_a_runner_owns_the_device', url, dict(emulator='MuMu', index=0), expect=409).get_json()['code'] == 'device_owned'
    with emulator_world(gen, 'ema_launch_raised') as w:
        w.mp.setattr(w.dash, '_run', raising(OSError('MuMuManager.exe could not be started')))
        act(w, 'launch_the_manager_could_not_be_started_500', url, dict(emulator='MuMu', index=0), expect=500)


BLUESTACKS_CONF = ('bst.enable_adb_access="0"\nbst.instance.Pie64.display_name="Pie64"\nbst.instance.Pie64.fb_width="1080"\n'
                   'bst.instance.Pie64.fb_height="2560"\nbst.instance.Pie64.dpi="240"\n')


def emulator_bluestacks_cases(gen):
    url = '/api/wizard/bluestacks/prepare'

    def bluestacks(w, conf=BLUESTACKS_CONF):
        folder = w.root / 'program-data' / 'BlueStacks_nxt'
        folder.mkdir(parents=True, exist_ok=True)
        if conf is not None:
            (folder / 'bluestacks.conf').write_text(conf, encoding='utf-8')
        return folder
    with emulator_world(gen, 'emb_prepare', manager=False) as w:
        bluestacks(w)
        got = act(w, 'prepare_changes_the_adb_switch_and_the_framebuffer', url, dict(index='Pie64'), expect=200).get_json()
        assert got['ok'] and got['changed'] and got['backup']
        again = act(w, 'prepare_again_changes_nothing', url, dict(index='Pie64'), expect=200).get_json()
        assert again['changed'] == {} and again['backup'] is None
    with emulator_world(gen, 'emb_prepare_refused', manager=False) as w:
        act(w, 'prepare_without_bluestacks_conf', url, dict(index='Pie64'), expect=400)
        bluestacks(w)
        act(w, 'prepare_a_bad_instance_key', url, dict(index='../x'), expect=400)
        act(w, 'prepare_an_unknown_instance', url, dict(index='Nougat'), expect=400)
        act(w, 'prepare_without_a_key', url, {}, expect=400)
        raw_post(w, ACT, 'prepare_body_not_json', url, 'index=Pie64', content_type='text/plain')
    with emulator_world(gen, 'emb_prepare_running', manager=False) as w:
        import psutil
        bluestacks(w)
        w.mp.setattr(psutil, 'process_iter', lambda attrs=None, ad_value=None: iter([SimpleNamespace(info={'name': 'HD-Player.exe', 'cmdline': []})]))
        refused = act(w, 'prepare_refused_while_bluestacks_is_running', url, dict(index='Pie64'), expect=409).get_json()
        assert 'close every BlueStacks window' in refused['error']


def run_emulator_actions_cases(gen):
    emulator_adopt_cases(gen)
    emulator_launch_cases(gen)
    emulator_bluestacks_cases(gen)

# -------------------------------------------------------------------- tools (M-03)
class InlineThreads:
    """`threading` for the dashboard module, with Thread replaced: the tools installer's worker is held (hold=True) or
    run at start() on the calling thread, so no download ever starts and the progress states are reproducible."""

    def __init__(self, hold=False):
        import threading
        self._threading, self.hold, self.pending = threading, hold, []

    def __getattr__(self, name):
        return getattr(self._threading, name)

    def Thread(self, target=None, daemon=None, **kwargs):      # noqa: N802 - the stdlib name
        outer = self

        class Worker:
            def start(self):
                (outer.pending.append if outer.hold else (lambda fn: fn()))(target)
        return Worker()


def emulator_tools_cases(gen):
    from device import tool_install
    url = '/api/wizard/tools/install'

    def fresh(w):
        w.dash._TOOLS_STATE.clear()
        w.dash._TOOLS_STATE.update(stage='idle', message='Connection tools have not been installed', percent=0)

    with gen.world('emt_license') as w:
        fresh(w)
        w.get(TOOLS, 'tools_idle_before_any_install', '/api/wizard/tools')
        assert w.post(TOOLS, 'install_without_the_license_400', url, {}).status_code == 400
        assert w.post(TOOLS, 'install_license_must_be_true_not_a_word', url, dict(accept_license='yes')).status_code == 400
        assert w.post(TOOLS, 'install_license_false_400', url, dict(accept_license=False)).status_code == 400
        raw_post(w, TOOLS, 'install_body_not_json', url, 'accept_license=true', content_type='text/plain')
        assert w.get(TOOLS, 'tools_still_idle_after_the_refusals', '/api/wizard/tools').get_json()['stage'] == 'idle'
    with gen.world('emt_started') as w:
        fresh(w)
        threads = InlineThreads(hold=True)
        w.mp.setattr(w.dash, 'threading', threads)
        started = w.post(TOOLS, 'install_accepted_202_the_download_starts', url, dict(accept_license=True))
        assert started.status_code == 202 and started.get_json() == {'ok': True, 'stage': 'downloading'} and len(threads.pending) == 1
        assert w.get(TOOLS, 'tools_downloading_at_zero', '/api/wizard/tools').get_json()['stage'] == 'downloading'
        again = w.post(TOOLS, 'install_again_while_downloading_is_answered_not_restarted', url, dict(accept_license=True))
        assert again.status_code == 200 and again.get_json()['ok'] is True and len(threads.pending) == 1

        def progressing(root, progress):
            progress('downloading', 'Downloaded 12 MB', 40)
        w.mp.setattr(tool_install, 'install', progressing)
        threads.pending.pop()()
        shown = w.get(TOOLS, 'tools_downloading_with_progress', '/api/wizard/tools').get_json()
        assert shown['stage'] == 'downloading' and shown['percent'] == 40 and shown['installed'] is False
    with gen.world('emt_done') as w:
        fresh(w)
        w.mp.setattr(w.dash, 'threading', InlineThreads())

        def installs(root, progress):
            progress('installing', 'Installing connection tools locally', 95)
            folder = Path(root) / 'tools' / 'platform-tools'
            folder.mkdir(parents=True, exist_ok=True)
            for filename in tool_install.FILES:
                (folder / filename).write_bytes(b'MZ')
            progress('ready', 'Connection tools installed', 100)
        w.mp.setattr(tool_install, 'install', installs)
        assert w.post(TOOLS, 'install_runs_to_the_end', url, dict(accept_license=True)).status_code == 202
        done = w.get(TOOLS, 'tools_installed_after_the_install', '/api/wizard/tools').get_json()
        assert done['stage'] == 'ready' and done['installed'] is True and done['path']
    with gen.world('emt_error') as w:
        fresh(w)
        w.mp.setattr(w.dash, 'threading', InlineThreads())
        w.mp.setattr(tool_install, 'install', raising(OSError('the download was interrupted')))
        assert w.post(TOOLS, 'install_failed_the_request_was_still_accepted', url, dict(accept_license=True)).status_code == 202
        failed = w.get(TOOLS, 'tools_error_state_after_a_failed_install', '/api/wizard/tools').get_json()
        assert failed['stage'] == 'error' and 'Check your internet connection' in failed['message'] and failed['installed'] is False
        assert w.post(TOOLS, 'install_can_be_retried_after_an_error', url, dict(accept_license=True)).status_code == 202
    with gen.world('emt_owner') as w:
        fresh(w)
        w.worker(4641, 'orchestrator.py', args=['--preset', 'bp_farm'])
        refused = w.post(TOOLS, 'install_refused_while_a_runner_owns_the_device', url, dict(accept_license=True))
        assert refused.status_code == 409 and refused.get_json()['code'] == 'device_owned'
    with gen.world('emt_lock') as w:
        fresh(w)
        assert w.dash._ACTION_LOCK.acquire(blocking=False)
        try:
            assert w.post(TOOLS, 'install_action_lock_another_action_in_progress', url, dict(accept_license=True)).get_json()['code'] == 'action_lock'
        finally:
            w.dash._ACTION_LOCK.release()


def run_emulator_tools_cases(gen):
    emulator_tools_cases(gen)


def run_emulator_cases(gen):
    emulator_inventory_cases(gen)
    run_emulator_actions_cases(gen)
    run_emulator_tools_cases(gen)