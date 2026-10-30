"""Contract cases of the BlueStacks inventory and the Prepare answers (Emulators page).

Family emulator_bluestacks_inventory: GET /api/wizard/emulators over a bluestacks.conf with TWO instances, one stopped and
one running, and POST /api/wizard/bluestacks/prepare for each. The existing emulator_inventory family has no BlueStacks row (its
world is MuMu's), so these are a new family, appended, rather than cases added to one whose hash is published.

Everything is the real route over temporary install folders and an in-memory process list (the emulator_world of
test_dashboard_contract.py); no bluestacks.conf, HD-Player or adb of this machine is read or touched.

What the routes do (read from the source, then recorded):
  * the inventory lists every `bst.instance.<key>.display_name` of bluestacks.conf; `running` is whether an HD-Player.exe carries
    `--instance <key>` on its command line; `adb_enabled` is the GLOBAL `bst.enable_adb_access` switch, so every instance of one
    file carries the same value; `display_ok` is the instance's own framebuffer being 2560x1080 at 360 dpi.
  * Prepare refuses while ANY HD-Player.exe runs (409), whichever instance is named: a running instance's Prepare AND the stopped
    instance's Prepare are both refused until BlueStacks is closed. Once nothing runs, Prepare writes the global ADB switch and the
    named instance's framebuffer; an instance already at 2560x1080@360 with ADB on is "already prepared" (nothing changed, no backup).
"""
from types import SimpleNamespace

from test_dashboard_contract import emulator_world

INV = 'emulator_bluestacks_inventory'
URL = '/api/wizard/bluestacks/prepare'
CONF = ('bst.enable_adb_access="0"\n'
        'bst.instance.Pie64.display_name="Pie64"\nbst.instance.Pie64.adb_port="5555"\nbst.instance.Pie64.fb_width="1080"\n'
        'bst.instance.Pie64.fb_height="2560"\nbst.instance.Pie64.dpi="240"\n'
        'bst.instance.Nougat64.display_name="Nougat64"\nbst.instance.Nougat64.adb_port="5565"\nbst.instance.Nougat64.fb_width="2560"\n'
        'bst.instance.Nougat64.fb_height="1080"\nbst.instance.Nougat64.dpi="360"\nbst.instance.Nougat64.custom_resolution_selected="1"\n')
PLAYER = {'name': 'HD-Player.exe', 'cmdline': ['C:\\Program Files\\BlueStacks_nxt\\HD-Player.exe', '--instance', 'Nougat64']}


def bluestacks_conf(w):
    folder = w.root / 'program-data' / 'BlueStacks_nxt'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'bluestacks.conf').write_text(CONF, encoding='utf-8')


def processes(w, rows):
    import psutil
    w.mp.setattr(psutil, 'process_iter', lambda attrs=None, ad_value=None: iter([SimpleNamespace(info=dict(row)) for row in rows]))


def prepare(w, case, index, expect):
    reply = w.post(INV, case, URL, dict(index=index))
    assert reply.status_code == expect, (case, reply.status_code, reply.get_json())
    return reply


def rows_of(got):
    return {row['index']: row for row in got['instances'] if row.get('emulator') == 'BlueStacks'}


def emulator_bluestacks_inventory_cases(gen):
    with emulator_world(gen, 'ebi_two', manager=False) as w:
        bluestacks_conf(w)
        w.adb.state, w.adb.listing = 'missing', []
        processes(w, [PLAYER])
        got = w.get(INV, 'one_stopped_one_running_instance_adb_off_and_a_display_that_is_not_ok', '/api/wizard/emulators').get_json()
        rows = rows_of(got)
        stopped, running = rows['Pie64'], rows['Nougat64']
        assert (stopped['running'], stopped['adb_enabled'], stopped['display_ok'], stopped['display']) == (False, False, False, '1080x2560@240')
        assert (running['running'], running['adb_enabled'], running['display_ok'], running['display']) == (True, False, True, '2560x1080@360')
        refused = prepare(w, 'prepare_the_running_instance_is_refused', 'Nougat64', 409).get_json()
        assert 'close every BlueStacks window' in refused['error']
        other = prepare(w, 'prepare_the_stopped_instance_is_refused_too_while_another_instance_runs', 'Pie64', 409).get_json()
        assert other['error'] == refused['error']
        w.get(INV, 'read_after_the_refusals_nothing_changed', '/api/wizard/emulators')
        processes(w, [])                                              # BlueStacks is closed
        done = prepare(w, 'prepare_the_stopped_instance_once_nothing_runs', 'Pie64', 200).get_json()
        assert done['ok'] and set(done['changed']) == {'bst.enable_adb_access', 'bst.instance.Pie64.fb_width', 'bst.instance.Pie64.fb_height',
                                                       'bst.instance.Pie64.dpi'} and done['backup']
        assert done['added'] == ['bst.instance.Pie64.custom_resolution_selected']       # the key the file did not have yet
        after = w.get(INV, 'read_after_the_prepare_display_ok_and_adb_on', '/api/wizard/emulators').get_json()
        assert all(row['display_ok'] and row['adb_enabled'] and not row['running'] for row in rows_of(after).values())
        again = prepare(w, 'prepare_the_other_instance_is_already_prepared', 'Nougat64', 200).get_json()
        assert again['changed'] == {} and again['added'] == [] and again['backup'] is None