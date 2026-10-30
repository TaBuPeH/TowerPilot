"""The new dashboard app (frontend/app, ADR-001) is served at /ui/app/ from its
git-ignored build in frontend/webui/app/. Without a build, every /ui/app/ URL
and / answer a page that says how to build it; with one, the files are served
from the build folder and nothing else, and / opens the app. /ui/index.html is a
static page that redirects old bookmarks onto the app (plan R17, option A). Links
are resolved: a junction or symlink inside the build, or a linked build folder,
never serves a file outside the real build folder. Flask test client against a
temporary webui folder - no server, no device, and the checkout's own build (if
any) is never read; the redirect checks read the checkout's real index.html."""

import paths as test_paths
import importlib.util
import json
import os
import re
import socket
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = test_paths.REPO
BUILD_HINT = b"cd frontend/app; npm ci; npm run build"
STATIC_PAGE = b"<p>static redirect page</p>"
SIBLING_SECRET = b"SIBLING-SECRET-OUTSIDE-WEBUI"


@pytest.fixture()
def dash(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    from device import adbclient

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    monkeypatch.setattr(adbclient, "_HOST", s.getsockname())   # never the real adb server
    s.close()
    spec = importlib.util.spec_from_file_location("tp_dashboard_ui_app", test_paths.DASHBOARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def webui(dash: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A stand-in frontend/webui/ with a top-level static page and no app build."""
    folder = tmp_path / "webui"
    folder.mkdir()
    (folder / "index.html").write_bytes(STATIC_PAGE)
    monkeypatch.setattr(dash.app, "static_folder", str(folder))
    (tmp_path / "dashboard.py").write_bytes(SIBLING_SECRET)    # what a traversal out of webui/ would reach
    return folder


def _build(webui: Path) -> Path:
    app = webui / "app"
    (app / "assets").mkdir(parents=True)
    (app / "index.html").write_text("<div id=root></div>", encoding="utf-8")
    (app / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (app / "assets" / "index-abc123.css").write_text("body{}", encoding="utf-8")
    return app


@pytest.mark.parametrize("path", ["/ui/app/", "/ui/app/index.html", "/ui/app/assets/index-abc123.js",
                                  "/ui/app/some/deep/route"])
def test_without_a_build_every_app_url_says_how_to_build_it(dash: ModuleType, webui: Path, path: str) -> None:
    with dash.app.test_client() as c:
        r = c.get(path)
    assert r.status_code == 404
    assert r.mimetype == "text/html"
    assert BUILD_HINT in r.data and b"not built" in r.data
    assert b"/ui/index.html" not in r.data            # no link to the retired dashboard
    assert r.headers.get("X-Frame-Options") == "DENY"


def test_a_folder_without_index_html_counts_as_not_built(dash: ModuleType, webui: Path) -> None:
    (webui / "app" / "assets").mkdir(parents=True)
    (webui / "app" / "assets" / "stale.js").write_text("old", encoding="utf-8")
    with dash.app.test_client() as c:
        r = c.get("/ui/app/assets/stale.js")
    assert r.status_code == 404 and BUILD_HINT in r.data


def test_with_a_build_the_files_are_served_from_it(dash: ModuleType, webui: Path) -> None:
    _build(webui)
    with dash.app.test_client() as c:
        root = c.get("/ui/app/")
        index = c.get("/ui/app/index.html")
        script = c.get("/ui/app/assets/index-abc123.js")
        style = c.get("/ui/app/assets/index-abc123.css")
        missing = c.get("/ui/app/assets/missing.js")
        bare = c.get("/ui/app")
        responses = (root, index, script, style, missing, bare)
        bodies = [r.data for r in responses]
        for r in responses:
            r.close()
    assert (root.status_code, bodies[0]) == (200, b"<div id=root></div>")
    assert (index.status_code, bodies[1]) == (200, b"<div id=root></div>")
    assert (script.status_code, bodies[2]) == (200, b"console.log(1)")
    assert "javascript" in script.mimetype
    assert (style.status_code, style.mimetype) == (200, "text/css")
    assert missing.status_code == 404 and BUILD_HINT not in bodies[4]
    assert bare.status_code == 308 and bare.headers["Location"].endswith("/ui/app/")
    assert root.headers.get("X-Frame-Options") == "DENY"


@pytest.mark.parametrize("path", ["/ui/app/..%2Findex.html", "/ui/app/..%2F..%2Fdashboard.py",
                                  "/ui/app/assets/..%2F..%2Findex.html", "/ui/app/..%5Cindex.html"])
def test_app_urls_never_leave_the_build_folder(dash: ModuleType, webui: Path, path: str) -> None:
    _build(webui)
    with dash.app.test_client() as c:
        r = c.get(path)
        body = r.data
        r.close()
    assert r.status_code == 404
    assert STATIC_PAGE not in body and b"import " not in body and SIBLING_SECRET not in body


def test_build_output_and_node_modules_are_git_ignored() -> None:
    text = (REPO / ".gitignore").read_text(encoding="utf-8")
    for entry in ("frontend/webui/app/", "frontend/app/node_modules/",
                  "frontend/app/test-results/", "frontend/app/playwright-report/"):
        assert entry in text.splitlines(), entry


# ------------------------------------------------- links and absolute paths
OUTSIDE = b"OUTSIDE-BUILD-CANARY"


@pytest.fixture()
def outside(tmp_path: Path) -> Path:
    """A folder next to webui/ holding a canary that must never be served."""
    folder = tmp_path / "outside"
    folder.mkdir()
    (folder / "secret.txt").write_bytes(OUTSIDE)
    (folder / "index.html").write_bytes(OUTSIDE)
    return folder


def _junction(link: Path, target: Path) -> None:
    """A real NTFS junction (no privilege needed), as `mklink /J` makes."""
    import _winapi
    _winapi.CreateJunction(str(target), str(link))
    assert os.path.isjunction(link) and (link / "secret.txt").exists() == (target / "secret.txt").exists()


def _get(dash: ModuleType, path: str) -> tuple[int, bytes, str | None]:
    with dash.app.test_client() as c:
        r = c.get(path)
        body = r.data
        frame = r.headers.get("X-Frame-Options")
        r.close()
    return r.status_code, body, frame


windows_only = pytest.mark.skipif(sys.platform != "win32", reason="NTFS junctions")


@windows_only
def test_a_junction_inside_the_build_cannot_reach_a_folder_outside_it(dash: ModuleType, webui: Path,
                                                                      outside: Path) -> None:
    # R3-06: before the fix this answered 200 with the canary
    _junction(_build(webui) / "junction", outside)
    status, body, frame = _get(dash, "/ui/app/junction/secret.txt")
    assert (status, OUTSIDE in body, frame) == (404, False, "DENY")


@windows_only
def test_a_junction_that_stays_inside_the_build_is_served(dash: ModuleType, webui: Path) -> None:
    # control: links are resolved, not refused wholesale
    build = _build(webui)
    _junction(build / "alias", build / "assets")
    assert _get(dash, "/ui/app/alias/index-abc123.js")[:2] == (200, b"console.log(1)")


@windows_only
def test_a_linked_build_folder_is_refused(dash: ModuleType, webui: Path, outside: Path) -> None:
    # webui/app itself is a junction to a folder that looks like a build
    _junction(webui / "app", outside)
    for path in ("/ui/app/", "/ui/app/index.html", "/ui/app/secret.txt"):
        status, body, _ = _get(dash, path)
        assert status == 404 and OUTSIDE not in body, path


def test_a_symlink_to_a_file_outside_the_build_is_refused(dash: ModuleType, webui: Path, outside: Path) -> None:
    build = _build(webui)
    try:
        (build / "linked.txt").symlink_to(outside / "secret.txt")
        (build / "linked-dir").symlink_to(outside, target_is_directory=True)
    except OSError as exc:                  # Windows without the symlink privilege
        pytest.skip(f"cannot create symlinks here: {exc}")
    for path in ("/ui/app/linked.txt", "/ui/app/linked-dir/secret.txt"):
        status, body, _ = _get(dash, path)
        assert status == 404 and OUTSIDE not in body, path


def test_absolute_drive_and_unc_paths_are_refused(dash: ModuleType, webui: Path, outside: Path) -> None:
    _build(webui)
    canary = (outside / "secret.txt").resolve().as_posix()          # a path outside the served app folder
    for path in ("/ui/app/" + canary, "/ui/app/" + canary.replace("/", "%5C"),
                 "/ui/app/C:/Windows/win.ini", "/ui/app/C:%5CWindows%5Cwin.ini",
                 "/ui/app/%5C%5Clocalhost%5Cshare%5Csecret.txt", "/ui/app/%2F%2Flocalhost/share/secret.txt",
                 "/ui/app/%5C%5C%3F%5C" + canary.replace("/", "%5C"), "/ui/app/assets/", "/ui/app/assets"):
        status, body, frame = _get(dash, path)
        assert status in (308, 404) and OUTSIDE not in body and frame == "DENY", path


# ------------------------------- every URL that reaches the build (R3-R2-01)
# Flask's static route /ui/<path> reaches the same folder as /ui/app/ under other
# spellings. Each must get the build's containment, not plain static serving.
ALTERNATE = ["/ui/App/junction/secret.txt", "/ui/APP/junction/secret.txt",
             "/ui/app//junction/secret.txt", "/ui/./app/junction/secret.txt",
             "/ui/%2e/app/junction/secret.txt", "/ui/app/%2e/junction/secret.txt",
             "/ui/app./junction/secret.txt", "/ui/app%20/junction/secret.txt",
             "/ui/app::$INDEX_ALLOCATION/junction/secret.txt", "/ui/app%5Cjunction%5Csecret.txt",
             "/ui/alias/junction/secret.txt", "/ui/alias/../app/junction/secret.txt"]


def _linked_build(webui: Path, outside: Path) -> Path:
    """A build holding a junction to the outside canary, and webui/alias, a junction to the build."""
    build = _build(webui)
    _junction(build / "junction", outside)
    _junction(webui / "alias", build)
    return build


@windows_only
@pytest.mark.parametrize("path", ALTERNATE)
def test_no_spelling_of_the_build_folder_escapes_it(dash: ModuleType, webui: Path, outside: Path,
                                                   path: str) -> None:
    _linked_build(webui, outside)
    status, body, frame = _get(dash, path)
    assert (status, OUTSIDE in body, frame) == (404, False, "DENY")


@windows_only
def test_the_static_guard_is_what_closes_the_other_spellings(dash: ModuleType, webui: Path,
                                                            outside: Path) -> None:
    # discriminating control: without the guard, the R3 round-2 URLs leak the canary
    _linked_build(webui, outside)
    dash.app.before_request_funcs[None].remove(dash.ui_app_static_guard)
    leaked = [path for path in ALTERNATE[:4] + ["/ui/alias/junction/secret.txt"]
              if _get(dash, path)[:2] == (200, OUTSIDE)]
    assert leaked == ALTERNATE[:4] + ["/ui/alias/junction/secret.txt"]


@windows_only
@pytest.mark.parametrize("path", ["/ui/App/index.html", "/ui/APP/assets/index-abc123.js",
                                  "/ui/app./index.html", "/ui/alias/assets/index-abc123.js",
                                  "/ui/App/", "/ui/alias/"])
def test_other_spellings_of_the_build_serve_its_own_files(dash: ModuleType, webui: Path, outside: Path,
                                                         path: str) -> None:
    _linked_build(webui, outside)
    status, body, frame = _get(dash, path)
    assert status == 200 and body in (b"<div id=root></div>", b"console.log(1)") and frame == "DENY"


INSIDE_WEBUI = b"OUTSIDE-BUILD-INSIDE-WEBUI"


@windows_only
def test_an_alias_to_a_folder_inside_the_build_keeps_build_containment(dash: ModuleType, webui: Path,
                                                                        outside: Path) -> None:
    # compliance M01: webui/asset-alias -> app/assets; app/assets/escape -> webui/legacy
    build = _build(webui)
    legacy = webui / "legacy"
    legacy.mkdir()
    (legacy / "secret.txt").write_bytes(INSIDE_WEBUI)
    _junction(build / "assets" / "escape", legacy)
    _junction(build / "assets" / "external", outside)
    _junction(webui / "asset-alias", build / "assets")
    for path in ("/ui/asset-alias/escape/secret.txt", "/ui/Asset-Alias/escape/secret.txt",
                 "/ui/app/assets/escape/secret.txt", "/ui/asset-alias/external/secret.txt"):
        status, body, frame = _get(dash, path)
        assert (status, INSIDE_WEBUI in body, OUTSIDE in body, frame) == (404, False, False, "DENY"), path
    # controls: the alias serves the build's own files; the legacy folder is plain static
    assert _get(dash, "/ui/asset-alias/index-abc123.js")[:2] == (200, b"console.log(1)")
    assert _get(dash, "/ui/legacy/secret.txt")[:2] == (200, INSIDE_WEBUI)


def test_dot_segments_before_the_build_serve_its_own_files(dash: ModuleType, webui: Path) -> None:
    _build(webui)
    assert _get(dash, "/ui/./app/index.html")[:2] == (200, b"<div id=root></div>")
    assert _get(dash, "/ui/%2e/app/assets/index-abc123.js")[:2] == (200, b"console.log(1)")


@windows_only
def test_a_plain_static_link_cannot_leave_webui(dash: ModuleType, webui: Path, outside: Path) -> None:
    _junction(webui / "linked", outside)
    (webui / "css").mkdir()
    (webui / "css" / "site.css").write_text("body{}", encoding="utf-8")
    for path in ("/ui/linked/secret.txt", "/ui/linked/index.html", "/ui/LINKED/secret.txt"):
        status, body, frame = _get(dash, path)
        assert (status, OUTSIDE in body, frame) == (404, False, "DENY"), path
    # controls: plain static files, nested ones included, are served as before
    assert _get(dash, "/ui/index.html")[:2] == (200, STATIC_PAGE)
    assert _get(dash, "/ui/css/site.css")[:2] == (200, b"body{}")


@pytest.mark.parametrize("path", ["/ui/C:/Windows/win.ini", "/ui/index.html::$DATA", "/ui/..%5Cdashboard.py",
                                  "/ui/%5C%5Clocalhost%5Cshare%5Csecret.txt", "/ui/.", "/ui/x/../index.html"])
def test_static_paths_with_drive_stream_backslash_or_parent_syntax_are_refused(
        dash: ModuleType, webui: Path, path: str) -> None:
    _build(webui)
    status, body, frame = _get(dash, path)
    assert status == 404 and STATIC_PAGE not in body and SIBLING_SECRET not in body and frame == "DENY", path


def test_only_the_static_route_and_ui_app_serve_files_under_ui(dash: ModuleType) -> None:
    rules = sorted((rule.rule, rule.endpoint) for rule in dash.app.url_map.iter_rules()
                   if rule.rule.startswith("/ui"))
    assert rules == [("/ui/<path:filename>", "static"), ("/ui/app/", "ui_app"),
                     ("/ui/app/<path:asset>", "ui_app")]
    assert dash.ui_app_static_guard in dash.app.before_request_funcs[None]

def test_the_home_page_opens_the_new_dashboard_once_it_is_built(dash: ModuleType, webui: Path) -> None:
    with dash.app.test_client() as c:
        before = c.get("/")
        _build(webui)
        after = c.get("/")
        redirect_page = c.get("/ui/index.html")
    assert before.status_code == 404 and before.mimetype == "text/html"
    assert BUILD_HINT in before.data and b"/ui/index.html" not in before.data
    assert before.headers.get("X-Frame-Options") == "DENY"
    assert after.status_code == 302 and after.headers["Location"].endswith("/ui/app/")
    assert redirect_page.status_code == 200 and redirect_page.data == STATIC_PAGE


# ------------------------------------------- /ui/index.html: the R17 redirect
REDIRECT = REPO / "frontend" / "webui" / "index.html"


def _redirect_aliases() -> dict[str, str]:
    html = REDIRECT.read_text(encoding="utf-8")
    block = re.search(r'<script id="aliases" type="application/json">(.*?)</script>', html, re.S)
    assert block is not None
    return json.loads(block.group(1))


def test_the_old_dashboard_address_is_a_redirect_with_no_legacy_page_left() -> None:
    """Option A (R17 brief decision 7): old bookmarks and the backend's
    calibration_url (/ui/index.html#calibrate) open the matching app page. The
    page carries no panels, stylesheets, script files or API calls."""
    html = REDIRECT.read_text(encoding="utf-8")
    assert "window.location.replace('/ui/app/'" in html
    assert '<a href="/ui/app/">' in html                          # works without JavaScript
    for absent in ("<link", " src=", "fetch(", "/api/", "<section", "<style", "style=", "classic.html"):
        assert absent not in html, absent
    assert len(html.splitlines()) < 40


def test_the_redirect_maps_the_old_anchors_onto_app_pages() -> None:
    assert _redirect_aliases() == {
        "overview": "dashboard", "home": "dashboard", "screens": "recognition",
        "calibrate": "recognition", "setup": "emulators", "settings": "runtime",
        "rewards": "collection"}
    pages = (REPO / "frontend" / "app" / "src" / "ui" / "routes.ts").read_text(encoding="utf-8")
    for target in set(_redirect_aliases().values()):
        assert f"'{target}'," in pages, target                     # every target is an app page


def test_the_dashboard_serves_the_real_redirect_page(
        dash: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The module is loaded by path and the static folder is NOT patched: the
    # default must be the checkout's real frontend/webui, whatever the working directory.
    monkeypatch.chdir(tmp_path)
    assert Path(dash.app.static_folder) == REDIRECT.parent and Path(dash.app.static_folder).is_absolute()
    assert Path(dash.ROOT) == test_paths.BACKEND and Path(dash.__file__) == test_paths.DASHBOARD
    with dash.app.test_client() as c:
        r = c.get("/ui/index.html")
        body = r.data
        r.close()
    assert r.status_code == 200 and r.mimetype == "text/html"
    assert body == REDIRECT.read_bytes() and r.headers.get("X-Frame-Options") == "DENY"
