"""A synthetic world for the worker half of a takeover (roadmap item 3, T3b): a ledger, a collector's battle record, an accepted attach operation and
scripted frames. No device, no adb, no socket: the screen readers and every input call are replaced by recorders, and a frame is a dict
(`screen`, `wave`, `tournament`)."""
import copy
from types import SimpleNamespace

from player import attach_policy
from player.run_controls import effective_preset, resolve
from towerpilot_workflows.battles import Battles
from towerpilot_workflows.operations import Operations

SERIAL = "synthetic-t3b"
RUN = "farm"


def full_preset():
    """A run as a profile compiles it: Home-only entry and equipment beside what an attached battle keeps."""
    return dict(kind="coin", tier=14, loadout={"cards": "x"}, perk_bans=["a"], restart_via_home=True,
                shopping=[dict(upgrade="damage")], shop_interval_sec=37, gather={"ad_gems": False, "guild": True})


def base_config():
    return dict(active_instance="main", active_profile=None, preset=RUN, screen={"width": 1080, "height": 2560}, loop={"fps": 10, "dry_run": True}, logging={}, fleet={"first_wave": 750, "interval": 750},
                instances={"main": dict(serial=SERIAL, preset=RUN)}, presets={RUN: full_preset()})


def frame(screen="battle", wave=120, tournament="no"):
    return dict(screen=screen, wave=wave, tournament=tournament)


def request_attach(revision, *, tier=None, observed_wave=None, battle_id=None, generation=None):
    return {"mode": attach_policy.MODE, "policy_revision": revision,
            "expected_battle": {"battle_id": battle_id, "generation": generation, "observed_wave": observed_wave},
            "tier": {"value": tier, "provenance": "unknown" if tier is None else "declared"},
            "attestation": attach_policy.ATTESTATION}


class World:
    def __init__(self, tmp_path):
        self.cfg = base_config()
        self.operations = Operations(tmp_path / "operations.db")
        self.store = Battles(self.operations.path)
        self.context = dict(device_key="synthetic-endpoint", instance="main", serial=SERIAL, account=None,
                            adb_host="127.0.0.7", adb_port=5047, config=copy.deepcopy(self.cfg),
                            artifact_dir=str(tmp_path / "artifacts"))
        self.op = None

    # ---- a collector that held the emulator
    def collector(self, *, finish=None, owner="succeeded"):
        """A collector operation with a live battle record; `finish` ('reconcile') interrupts the record the way the refresher does; `owner` is the
        collector operation's final status (None leaves it running)."""
        op, _ = self.operations.accept("gems", self.context, {})
        op = self.operations.transition(op["id"], op["revision"], "running", worker={"pid": 1, "creation_time": 1})
        envelope = dict(preset=full_preset(), config=copy.deepcopy(self.cfg), controls=resolve(full_preset(), self.cfg), preset_key=RUN)
        battle = self.store.create(op["id"], self.context, envelope, generation="collector-generation", origin="started")
        battle = self.store.activate(battle["id"], battle["generation"], {"controls": [], "actions": []})
        if finish == "reconcile":
            self.store.finish(battle["id"], battle["generation"], reason="driver_operation_ended", status="interrupted")
        if owner is not None:
            op = self.operations.transition(op["id"], op["revision"], owner)
        self.collector_op = op
        return battle

    def predecessor(self, battle):
        return {"battle_id": battle["id"], "generation": battle["generation"], "operation_id": battle["operation_id"]}

    # ---- the attach operation
    def attach_operation(self, *, tier=None, observed_wave=None, predecessor=None, mutate=None, running=True):
        preset = effective_preset(self.cfg, RUN)
        controls = resolve(preset, self.cfg)
        frozen = copy.deepcopy(self.cfg)
        restricted, restricted_controls, restricted_frozen = attach_policy.restrict_envelope(
            preset, controls, frozen, RUN, {"value": tier, "provenance": "unknown" if tier is None else "declared"})
        revision = attach_policy.policy_revision(RUN, preset)
        stripped = [key for key in attach_policy.STRIPPED_KEYS if key in preset]
        block = attach_policy.build_envelope_attach(
            revision, {"value": tier, "provenance": "unknown" if tier is None else "declared"}, stripped, predecessor)
        envelope = dict(preset=restricted, config=restricted_frozen, controls=restricted_controls, preset_key=RUN,
                        source=None, source_revision=None, attach=block)
        request = dict(attach=request_attach(revision, tier=tier, observed_wave=observed_wave), battle_envelope=envelope)
        if mutate is not None:
            mutate(request)
        op, _ = self.operations.accept("run", self.context, request)
        self.op = op if not running else self.operations.transition(op["id"], op["revision"], "running", worker={"pid": 2, "creation_time": 2})
        self.cfg = restricted_frozen if mutate is None else self.cfg
        return self.op

    def records(self):
        return self.store.list(limit=100)


def readers(monkeypatch, script, taps):
    """Replace the screen readers and the input boundary. `script` is a list of frames; a frame is read once per capture."""
    from device import act, capture
    from vision import screen, wave_reader
    state = SimpleNamespace(frames=list(script), index=-1)

    class Exhausted(BaseException):
        """The scripted frames ran out: the test's own end of the loop."""

    def grab():
        state.index += 1
        if state.index >= len(state.frames):
            raise Exhausted
        return state.frames[state.index]

    def current(value):
        return value if isinstance(value, dict) else frame()

    monkeypatch.setattr(capture, "grab", grab)
    monkeypatch.setattr(wave_reader, "read_wave", lambda f: current(f)["wave"])
    monkeypatch.setattr(screen, "identify", lambda f: SimpleNamespace(name=current(f)["screen"]))
    monkeypatch.setattr(screen, "tournament_state", lambda f: current(f)["tournament"])
    monkeypatch.setattr(screen, "in_tournament", lambda f: current(f)["tournament"] == "yes")
    for name in ("tap", "swipe"):
        monkeypatch.setattr(act, name, lambda *a, _n=name, **k: taps.append((_n, a, k)) or {})
    return state, Exhausted
