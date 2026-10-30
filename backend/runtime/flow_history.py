"""The history of the reward flows a driver runs through `missions.Mission` (roadmap item 7, A3a).

A Mission runs one generator flow at a time, one action per frame, and reports the end of every flow through its `on_end` hook. This module
turns that into execution rows: the host opens a row when it starts a flow (`opened`), tells what the flow proved while it ran (`proved`),
and the hook closes the row from how the flow ended (`ended`). A badge check that started no flow is one aggregated no-op row (`checked`).

It sends no input, imports no device, adb or capture module, and never raises: the recorders it uses are contained
(`runtime.action_session`), and a row that cannot be written is counted there, not here. The flows themselves are unchanged. A flow that
ends normally is `succeeded` with the effect UNKNOWN unless the host proved one: handler completion is not a game effect. An action whose
definition lists evidence (`ActionDefinition.evidence`) is evidence-gated: it is `succeeded` only with proof; a completed flow without it is `no_op`.
"""
from towerpilot_workflows import action_contract

HOW = ('completed', 'interrupted', 'crashed', 'aborted')


def flow_outcome(gated, how, proof):
    """The recorded outcome of a flow: (result, reason, effect, evidence, confirmed). `proof` is None or (reason, count) the host proved."""
    effect = 'confirmed' if proof else 'unknown'
    evidence, confirmed = ((proof[0],), proof[1]) if proof else ((), 0)
    if how == 'completed':
        if proof:
            return 'succeeded', proof[0], 'confirmed', evidence, confirmed
        if gated:
            return 'no_op', 'no_effect_confirmed', 'none', (), 0
        return 'succeeded', 'flow_completed', 'unknown', (), 0
    if how == 'crashed':
        return 'failed', 'flow_crashed', effect, evidence, confirmed
    return 'cancelled', 'flow_interrupted' if how == 'interrupted' else 'flow_aborted', effect, evidence, confirmed


class FlowHistory:
    def __init__(self, session):
        self.session = session
        self._recorders = {}
        self._open = None            # [action, recorder, proof] of the flow in progress
        self._finished = {}          # action -> id of its newest finished execution (a dependent action names it as its parent)

    @property
    def action(self):
        """The action whose flow is running, else None."""
        return None if self._open is None else self._open[0]

    def _recorder(self, action):
        if action not in self._recorders:
            self._recorders[action] = self.session.recorder(action)
        return self._recorders[action]

    def checked(self, action, *, reason='badge_absent'):
        """A check that looked at the screen and started no flow: one aggregated no-op row."""
        self._recorder(action).check(result='no_op', reason=reason)

    def opened(self, action, *, trigger='automatic', occurrence_key=None, parent=None):
        """A flow of `action` has started. `parent` names the action whose newest finished execution this one follows (trigger 'dependency')."""
        recorder = self._recorder(action)
        recorder.opened(trigger=trigger, occurrence_key=occurrence_key, parent_execution=self._finished.get(parent) if parent else None)
        self._open = [action, recorder, None]

    def proved(self, reason, count=1):
        """The flow in progress has shown a game effect (a claim, a cooldown on the card)."""
        if self._open is not None:
            self._open[2] = (reason, int(count))

    def ended(self, how):
        """The flow ended (`HOW`): close its row."""
        if self._open is None:
            return
        action, recorder, proof = self._open
        self._open = None
        execution_id = recorder.execution_id
        definition = action_contract.catalog().find(action)
        result, reason, effect, evidence, confirmed = flow_outcome(bool(definition and definition.evidence), how, proof)
        recorder.closed(result=result, reason=reason, effect=effect, confirmed=confirmed, evidence=evidence)
        if execution_id is not None:
            self._finished[action] = execution_id
