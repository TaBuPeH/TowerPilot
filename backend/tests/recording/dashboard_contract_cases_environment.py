"""Contract cases of the runtime environment report and the OCR probe (item 10 p10b; Runtime and Setup pages).

Family runtime_environment: what GET /api/runtime publishes as `environment`, what GET /api/stream/backends says about adb and scrcpy,
and every answer of POST /api/environment/probe, each over a synthetic repository root. The existing runtime_state family records the
same `environment` once per sample over an EMPTY root; this family is the report state by state. It is a new family, appended, because the
hash of runtime_state is published.

Nothing of this machine is recorded or read. The facts the report takes from the host are pinned in dashboard_contract_cases_runtime
.pin_environment (operating system, interpreter, emulator locators, scrcpy server path and decoder, clock, stored probe result), and
everything the report reads under the repository root (platform-tools and their install receipt, the web app, the OCR script, the
release identity) is a file this scenario creates under <root>/install/. The `adb`, platform-tools and scrcpy versions in the answers are
the pins of the code (device.tool_install, device.preview.scrcpy_package), not the version of anything installed here.

What the routes do (read from runtime/environment.py and backend/dashboard.py, then recorded):
  * a report never spawns, downloads, contacts adb or opens a device; it states which local prerequisites exist. `available` does not
    mean an emulator is connected or a capture works. A locator that finds nothing is `unknown`: another install may exist.
  * the platform-tools version comes only from an install receipt that matches the pin; a missing, unreadable or different receipt leaves
    it null and says so in `basis`. A tools folder that cannot be read is `unknown`, not `missing`.
  * only POST /api/environment/probe with exactly {"probe": "windows_ocr"} runs the probe (Windows only), under the dashboard's global
    action lock, with synthetic pixels; success needs the expected text on a recognized line, exit 0 and an empty stderr. The result is
    kept in process memory (state available | missing | timeout, checked_at, basis, detail) and is `unknown`/`not probed` until then.
"""
import contextlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from dashboard_contract_cases_runtime import pin_build, pin_environment, source_tree
from dashboard_contract_pages_world import raw_post
from device import tool_install
from device.preview import scrcpy_package
from runtime import environment

RE = 'runtime_environment'
RUNTIME, BACKENDS, PROBE = '/api/runtime', '/api/stream/backends', '/api/environment/probe'
REQUEST = {'probe': 'windows_ocr'}


@contextlib.contextmanager
def environment_world(gen, name, **pins):
    """A world whose repository root (`Path(ROOT).parent`, which is what the report inspects) is its own folder <root>/install."""
    with gen.world(name) as w:
        install = w.root / 'install'
        (install / 'backend').mkdir(parents=True)
        w.mp.setattr(w.dash, 'ROOT', str(install / 'backend'))
        pin_build(w, source_tree(w))
        if pins:
            pin_environment(w, **pins)
        yield w, install


def put(path, data=b'synthetic'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode('utf-8'))
    return path


def platform_tools(install, receipt='matching'):
    folder = install / 'backend' / 'tools' / 'platform-tools'
    for name in tool_install.FILES:
        put(folder / name, b'synthetic executable')
    pin = dict(version=tool_install.VERSION, sha256=tool_install.SHA256, url=tool_install.URL)
    if receipt == 'matching':
        put(folder / 'source.json', json.dumps(pin))
    elif receipt == 'other_version':
        put(folder / 'source.json', json.dumps(dict(pin, version='0.0.1')))
    elif receipt == 'unreadable':
        put(folder / 'source.json', '{broken')
    return folder


def scrcpy_server(w):
    """A server file of the world whose digest is the pin the report checks."""
    import hashlib
    server = put(w.root.parent / f'{w.name}-scrcpy' / 'scrcpy-server', b'synthetic scrcpy server')
    w.mp.setattr(scrcpy_package, 'SERVER_SHA256', hashlib.sha256(server.read_bytes()).hexdigest())


def portable_python(w):
    put(Path(environment.sys.executable).with_name('python312._pth'), 'Lib/site-packages')


def runtime(w, case):
    got = w.get(RE, case, RUNTIME)
    assert got.status_code == 200, (case, got.status_code)
    return got.get_json()['environment']


def backends(w, case, status=200):
    got = w.get(RE, case, BACKENDS)
    assert got.status_code == status, (case, got.status_code)
    return got.get_json()


