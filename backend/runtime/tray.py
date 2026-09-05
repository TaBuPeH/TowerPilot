"""Windows tray supervisor for the Tower autopilot.

One icon in the notification area. Menu:

    Main Tower-1  >  * High Tier          <- radio: the RUNNING preset
                     Low Tier
                     Tournament (undefined)
                     ...
                     Disabled
                     ---
                     wave 2837 - 12m
    TEST-1        >  ...
    ---
    Open logs folder
    Quit

Design (settled with the user):
  - ONE orchestrator PROCESS per instance. The orchestrator keeps module-level globals (tap
    rate cap, layout offset, log file) that assume a single screen, so process
    isolation - not threads - is what makes multi-instance safe. A crash or a
    hung adb call on one account cannot stall the others.
  - Presets are MUTUALLY EXCLUSIVE per instance, so the menu is a radio group
    and the checked item IS what is running. "Disabled" means no process.
  - Switching preset = kill the process, spawn a new one. No live reload. The
    orchestrator tolerates an abrupt kill: on restart the battle-presence gate blocks
    all clicking until it can read the wave counter, and bail() returns to the
    battle if it wakes up on a menu.
  - The menu is generated from config.yaml, so adding an instance or a preset
    there makes it appear here with no code change.
"""
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil
import pystray
from PIL import Image

import sys as _sys
from pathlib import Path as _Path
# Runnable as a script from the backend root (`python runtime/tray.py`):
# put that root on sys.path so package imports resolve.
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

from settings import CONFIG, ROOT, preset_menu

PY = sys.executable
PYW = str(Path(PY).with_name("pythonw.exe"))     # children: no console window
DEFAULT_RUNNER = "orchestrator.py"


def preset_runner(preset: str) -> tuple[str, list[str]]:
    """(script, extra args) for a preset - not every preset is the orchestrator.

    Shard farming is its own script with its own loop, so a preset may name a
    `runner:` and `runner_args:`. Everything without one is a orchestrator preset,
    which is all of them historically."""
    body = (CONFIG["presets"].get(preset) or {})
    return (body.get("runner") or DEFAULT_RUNNER,
            [str(a) for a in (body.get("runner_args") or [])])


def known_runners() -> set[str]:
    """Every script the tray may own, for orphan matching - as BASENAMES,
    because the matcher compares Path(cmd[1]).name and a config runner may
    be a path (`flows/shard.py`). Derived from the config rather than
    hardcoded, so adding a preset with a new runner does not silently create
    orphans this tray refuses to reclaim."""
    out = {DEFAULT_RUNNER}
    for body in CONFIG["presets"].values():
        r = (body or {}).get("runner")
        if r:
            out.add(Path(r).name)
    return out
ICON = ROOT / "assets" / "tower.png"
LOG_DIR = ROOT / CONFIG["logging"]["dir"]
TRAY_LOG = LOG_DIR / "tray.log"
POLL_SEC = 3.0                     # supervisor tick: reap + refresh menu
RUNLOG_GRACE_SEC = 12.0            # do not kill mid death-stats collection


def log(msg: str) -> None:
    """Supervisor's own log. The shortcut launches this under pythonw.exe,
    where stdout goes NOWHERE - without a file log a startup crash would be
    completely invisible ('the icon just never appeared')."""
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    try:
        LOG_DIR.mkdir(exist_ok=True)
        with TRAY_LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    print(line, flush=True)


