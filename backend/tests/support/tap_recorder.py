"""One tap recorder for tests that prove "nothing was tapped".

A stub that raises on a tap proves nothing when the code under test catches the
exception (ad_gems._claim wraps its tap in `except Exception`, and an
AssertionError is one). A recorder cannot be swallowed: the test reads
`recorder.taps` after the call.
"""


class TapRecorder:
    """Stands in for `act.tap`: remembers every call and returns a sent-tap event."""

    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {'dry_run': self.dry_run}

    @property
    def taps(self):
        """The (x, y, ...) positional arguments of every tap, in order."""
        return [args for args, _ in self.calls]

    @property
    def points(self):
        """Just the (x, y) of every tap."""
        return [args[:2] for args in self.taps]

    def assert_no_taps(self, why=''):
        assert not self.calls, f'unexpected tap(s) {self.calls}' + (f': {why}' if why else '')


def tap_recorder(monkeypatch, act_module, *, dry_run=False):
    """Replace `act_module.tap` with a recorder and return it."""
    recorder = TapRecorder(dry_run)
    monkeypatch.setattr(act_module, 'tap', recorder)
    return recorder