class OcrHost:
    """The dashboard's hidden-process boundary (`_run`) as the probe sees it. It checks what the probe hands over (the shipped script, the
    bounded timeout, a real synthetic 900x200 image) and answers as scripted; an answer that raises is how a timeout or a missing host is made."""

    def __init__(self, w, *, code=0, stdout=b'20\t20\tHELLO\r\n', stderr=b'', error=None):
        self.w, self.code, self.stdout, self.stderr, self.error, self.calls = w, code, stdout, stderr, error, []

    def __call__(self, args, **kwargs):
        script = Path(self.w.dash.ROOT).parent / 'backend' / 'tools' / 'winocr.ps1'
        assert args[:6] == ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script)], args
        assert kwargs['capture_output'] is True and 0 < kwargs['timeout'] <= environment.OCR_TIMEOUT, kwargs
        with Image.open(args[-1]) as image:
            assert image.size == (900, 200)
        self.calls.append(args)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(returncode=self.code, stdout=self.stdout, stderr=self.stderr)


def probe(w, case, host, status=200, body=REQUEST):
    w.mp.setattr(w.dash, '_run', host)
    before = len(host.calls)
    got = w.post(RE, case, PROBE, body)
    assert got.status_code == status, (case, got.status_code, got.get_json())
    return got.get_json(), len(host.calls) - before