class Instance:
    """Supervises one emulator's orchestrator process."""

    def __init__(self, key: str, cfg: dict):
        self.key = key
        self.label = cfg.get("label") or key
        self.proc: subprocess.Popen | None = None
        self.preset: str | None = None      # preset of the RUNNING process
        self.started = 0.0
        self.last_exit: int | None = None

    # ---- process control -------------------------------------------------
    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def orphans(self) -> list[psutil.Process]:
        """Orchestrator processes driving THIS instance that we do not own.

        Children survive their parent on Windows, so a killed/crashed tray
        leaves orchestrators running. Without this check the next launch would spawn
        a SECOND orchestrator on the same emulator and both would tap it - the worst
        failure mode this app can have. Identified by command line, which
        needs no pid file to go stale."""
        out = []
        mine = self.proc.pid if self.proc is not None else None
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            cmd = [str(c) for c in (p.info.get("cmdline") or [])]
            if len(cmd) < 4 or p.info["pid"] == mine:
                continue
            # STRICT token matching, never substring-on-the-whole-line: a
            # shell or editor whose command line merely mentions
            # "orchestrator.py --instance main" must never be terminated by us.
            if not (p.info.get("name") or "").lower().startswith("python"):
                continue
            if Path(cmd[1]).name not in known_runners():
                continue
            if "--instance" not in cmd:
                continue
            i = cmd.index("--instance")
            if i + 1 < len(cmd) and cmd[i + 1] == self.key:
                out.append(p)
        return out

    def kill_orphans(self) -> int:
        found = self.orphans()
        for p in found:
            try:
                for kid in p.children(recursive=True):
                    kid.terminate()
                p.terminate()
                p.wait(timeout=5)
            except psutil.TimeoutExpired:
                try:
                    p.kill()
                except psutil.Error:
                    pass
            except psutil.Error:
                pass
        return len(found)

    def start(self, preset: str):
        self.stop()
        self.kill_orphans()          # guarantee single ownership
        creation = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        exe = PYW if Path(PYW).exists() else PY
        script, extra = preset_runner(preset)
        self.proc = subprocess.Popen(
            [exe, str(ROOT / script), "--instance", self.key,
             "--preset", preset, *extra],
            cwd=str(ROOT), creationflags=creation,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.preset = preset
        self.started = time.monotonic()
        self.last_exit = None
        log(f"start {self.key} preset={preset} runner={script} "
            f"pid={self.proc.pid}")

    def stop(self):
        """Terminate the orchestrator and every child it spawned (adb shells).

        Waits out a death-stats collection first: killing between the death
        and the RETRY tap loses that run's run.md, a ~12s window."""
        if not self.running:
            self.proc, self.preset = None, None
            return
        if self._collecting_runlog():
            for _ in range(int(RUNLOG_GRACE_SEC)):
                time.sleep(1.0)
                if not self._collecting_runlog() or not self.running:
                    break
        try:
            parent = psutil.Process(self.proc.pid)
            kids = parent.children(recursive=True)
            for p in kids:
                try:
                    p.terminate()
                except psutil.Error:
                    pass
            parent.terminate()
            try:
                parent.wait(timeout=5)
            except psutil.TimeoutExpired:
                parent.kill()
        except psutil.Error:
            pass
        log(f"stop {self.key} (was preset={self.preset})")
        self.proc, self.preset = None, None

    def _collecting_runlog(self) -> bool:
        """True when the last event was a death with no retry yet."""
        ev = self.last_events(4)
        for rec in reversed(ev):
            if '"kind": "retry"' in rec:
                return False
            if '"kind": "death"' in rec:
                return True
        return False

    # ---- status ----------------------------------------------------------
    def log_dir(self) -> Path:
        return LOG_DIR / self.key

    def last_events(self, n: int = 1) -> list[str]:
        d = self.log_dir()
        if not d.is_dir():
            return []
        files = sorted(d.glob("events_*.jsonl"))
        if not files:
            return []
        try:
            lines = files[-1].read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        return lines[-n:]

    def status(self) -> str:
        if not self.running:
            if self.last_exit not in (None, 0):
                return f"stopped (exit {self.last_exit})"
            return "disabled"
        wave, kind = None, None
        for rec in reversed(self.last_events(40)):
            if wave is None and '"wave":' in rec:
                try:
                    seg = rec.split('"wave":', 1)[1].split(",", 1)[0]
                    wave = seg.strip().strip("}").strip()
                except IndexError:
                    pass
            if kind is None and '"kind":' in rec:
                kind = rec.split('"kind": "', 1)[-1].split('"', 1)[0]
            if wave and kind:
                break
        up = int(time.monotonic() - self.started)
        mins = f"{up // 60}m" if up >= 60 else f"{up}s"
        bits = [f"up {mins}"]
        if wave and wave != "null":
            bits.insert(0, f"wave {wave}")
        if kind:
            bits.append(kind)
        return " - ".join(bits)


class Supervisor:
    def __init__(self):
        self.instances = {k: Instance(k, v)
                          for k, v in CONFIG["instances"].items()}
        self.presets = preset_menu()
        self.icon = pystray.Icon(
            "tower_autopilot", Image.open(ICON), "Tower Autopilot",
            menu=self._menu())
        self._stop = threading.Event()

    # ---- menu ------------------------------------------------------------
    def _menu(self) -> pystray.Menu:
        items = []
        for inst in self.instances.values():
            items.append(pystray.MenuItem(inst.label, self._instance_menu(inst)))
        items += [
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Stop all", self._stop_all),
            pystray.MenuItem("Open logs folder", self._open_logs),
            pystray.MenuItem("Quit", self._quit),
        ]
        return pystray.Menu(*items)

    def _instance_menu(self, inst: "Instance") -> pystray.Menu:
        """Rebuilt from callables on every open, so checkmarks and the status
        line always reflect reality rather than the last click."""
        items = []
        for key, label, defined in self.presets:
            text = label if defined else f"{label}  (undefined)"
            items.append(pystray.MenuItem(
                text,
                (lambda i, k: lambda _icon, _item: self._select(i, k))(inst, key),
                checked=(lambda i, k: lambda _item: i.running and i.preset == k)(inst, key),
                radio=True, enabled=defined))
        items.append(pystray.MenuItem(
            "Disabled",
            (lambda i: lambda _icon, _item: self._select(i, None))(inst),
            checked=(lambda i: lambda _item: not i.running)(inst),
            radio=True))
        items += [
            pystray.Menu.SEPARATOR,
            pystray.MenuItem((lambda i: lambda _item: i.status())(inst),
                             None, enabled=False),
        ]
        return pystray.Menu(*items)

    # ---- actions ---------------------------------------------------------
    def _select(self, inst: "Instance", preset: str | None):
        """Menu clicks run on the UI thread; process work goes to a worker so
        the menu never freezes while a kill waits out its grace period."""
        def work():
            if preset is None:
                inst.stop()
            elif not (inst.running and inst.preset == preset):
                inst.start(preset)
            self.icon.update_menu()
        threading.Thread(target=work, daemon=True).start()

    def _stop_all(self, _icon=None, _item=None):
        def work():
            for inst in self.instances.values():
                inst.stop()
            self.icon.update_menu()
        threading.Thread(target=work, daemon=True).start()

    def _open_logs(self, _icon=None, _item=None):
        LOG_DIR.mkdir(exist_ok=True)
        os.startfile(str(LOG_DIR))              # noqa: S606 - Windows only

    def _quit(self, _icon=None, _item=None):
        self._stop.set()
        for inst in self.instances.values():
            inst.stop()
        self.icon.stop()

    # ---- supervision loop ------------------------------------------------
    def _watch(self):
        """Reap dead children so a crashed orchestrator shows as stopped rather than
        looking alive forever, and keep the status line ticking."""
        while not self._stop.wait(POLL_SEC):
            for inst in self.instances.values():
                if inst.proc is not None and inst.proc.poll() is not None:
                    inst.last_exit = inst.proc.returncode
                    log(f"{inst.key} orchestrator exited (code {inst.last_exit}, "
                        f"preset {inst.preset})")
                    inst.proc, inst.preset = None, None
            try:
                self.icon.update_menu()
            except Exception:
                pass

    def run(self, autostart: list[str] | None = None):
        # a previous tray that was killed may have left orchestrators running
        log(f"tray start (instances: {', '.join(self.instances)})")
        for inst in self.instances.values():
            n = inst.kill_orphans()
            if n:
                log(f"reclaimed {n} orphaned orchestrator(s) for {inst.key}")
        # CLI --autostart wins; otherwise instances marked `autostart:` in
        # config come up on their own, so the shortcut needs no arguments
        keys = list(autostart or [])
        if not keys:
            keys = [k for k, v in CONFIG["instances"].items() if v.get("autostart")]
        for key in keys:
            inst = self.instances.get(key)
            if inst:
                inst.start(inst_preset(key))
        threading.Thread(target=self._watch, daemon=True).start()
        self.icon.run()


def inst_preset(key: str) -> str:
    return CONFIG["instances"][key].get("preset") or CONFIG["preset"]


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Tower autopilot tray supervisor")
    ap.add_argument("--autostart", nargs="*", default=[],
                    help="instance keys to launch immediately")
    args = ap.parse_args()
    Supervisor().run(args.autostart)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        log("FATAL:\n" + traceback.format_exc())
        raise
