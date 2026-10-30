"""Worker side of an explicit takeover: the entry gate, the durable predecessor and the refusal record.

An attached run is a `run` operation whose request carries `attach` and whose `battle_envelope` carries the server-built `attach` block
(`player/attach_policy.py` owns both shapes). The worker proves from ONE frame that a battle is on screen and is not a tournament before its first
input and before it writes any ledger record, then continues that battle without starting, ending or re-equipping anything. This module holds only the
decisions; the orchestrator calls it and keeps every device call. Nothing here taps, swipes or captures.
"""
import copy
from dataclasses import dataclass

from player import attach_policy

# A wave this far below the wave the person saw is not the battle they confirmed (the orchestrator's own backward-wave margin).
WAVE_MARGIN = 100
# The reasons a finished predecessor may carry for the link to be kept: the collector's own record ended by the collector going away.
SETTLED_REASONS = ("driver_operation_ended", "driver_observation_lost")
_OWNER_LIVE = ("accepted", "running")
# Routine gates an attached battle never binds, so a live-control patch for one is refused as unsupported: it must not repeat into another battle.
NOT_OFFERED = ("repeat",)
_IDENTITY = ("device_key", "instance", "account", "serial")


class AttachRefused(Exception):
    """The worker refuses to attach. `code` is the operation's error code; nothing was sent to the game and no record was written."""

    def __init__(self, code, detail=None):
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True)
class Attach:
    block: dict            # the validated `battle_envelope.attach`
    request: dict          # the validated request `attach`

    @property
    def tier(self):
        """The declared tier, or None when unknown. Never read from the preset."""
        return self.block["tier"]["value"]

    @property
    def predecessor(self):
        return self.block["predecessor"]


def _invalid(detail):
    return AttachRefused("attach_envelope_invalid", detail)


def from_operation(operation, preset, config, *, delegated=False):
    """The `Attach` an accepted operation asks for, None for an ordinary run; `AttachRefused` when it asks for one it cannot describe.

    Fails closed: an `attach` in the request without its envelope block (or the reverse), two disagreeing copies, a restored preset that still carries a
    stripped key, or a control that turns repeat or rewards on all refuse. The operation, not argv or the environment, is the authority."""
    request = operation.get("request") or {}
    envelope = request.get("battle_envelope")
    envelope = envelope if isinstance(envelope, dict) else {}
    asked, built = "attach" in request, "attach" in envelope
    if not asked and not built:
        return None
    if operation.get("kind") != "run":
        raise _invalid("a takeover is a run operation")
    if delegated:
        raise _invalid("a delegated child cannot attach")
    if not (asked and built):
        raise _invalid("the request and the envelope must both carry attach")
    try:
        block = attach_policy.validate_envelope_attach(envelope["attach"])
        asked_for = attach_policy.validate_request(request["attach"])
        if envelope.get("preset_key") != config.get("preset"):
            raise _invalid("the envelope names a different run")
        if block["policy_revision"] != asked_for["policy_revision"] or block["tier"] != asked_for["tier"]:
            raise _invalid("the request and the envelope disagree")
        # the preset the worker runs, the one the envelope froze and the copy inside the frozen configuration must all be restricted
        copies = {"the preset": preset, "the envelope preset": envelope.get("preset"),
                  "the frozen configuration": ((envelope.get("config") or {}).get("presets") or {}).get(envelope.get("preset_key"))}
        for where, body in copies.items():
            left = attach_policy.unrestricted_keys(body)
            if left:
                raise _invalid(f"{where} still carries " + ", ".join(left))
        routines = ((envelope.get("controls") or {}).get("routines")) or {}
        for path in attach_policy.FORCED_OFF:
            _, key, field = path.split(".")
            if (routines.get(key) or {}).get(field) is not False:
                raise _invalid(f"{path} must be false")
    except attach_policy.AttachPolicyError as exc:
        raise _invalid(str(exc)) from exc
    return Attach(block=copy.deepcopy(block), request=copy.deepcopy(asked_for))


def entry_gate(attach, frame, *, identify, read_wave, tournament_state, input_revoked=lambda: False):
    """Prove from one frame that the battle is on screen and is not a tournament; `AttachRefused` otherwise. The callables are the screen readers.

    Order: input, battle, tournament (unknown refuses: a missing badge template proves nothing), wave. Reads only; the caller sends no input before this
    returns."""
    if input_revoked():
        raise AttachRefused("attach_input_revoked", "a person revoked automation input")
    wave = read_wave(frame)
    if identify(frame) != "battle" or wave is None:
        raise AttachRefused("attach_no_battle", "the battle is not on screen or its wave cannot be read")
    state = tournament_state(frame)
    if state == "yes":
        raise AttachRefused("attach_tournament", "the tournament badge is on screen")
    if state != "no":
        raise AttachRefused("attach_tournament_unknown", "takeover refuses to guess whether this is a tournament")
    expected = attach.request["expected_battle"]["observed_wave"]
    if expected is not None and wave < expected - WAVE_MARGIN:
        raise AttachRefused("attach_wave_mismatch", f"wave {wave} is far below the wave {expected} that was confirmed")
    return wave


def entry_record(wave):
    """What the worker stores in the battle record's runtime as `attach_entry`: the wave the entry gate itself read from its one frame. A value that is not a
    non-negative integer is stored as None (unknown), never guessed; nothing else supplies this number."""
    return {"observed_wave": wave if type(wave) is int and wave >= 0 else None, "source": "entry_gate"}


def settle_predecessor(store, operations, predecessor, context):
    """Validate the collector record the server named and close it; return its id, or None when no collector held the emulator.

    The record must exist, belong to the same device, instance, account and serial and to the named operation and generation. A live record whose
    operation has ended is finished `interrupted`/`driver_operation_ended`; one that is already interrupted for a collector's reason is kept as it is. A
    conflicting finalization on an already finished record counts as finalized, never as a failure. Anything else refuses before a record is created."""
    if predecessor is None:
        return None
    from towerpilot_workflows.operations import Conflict

    def mismatch(detail):
        return AttachRefused("attach_predecessor_mismatch", detail)

    try:
        old = store.get(predecessor["battle_id"])
    except Exception as exc:                                    # noqa: BLE001 - an unknown id is a mismatch, whatever the store raises
        raise mismatch("the named record does not exist") from exc
    if any(old["context"].get(name) != context.get(name) for name in _IDENTITY):
        raise mismatch("the named record belongs to another emulator or account")
    if old["operation_id"] != predecessor["operation_id"] or old["generation"] != predecessor["generation"]:
        raise mismatch("the named record belongs to another operation")
    if old["status"] in ("prepared", "active"):
        if operations.get(old["operation_id"])["status"] in _OWNER_LIVE:
            raise mismatch("the collector operation is still running")
        try:
            old = store.finish(old["id"], old["generation"], reason="driver_operation_ended", status="interrupted")
        except Conflict:
            old = store.get(old["id"])                          # another finalizer got there first
    if old["status"] in ("prepared", "active"):
        raise mismatch("the named record could not be closed")
    if not (old["status"] == "interrupted" and old.get("reason") in SETTLED_REASONS):
        raise mismatch("the named record ended for another reason")
    return old["id"]


def refuse(operations, operation_id, error):
    """Record the coded failure on the operation (the worker wrapper keeps a terminal outcome it did not write). Writes no battle record."""
    op = operations.get(operation_id)
    if op["status"] in _OWNER_LIVE or op["status"] == "cancelling":
        operations.transition(operation_id, op["revision"], "failed",
                              error={"code": error.code, "message": str(error.detail or error.code)[:1000]})
    return error
