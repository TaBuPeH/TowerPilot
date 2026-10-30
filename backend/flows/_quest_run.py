"""Process-local evidence for quest cycles; adopting never grants exit authority."""
import time

from device import capture
from interactions.tourney import Abort
from vision import detect, screen, wave_reader


class QuestRun:
    def __init__(self):
        self.phase = "unowned"
        self.owned = False
        self.wave = None
        self.unreadable_since = None
        self.menu_seen = False

    def refuse(self, why):
        self.phase = "revoked"
        self.owned = False
        raise Abort(f"quest run authority refused: {why}")

    def home(self, frame=None):
        frame = capture.grab() if frame is None else frame
        dead, _ = detect.death_screen(frame)
        if (screen.identify(frame).name != "home"
                or screen.in_tournament(frame)
                or wave_reader.read_wave(frame) is not None
                or detect.side_menu_open(frame) or dead):
            self.refuse("setup requires clean Home")
        return frame

    def _battle(self, frame, *, readable=False, menu=False):
        while True:
            frame, ready = self._observe_battle(frame, menu=menu)
            if ready or not readable:
                return frame
            time.sleep(0.2)
            frame = capture.grab()

    def _observe_battle(self, frame, *, menu=False):
        if screen.in_tournament(frame):
            self.refuse("tournament")
        if screen.identify(frame).name != "battle":
            self.refuse("left the observed battle")
        if detect.side_menu_open(frame) and not menu:
            self.refuse("foreign side menu")
        wave = wave_reader.read_wave(frame)
        if wave is None:
            if self.unreadable_since is None:
                self.unreadable_since = time.monotonic()
            if time.monotonic() - self.unreadable_since > 6:
                self.refuse("wave remained unreadable")
        else:
            self.unreadable_since = None
            if self.wave is not None and wave < self.wave:
                self.refuse("wave rollback / replacement run")
            self.wave = wave
        return frame, wave is not None

    def observe(self, frame):
        if self.phase != "live":
            self.refuse("no continuous live cycle")
        return self._battle(frame)

    def check(self, frame):
        self.observe(frame)
        return self._battle(frame, readable=True)

    def fresh(self):
        return self.check(capture.grab())

    def pause(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.observe(capture.grab())
            time.sleep(min(0.3, max(0, deadline - time.monotonic())))

    def adopt(self, frame):
        self.owned = False
        self.phase = "live"
        self.check(frame)
        # Deliberately no ownership, even after many observed waves.

    def start(self):
        from flows import shard
        self.home()
        try:
            shard.start_battle(guard=self.guard)
        except Exception:
            self.phase, self.owned = "revoked", False
            raise

    def abandon(self, *, to_home=False):
        from flows import shard
        if not self.owned:
            self.refuse("this process did not start the run")
        try:
            shard.abandon_run(to_home=to_home, guard=self.guard)
            if not to_home:
                # RETRY dispatch alone is not evidence that a successor exists.
                shard.wait_for_wave(1, guard=self.guard, recover_home=False)
        except Exception:
            self.phase, self.owned = "revoked", False
            raise

    def guard(self, frame, stage="battle", dispatched=None):
        """Validate each shared helper observation and each input transition."""
        if self.phase == "revoked":
            self.refuse("previous evidence was revoked")
        transitions = {"start_dispatched": "starting", "retry_dispatched": "retrying",
                       "menu_dispatched": "menu", "button_dispatched": "confirm",
                       "confirm_dispatched": "stats", "home_dispatched": "home_wait",
                       "sprint_dispatched": "sprint", "sprint_confirm_dispatched": "sprint"}
        if stage in transitions:
            if not isinstance(dispatched, dict) or dispatched.get("dry_run") is not False:
                self.refuse("input was not dispatched")
            self.phase = transitions[stage]
            if stage == "menu_dispatched":
                self.menu_seen = False
            if stage in ("start_dispatched", "retry_dispatched", "home_dispatched"):
                self.owned = False
                self.wave = None
            return frame
        if stage == "start_home":
            self.home(frame)
            self.owned = False
            self.wave = None
            return frame
        if stage in ("start_wait", "wave") and self.phase in ("starting", "retrying"):
            name = screen.identify(frame).name
            if screen.in_tournament(frame):
                self.refuse("tournament during start")
            if name == "battle":
                frame = self._battle(frame, readable=True)
                self.phase = "live"
                self.owned = True
            elif name not in ("home", "unknown", "game_stats"):
                self.refuse("foreign screen during start")
            elif self.phase == "starting" and name == "game_stats":
                self.refuse("foreign stats during start")
            elif self.phase == "retrying" and name == "home":
                self.refuse("RETRY returned Home")
            return frame
        if stage in ("battle", "wave", "start_wait"):
            return self.check(frame)
        if stage == "exit_begin":
            if not self.owned:
                self.refuse("cannot exit an adopted run")
            return self.check(frame)
        if stage == "exit_menu":
            frame = self._battle(frame, readable=True, menu=True)
            opened = detect.side_menu_open(frame)
            if self.phase != "menu" or (self.menu_seen and not opened):
                self.refuse("our side menu was closed")
            self.menu_seen = self.menu_seen or opened
            return frame
        if stage == "exit_button":
            _, readable = self._observe_battle(frame, menu=True)
            if not readable:
                self.refuse("exit target has no current readable wave")
            if self.phase != "menu" or not detect.side_menu_open(frame):
                self.refuse("exit button outside our side menu")
            return frame
        if screen.in_tournament(frame):
            self.refuse("tournament during quest transition")
        name = screen.identify(frame).name
        wave = wave_reader.read_wave(frame)
        if wave is not None and self.wave is not None:
            if wave < self.wave:
                self.refuse("wave rollback during quest transition")
            self.wave = wave
        if stage == "sprint_begin":
            return self.check(frame)
        elif stage in ("sprint_open_wait", "sprint_after"):
            if self.phase != "sprint":
                self.refuse("no owned sprint transition")
            deadline = time.monotonic() + 6
            while True:
                if screen.in_tournament(frame):
                    self.refuse("tournament during sprint transition")
                wave = wave_reader.read_wave(frame)
                if wave is not None and self.wave is not None and wave < self.wave:
                    self.refuse("wave rollback during sprint transition")
                if wave is not None:
                    self.wave = wave
                if stage == "sprint_open_wait" and name == "intro_sprint_end":
                    return frame
                if stage == "sprint_after" and name == "battle":
                    self.phase = "live"
                    return self.check(frame)
                if name not in ("unknown", "intro_sprint_end", "battle"):
                    self.refuse("foreign sprint transition")
                if time.monotonic() >= deadline:
                    if stage == "sprint_open_wait" and name == "battle":
                        self.phase = "live"  # indicator tap was swallowed
                        return self.check(frame)
                    self.refuse("sprint transition timed out")
                if name == "battle":
                    self._battle(frame)
                time.sleep(0.2)
                frame = capture.grab()
                name = screen.identify(frame).name
        elif stage == "sprint_confirm":
            if self.phase != "sprint" or name != "intro_sprint_end":
                self.refuse("foreign sprint confirmation")
        elif stage == "exit_poll":
            if name == "game_stats" and self.phase in ("stats", "results"):
                self.phase = "results"   # retire the old menu/confirmation source
                return frame
            if name == "battle":
                if self.phase == "results":
                    self.refuse("left our stats destination")
                frame = self._battle(frame, menu=True)
                if not detect.side_menu_open(frame):
                    self.refuse("our exit menu was closed")
                return frame
            allowed = {"confirm": {"exit_battle", "end_round", "unknown"},
                       "stats": {"exit_battle", "end_round", "unknown"},
                       "results": {"unknown"}}
            if name not in allowed.get(self.phase, set()):
                self.refuse("foreign screen during exit")
        elif stage == "exit_confirm":
            if self.phase not in ("confirm", "stats") or name not in ("exit_battle", "end_round"):
                self.refuse("unowned exit confirmation")
        elif stage in ("exit_retry", "exit_home"):
            if self.phase != "results" or name != "game_stats":
                self.refuse("unowned stats")
        elif stage == "home_wait":
            if self.phase != "home_wait" or name not in ("home", "game_stats", "unknown"):
                self.refuse("foreign screen returning Home")
            if name == "home":
                self.home(frame)
                self.phase = "home"
        else:
            self.refuse(f"unexpected checkpoint {stage}")
        return frame
