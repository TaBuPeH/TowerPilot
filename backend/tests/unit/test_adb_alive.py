"""adbclient.alive(): success of `exec:true` on the device transport IS the
answer. `true` prints nothing, so the old `b"device" in <stdout>` test was
always False and only its trailing `or True` made a live device read alive;
boot.py's adb stage (reconnect() and alive()) depends on that True."""

import socket
import threading

from device import adbclient

SERIAL = "127.0.0.1:16384"


def _fake_server(monkeypatch, replies):
    """A one-shot local adb server: reads one request per reply, records the
    payloads, answers each with its reply, then closes (end of stdout)."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    got = []

    def serve():
        conn, _ = srv.accept()
        with conn:
            for reply in replies:
                n = int(conn.recv(4), 16)
                got.append(conn.recv(n).decode())
                conn.sendall(reply)
        srv.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    monkeypatch.setattr(adbclient, "_HOST", srv.getsockname())
    return got, t


def test_a_device_that_runs_true_is_alive(monkeypatch):
    got, t = _fake_server(monkeypatch, [b"OKAY", b"OKAY"])
    assert adbclient.alive(SERIAL) is True
    t.join(2)
    assert got == [f"host:transport:{SERIAL}", "exec:true"]


def test_an_offline_device_is_not_alive(monkeypatch):
    msg = b"device offline"
    _fake_server(monkeypatch, [b"FAIL" + b"%04x" % len(msg) + msg])
    assert adbclient.alive(SERIAL) is False


def test_no_server_or_no_serial_is_not_alive(monkeypatch):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens here now
    monkeypatch.setattr(adbclient, "_HOST", ("127.0.0.1", port))
    assert adbclient.alive(SERIAL) is False
    assert adbclient.alive("") is False