def runtime_environment_cases(gen):
    # ------------------------------------------------------------------ a source checkout
    with environment_world(gen, 're_source') as (w, install):
        env = runtime(w, 'source_checkout_nothing_installed')
        assert env['os'] == dict(system='Windows', release='11', architecture='AMD64') and env['python'] == dict(version='3.12.0', kind='source')
        assert env['release'] == dict(kind='checkout') and env['platform_tools']['state'] == 'missing' and env['platform_tools']['version'] is None
        assert (env['emulators']['mumu']['state'], env['emulators']['bluestacks']['state']) == ('available', 'unknown')
        assert env['scrcpy_server']['state'] == 'missing' and env['scrcpy_decoder']['state'] == 'available'
        assert env['app_build']['state'] == env['windows_ocr_script']['state'] == 'missing'
        assert env['windows_ocr'] == dict(state='unknown', basis='not probed', checked_at=None, detail='not probed')
        got = backends(w, 'stream_backends_nothing_installed_adb_missing')
        assert got['ok'] is True and got['instance'] == 'main' and got['adb']['available'] is False and got['adb']['state'] == 'missing'
        assert got['mumu']['available'] is False and got['scrcpy']['installed'] is False and got['scrcpy']['decoder_available'] is True
    # ------------------------------------------------- a source checkout with every prerequisite
    with environment_world(gen, 're_complete', bluestacks=True) as (w, install):
        platform_tools(install)
        put(install / 'frontend' / 'webui' / 'app' / 'index.html', '<!doctype html>')
        put(install / 'backend' / 'tools' / 'winocr.ps1', '# synthetic script')
        put(install / 'release_identity.json', json.dumps({'kind': 'source'}))
        scrcpy_server(w)
        env = runtime(w, 'source_release_every_prerequisite_present')
        assert env['release'] == dict(kind='source') and env['platform_tools']['version'] == tool_install.VERSION
        assert env['scrcpy_server']['state'] == 'available' and env['stream_backends']['scrcpy']['state'] == 'available'
        assert env['app_build']['state'] == env['windows_ocr_script']['state'] == 'available'
        assert env['windows_ocr']['state'] == 'unknown'    # a script is not a working engine: only the probe says so
        assert all(row['state'] == 'available' for row in env['emulators'].values())
        got = backends(w, 'stream_backends_adb_from_the_local_tools_and_scrcpy_ready')
        assert got['adb']['available'] is True and got['scrcpy']['available'] is True and 'device connection not probed' in got['adb']['basis']
    # ----------------------------------------------------------------------- a portable release
    with environment_world(gen, 're_portable') as (w, install):
        portable_python(w)
        put(install / 'release_identity.json', json.dumps({'kind': 'portable-windows-x64'}))
        scrcpy_server(w)
        w.mp.setattr(scrcpy_package, 'importlib', SimpleNamespace(util=SimpleNamespace(find_spec=lambda name: None)))
        env = runtime(w, 'portable_release_scrcpy_server_present_decoder_missing')
        assert env['python']['kind'] == 'portable' and env['release'] == dict(kind='portable-windows-x64')
        assert env['scrcpy_server']['state'] == 'available' and env['scrcpy_decoder']['state'] == env['stream_backends']['scrcpy']['state'] == 'missing'
    with environment_world(gen, 're_identity') as (w, install):
        put(install / 'release_identity.json', '{broken')
        assert runtime(w, 'release_identity_that_cannot_be_read_is_unknown')['release'] == dict(kind='unknown')
        put(install / 'release_identity.json', json.dumps({'kind': 'something-else'}))
        assert runtime(w, 'release_identity_of_an_unrecognized_kind_is_unknown')['release'] == dict(kind='unknown')
    # ------------------------------------------------------ platform-tools and the adb prerequisite
    with environment_world(gen, 're_adb') as (w, install):
        platform_tools(install, receipt=None)
        env = runtime(w, 'platform_tools_installed_without_a_receipt_version_unknown')
        assert env['platform_tools']['state'] == 'available' and env['platform_tools']['version'] is None and 'no readable receipt' in env['platform_tools']['basis']
        platform_tools(install, receipt='other_version')
        env = runtime(w, 'platform_tools_receipt_does_not_match_the_pin_version_unknown')
        assert env['platform_tools']['version'] is None and 'does not match pin' in env['platform_tools']['basis']
        platform_tools(install, receipt='matching')
        assert runtime(w, 'platform_tools_with_the_matching_receipt')['platform_tools']['version'] == tool_install.VERSION
        real_installed = tool_install.installed

        def denied(root):
            raise PermissionError('the install folder is locked')
        w.mp.setattr(tool_install, 'installed', denied)
        env = runtime(w, 'platform_tools_folder_cannot_be_read_state_unknown')
        assert env['platform_tools']['state'] == 'unknown' and env['stream_backends']['adb']['available'] is False
        got = backends(w, 'stream_backends_install_unreadable_adb_unknown')
        assert got['adb']['state'] == 'unknown' and got['adb']['available'] is False
        w.mp.setattr(tool_install, 'installed', real_installed)
        vendor = put(w.root / 'vendor' / 'adb.exe', b'synthetic vendor adb')
        w.edit_config(lambda cfg: cfg.setdefault('adb', {}).__setitem__('exe', str(vendor)))
        got = backends(w, 'stream_backends_configured_adb_executable_found')
        assert got['adb']['available'] is True and got['adb']['basis'].startswith('configured adb executable found locally')
        vendor.unlink()
        w.edit_config(lambda cfg: cfg.setdefault('adb', {}).__setitem__('exe', str(vendor)))
        got = backends(w, 'stream_backends_configured_adb_executable_gone_falls_back_to_the_local_tools')
        assert got['adb']['available'] is True and 'tool_install.installed' in got['adb']['basis']
        for name in tool_install.FILES:
            (install / 'backend' / 'tools' / 'platform-tools' / name).unlink()
        got = backends(w, 'stream_backends_configured_adb_executable_gone_and_no_local_tools')
        assert got['adb']['available'] is False and got['adb']['state'] == 'missing'
        w.edit_config(lambda cfg: cfg['instances']['main'].__setitem__('preview', {'backend': 'invalid'}))
        refused = backends(w, 'stream_backends_invalid_preview_setting_is_refused', 400)
        assert refused['ok'] is False and 'preview.backend' in refused['error']
    # ---------------------------------------------------- emulator locators and another operating system
    with environment_world(gen, 're_locators', mumu=False, bluestacks=True) as (w, install):
        env = runtime(w, 'emulator_locators_find_bluestacks_not_mumu')
        assert (env['emulators']['mumu']['state'], env['emulators']['bluestacks']['state']) == ('unknown', 'available')
    with environment_world(gen, 're_other_os', system='Linux') as (w, install):
        env = runtime(w, 'other_operating_system_is_reported_as_it_is')
        assert env['os']['system'] == 'Linux'
    # --------------------------------------------------------------------------- the OCR probe
    with environment_world(gen, 're_probe') as (w, install):
        put(install / 'backend' / 'tools' / 'winocr.ps1', '# synthetic script')
        idle = OcrHost(w)
        w.mp.setattr(w.dash, '_run', idle)
        env = runtime(w, 'ocr_script_present_but_never_probed_state_unknown')
        assert env['windows_ocr_script']['state'] == 'available' and env['windows_ocr']['state'] == 'unknown'
        assert w.client.get(PROBE).status_code == 405 and idle.calls == []        # a GET never probes (and is not a recorded case: Werkzeug's own page)
        for case, body in (('probe_without_a_body', None), ('probe_with_an_array', []), ('probe_with_an_empty_object', {}),
                           ('probe_with_another_probe_name', {'probe': 'adb'}), ('probe_with_an_extra_key', {'probe': 'windows_ocr', 'force': True})):
            refused, calls = probe(w, case, idle, 400, body)
            assert refused['ok'] is False and 'windows_ocr' in refused['error'] and calls == 0, case
        for case, data in (('probe_with_json_null', 'null'), ('probe_with_text_that_is_not_json', '{broken')):
            got = raw_post(w, RE, case, PROBE, data)
            assert got.status_code == 400 and got.get_json()['ok'] is False and idle.calls == [], case
        assert w.dash._ACTION_LOCK.acquire(blocking=False)
        try:
            refused, calls = probe(w, 'probe_refused_while_the_action_lock_is_held', idle, 409)
            assert refused['code'] == 'action_lock' and calls == 0
        finally:
            w.dash._ACTION_LOCK.release()
        env = runtime(w, 'read_after_the_refused_probe_still_unknown')
        assert env['windows_ocr']['state'] == 'unknown' and idle.calls == []
        done, calls = probe(w, 'probe_recognizes_the_synthetic_text', OcrHost(w))
        assert calls == 1 and done['ok'] is True and done['windows_ocr']['state'] == 'available' and done['windows_ocr']['checked_at'] == w.clock.now
        env = runtime(w, 'read_after_the_successful_probe')
        assert env['windows_ocr'] == done['windows_ocr']
        assert not w.dash._ACTION_LOCK.locked()
    with environment_world(gen, 're_probe_fails') as (w, install):
        script = put(install / 'backend' / 'tools' / 'winocr.ps1', '# synthetic script')
        script.unlink()
        done, calls = probe(w, 'probe_when_the_ocr_script_is_missing', OcrHost(w))
        assert calls == 0 and done['windows_ocr']['state'] == 'missing' and 'script is missing' in done['windows_ocr']['detail']
        assert runtime(w, 'read_after_the_missing_script_probe')['windows_ocr'] == done['windows_ocr']
        put(install / 'backend' / 'tools' / 'winocr.ps1', '# synthetic script')
        for case, host, expected in (
                ('probe_exit_zero_but_the_text_was_not_recognized', OcrHost(w, stdout=b'20\t20\tsomething else\r\n'), 'missing'),
                ('probe_nonzero_exit', OcrHost(w, code=1), 'missing'),
                ('probe_stderr_output_is_not_success', OcrHost(w, stderr=b'engine error'), 'missing'),
                ('probe_exceeds_the_ten_second_budget', OcrHost(w, error=subprocess.TimeoutExpired('powershell.exe', environment.OCR_TIMEOUT)), 'timeout'),
                ('probe_cannot_start_the_script_host', OcrHost(w, error=FileNotFoundError('powershell.exe')), 'missing')):
            done, calls = probe(w, case, host)
            assert calls == 1 and done['windows_ocr']['state'] == expected, case
        assert runtime(w, 'read_after_the_last_failed_probe')['windows_ocr']['detail'].startswith('Windows OCR probe failed')
        ticks = iter([0.0, environment.OCR_TIMEOUT + 1.0])
        w.mp.setattr(environment, 'time', SimpleNamespace(time=lambda: w.clock.now, monotonic=lambda: next(ticks)))
        done, calls = probe(w, 'probe_preparation_used_the_whole_budget_nothing_is_run', OcrHost(w))
        assert calls == 0 and done['windows_ocr']['state'] == 'timeout'
        w.mp.setattr(environment, 'time', w.clock)
        done, calls = probe(w, 'probe_succeeds_after_the_failures_and_replaces_the_stored_result', OcrHost(w))
        assert calls == 1 and done['windows_ocr']['state'] == 'available'
        assert runtime(w, 'read_after_the_probe_that_replaced_the_failure')['windows_ocr'] == done['windows_ocr']
    with environment_world(gen, 're_probe_os', system='Linux') as (w, install):
        put(install / 'backend' / 'tools' / 'winocr.ps1', '# synthetic script')
        done, calls = probe(w, 'probe_on_another_operating_system_needs_windows', OcrHost(w))
        assert calls == 0 and done['windows_ocr']['state'] == 'missing' and done['windows_ocr']['detail'] == 'Windows OCR requires Windows'