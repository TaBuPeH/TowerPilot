"""Starting the dashboard: the landing hold and the exclusive port.

The dashboard is the only supervisor of the gem collector, so exactly one may serve a port, and a tree that is
being landed must not be started half-written. Both rules decide BEFORE the dashboard starts any background
thread (refresher, harvest tick, reconnect), which `dashboard.serve` guarantees by calling them first.

* The hold: while `<log root>/_dashboard/HOLD` exists, a start is refused. The person (or the landing step)
  creates the file before copying runtime files and deletes it afterwards; the file's text, if any, is shown in
  the refusal. A forgotten file keeps every start refused - remove it by hand.
* The port: on Windows werkzeug's default bind sets SO_REUSEADDR, which lets a SECOND listener share a port that
  is already served (measured: two `make_server` calls on one port both succeed). The bind here uses
  SO_EXCLUSIVEADDRUSE instead, which also refuses a port an older dashboard bound the default way.
"""
import os
import socket
from pathlib import Path

from werkzeug.serving import ThreadedWSGIServer

HOLD_NAME = "HOLD"


class PortTaken(OSError):
    """Another process already serves (or has bound) the dashboard's port."""


def hold_path(log_root) -> Path:
    return Path(log_root) / "_dashboard" / HOLD_NAME


def hold_notice(log_root) -> str | None:
    """None when no hold is set, else the hold file's text (empty when it has none)."""
    try:
        return hold_path(log_root).read_text(encoding="utf-8", errors="replace").strip()
    except FileNotFoundError:
        return None


def bind(host: str, port: int, app) -> ThreadedWSGIServer:
    """A bound, listening threaded server for `app`, or PortTaken. Nothing is served until `serve_forever`."""
    failures = []

    class _ExclusiveServer(ThreadedWSGIServer):
        allow_reuse_address = os.name != "nt"

        def server_bind(self):
            if os.name == "nt":
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                super().server_bind()
            except OSError as exc:
                failures.append(exc)
                raise

    try:
        return _ExclusiveServer(host, port, app)
    except SystemExit:      # werkzeug prints the bind error to stderr and exits; report it as a value instead
        detail = failures[0] if failures else "bind failed"
        raise PortTaken(f"cannot bind {host}:{port}: {detail}") from (failures[0] if failures else None)
