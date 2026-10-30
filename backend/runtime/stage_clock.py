"""Per-pass stage timers for the gem collector's one thread (FOCUS #11): where each pass's seconds go.

Recorded only; nothing here changes what any stage does. The collector calls `next_pass()` once per loop turn; every
owner times its own stages with `stage(name)` (nested stages simply overlap their parent: `poll` includes `ad.*` and
`orbit.*`). `next_pass()` returns the aggregate of the last HEARTBEAT_SEC as one dict for a heartbeat event, else None.
One thread uses one clock, like `vision.textocr` timing."""
import time
from contextlib import contextmanager

HEARTBEAT_SEC = 60.0


class StageClock:
    def __init__(self, now=time.monotonic):
        self._now = now
        self._pass = {}
        self._pass_started = None
        self._window_started = None
        self._reset_window()

    def _reset_window(self):
        self._passes = 0
        self._period_total = self._period_max = 0.0
        self._stages = {}       # name -> [total seconds, max seconds in one pass, passes that ran it, calls]
        self._calls = {}

    def add(self, name, seconds):
        self._pass[name] = self._pass.get(name, 0.0) + seconds
        self._calls[name] = self._calls.get(name, 0) + 1

    @contextmanager
    def stage(self, name, into=None):
        """Time one stage; `into` (a dict) also receives this single call's seconds, for a caller that reports it."""
        started = self._now()
        try:
            yield
        finally:
            seconds = self._now() - started
            self.add(name, seconds)
            if into is not None:
                into[name] = round(into.get(name, 0.0) + seconds, 3)

    def next_pass(self, heartbeat_sec=HEARTBEAT_SEC):
        """Close the pass that just ended and start the next. Returns the window's aggregate when it is due."""
        now = self._now()
        if self._pass_started is not None:
            period = now - self._pass_started
            self._passes += 1
            self._period_total += period
            self._period_max = max(self._period_max, period)
            for name, seconds in self._pass.items():
                row = self._stages.setdefault(name, [0.0, 0.0, 0, 0])
                row[0] += seconds
                row[1] = max(row[1], seconds)
                row[2] += 1
                row[3] += self._calls.get(name, 0)
        self._pass, self._calls, self._pass_started = {}, {}, now
        if self._window_started is None:
            self._window_started = now
            return None
        if now - self._window_started < heartbeat_sec or not self._passes:
            return None
        beat = dict(window_sec=round(now-self._window_started, 1), passes=self._passes,
                    period_mean_sec=round(self._period_total/self._passes, 3), period_max_sec=round(self._period_max, 3),
                    stages={name: dict(mean_sec=round(total/self._passes, 3), max_sec=round(peak, 3),
                                       passes=ran, calls=calls)
                            for name, (total, peak, ran, calls) in sorted(self._stages.items())})
        self._window_started = now
        self._reset_window()
        return beat


clock = StageClock()