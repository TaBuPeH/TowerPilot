import io
import zipfile

import pytest
from device import tool_install


def archive(missing=None, payload=b"MZtest"):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name in tool_install.FILES:
            if name != missing:
                z.writestr("platform-tools/" + name, payload)
        z.writestr("../../outside.exe", b"bad")
    out.seek(0)
    return out


def test_extracts_only_required_files(tmp_path):
    dest = tmp_path / "tools" / "platform-tools"
    tool_install.unpack(archive(), dest)
    assert sorted(p.name for p in dest.iterdir()) == sorted(tool_install.FILES)
    assert tool_install.installed(tmp_path) == str(dest / "adb.exe")
    assert not (tmp_path / "outside.exe").exists()


def test_missing_dependency_is_not_ready(tmp_path):
    with pytest.raises(KeyError):
        tool_install.unpack(archive(missing="AdbWinUsbApi.dll"), tmp_path / "tools/platform-tools")
    assert tool_install.installed(tmp_path) is None


def test_rejects_non_windows_executable(tmp_path):
    with pytest.raises(ValueError, match="Windows"):
        tool_install.unpack(archive(payload=b"HTML error"), tmp_path)


def test_existing_install_does_not_download(tmp_path, monkeypatch):
    tool_install.unpack(archive(), tmp_path / "tools/platform-tools")
    monkeypatch.setattr(tool_install.urllib.request, "urlopen", lambda *a, **kw: pytest.fail("Unexpected download"))
    assert tool_install.install(tmp_path, lambda *a: None)


def test_cold_server_uses_managed_adb(tmp_path, monkeypatch):
    import socket
    from types import SimpleNamespace
    tool_install.unpack(archive(), tmp_path / "tools/platform-tools")
    def offline(*args, **kwargs):
        raise ConnectionRefusedError()
    monkeypatch.setattr(socket, "create_connection", offline)
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0)
    tool_install.ensure_server(tmp_path, "missing-adb.exe", run)
    assert calls == [([tool_install.installed(tmp_path), "start-server"],
                      {"capture_output": True, "timeout": 30})]


def test_live_server_is_never_replaced(tmp_path, monkeypatch):
    import socket
    from contextlib import nullcontext
    monkeypatch.setattr(socket, "create_connection", lambda *a, **kw: nullcontext())
    tool_install.ensure_server(tmp_path, None, lambda *a, **kw: pytest.fail("restarted server"))


def test_missing_tools_explains_setup(tmp_path, monkeypatch):
    import socket
    def offline(*args, **kwargs):
        raise ConnectionRefusedError()
    monkeypatch.setattr(socket, "create_connection", offline)
    with pytest.raises(RuntimeError, match="Setup"):
        tool_install.ensure_server(tmp_path, None, lambda *a, **kw: pytest.fail("spawn"))
