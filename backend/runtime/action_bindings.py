"""Host bindings of the timed-action contract (roadmap item 6, batch 2): the thin adapters in front of each driver's handlers.

A driver keeps its handler (the gem collectors, the shopper) and registers, for each configured instance, a binding that
answers the questions the generic control, Run now and status seams ask: how a value is installed, whether a request is
accepted, how far the execution a request started has come, what the routine's status is. `BattleSession` asks the bindings
of its host; it names no action. The bindings of the pilot actions and of the four runtime gates that are not timed actions
yet are built here from the host's own state (`rs`); any further binding a host registers is `rs.action_bindings`
(`{routine key: binding}`), which is how a new action reaches the same seams without a branch anywhere else.

This module sends no input and imports no device, adb, capture or settings module: a binding calls methods of the objects
it is handed, and nothing else.
"""
import copy

from towerpilot_workflows import action_contract
from towerpilot_workflows.action_contract import BindingResult, routine_key_valid, split_key


class BindingBase:
    """The defaults of a binding: no documents, no Run now, no variants, never busy."""

    def configure(self, values):
        raise ValueError('Runtime gate is unsupported')

    def configure_document(self, name, value):
        raise ValueError('Runtime gate is unsupported')

    def documents(self):
        return ()

    def runnable(self):
        return False

    def variants(self):
        return ()

    def descriptor_variants(self):
        """The variants the action descriptor lists. The admission list (`capabilities.actions`) already holds every token, so a
        binding with many of them lists none here rather than rewriting them into every status the driver publishes."""
        return ()

    def busy(self):
        return False

    def request_now(self, variant=None, *, command_id=None):
        return False

    def progress(self):
        return None

    def status(self):
        return {}

    def sensing(self):
        """What the routine's recognizer has seen so far (a published snapshot), None when it keeps no such tally."""
        return None

    def routine(self, *, battle_visible, now, epoch):
        raise NotImplementedError

    def mark_unobserved(self, reason):
        return False


def outcome_of(record, action=None):
    """The generic reading of a handler's own outcome record, for a result no one has to interpret: a confirmed effect succeeded,
    taps nobody could confirm failed unconfirmed, a skipped attempt did nothing. Anything else is unknown, never confirmed."""
    if not isinstance(record, dict):
        return None, 'unknown', None
    reason = record.get('reason')
    if record.get('confirmed') is True:
        return 'succeeded', 'confirmed' if action is None else action_contract.effect_of(action, reason), reason
    if record.get('confirmed') is False:
        return ('no_op', 'none', reason) if record.get('outcome') == 'skipped' else ('failed', 'unconfirmed', reason)
    return None, 'unknown', reason


def _acknowledged(record, effect):
    """The collector's own outcome record as the command acknowledgement carries it. The record stays the collector's (its
    counters and published last_outcome count a vanished offer as confirmed); the acknowledgement must not claim a game effect
    that effect_of ruled unconfirmed, so it is a copy that no longer reports confirmed (its outcome says unconfirmed)."""
    if not isinstance(record, dict) or effect != 'unconfirmed' or record.get('confirmed') is not True:
        return record
    claimed = dict(record, confirmed=False)
    if claimed.get('outcome') == 'confirmed':
        claimed['outcome'] = 'unconfirmed'
    return claimed

class CollectorBinding(BindingBase):
    """A gem collector: the ad-gem collector or the floating-gem tapper. Their own `status()` is the routine status.

    The collector is looked up on the host when it is used, not when the binding is made: a host that starts a new battle
    replaces its collectors, and a host that has not attached them yet still publishes what it will run."""

    def __init__(self, rs, path, action=None):
        self.rs, self.path, self.action = rs, path, action

    @property
    def collector(self):
        collector = _attribute(self.rs, self.path)
        if collector is None:
            raise AttributeError(f'The host holds no {self.path}')
        return collector

    def configure(self, values):
        self.collector.configure(values)
        return True

    def runnable(self):
        return True

    def request_now(self, variant=None, *, command_id=None):
        if variant is not None or not self.collector.request_now():
            return False
        self.collector.manual_command_id = command_id      # the window the request starts is linked to its command
        return True

    def status(self):
        return self.collector.status()

    def sensing(self):
        sensing = getattr(self.collector, 'sensing', None)
        return None if sensing is None else sensing()

    def routine(self, *, battle_visible, now, epoch):
        return self.collector.status()

    def mark_unobserved(self, reason):
        mark = getattr(self.collector, 'mark_unknown', None)
        if mark is None:
            return False
        mark(reason)
        return True

    def progress(self):
        state = self.collector.status()
        if state['manual_active']:
            return BindingResult('started', diagnostic=state)
        if state['manual_pending']:
            return BindingResult('pending', diagnostic=state)
        last = state['last_outcome']
        outcome, effect, reason = outcome_of(last, self.action)
        return BindingResult('terminal', outcome=outcome, effect=effect, reason=reason, ack=_acknowledged(last, effect), diagnostic=state)


class ShoppingBinding(BindingBase):
    """The shopper, with the battle session's effective preset: a shopping edit changes both."""

    def __init__(self, rs, session):
        self.rs, self.session = rs, session

    @property
    def shop(self):
        return self.rs.shop

    def runnable(self):
        return self.shop.plan is not None

    def documents(self):
        return ('shopping_plan',) if self.shop.plan is not None else ()

    def variants(self):
        from player.shopping_plan import catalog
        return tuple(['attack', 'defense', 'utility'] + [f"{item['section']}:{item['id']}" for item in catalog()])

    def busy(self):
        return self.shop.active

    def configure(self, values):
        rs, effective = self.rs, self.session.effective
        for field, value in values.items():
            if field == 'enabled':
                rs.shopping_enabled = value
                if effective.get('shopping_plan') is not None:
                    effective['shopping_plan']['enabled'] = value
                    rs.shop.update_plan(effective['shopping_plan'])
                if not value:
                    rs.shop.abort()
            elif field == 'interval_sec':
                effective['shop_interval_sec'] = value
                if effective.get('shopping_plan') is not None:
                    effective['shopping_plan']['interval_sec'] = value
                    rs.shop.update_plan(effective['shopping_plan'])
        return True

    def configure_document(self, name, value):
        if name != 'shopping_plan':
            raise ValueError('Runtime gate is unsupported')
        if value is None:
            raise ValueError('No frozen shopping plan to inherit')
        battle = self.session.battle
        plan = copy.deepcopy(value)
        interval_override = battle['overrides'].get('routines', {}).get('shopping', {}).get('interval_sec')
        enabled_gate = battle['requested_controls']['routines']['shopping']['enabled']
        if interval_override is not None:
            plan['interval_sec'] = interval_override
        plan['enabled'] = enabled_gate
        self.rs.shopping_enabled = enabled_gate
        self.rs.shop.update_plan(plan)
        self.session.effective['shopping_plan'] = plan
        return True

    def request_now(self, variant=None, *, command_id=None):
        pieces = (variant or '').split(':') if variant else []
        self.shop.manual_command_id = command_id
        if not self.shop.request_once(section=pieces[0] if pieces else None, upgrade=pieces[1] if len(pieces) > 1 else None):
            self.shop.manual_command_id = None
            return False
        return True

    def status(self):
        return self.shop.status()

    def progress(self):
        status = self.shop.status()
        if status['active']:
            return BindingResult('started', ack={'outcome': 'unknown'}, diagnostic=status)
        evidence = copy.deepcopy(status.get('manual'))
        if isinstance(evidence, dict) and (status.get('last_sweep') or {}).get('manual'):
            evidence['last_sweep'] = copy.deepcopy(status['last_sweep'])
        outcome, effect, reason = self._outcome_of(status) if isinstance(evidence, dict) else (None, 'unknown', None)
        return BindingResult('terminal', outcome=outcome, effect=effect, reason=reason, ack=evidence, diagnostic=status)

    @staticmethod
    def _outcome_of(status):
        sweep = status.get('last_sweep') or {}
        kind, reason = sweep.get('outcome'), sweep.get('reason')
        table = {'confirmed': ('succeeded', 'confirmed'), 'unconfirmed': ('failed', 'unconfirmed'), 'refused': ('refused', 'none'),
                 'failed': ('failed', 'unknown'), 'error': ('failed', 'unknown'), 'aborted': ('cancelled', 'unknown'),
                 'dry_run': ('no_op', 'none'), 'skipped': ('no_op', 'none'), 'completed': ('no_op', 'none')}
        outcome, effect = table.get(kind, (None, 'unknown'))
        return outcome, effect, reason

    def routine(self, *, battle_visible, now, epoch):
        rs = self.rs
        status = rs.shop.status()
        interval = action_contract.shopping_interval(self.session.effective)
        return dict(enabled=rs.shopping_enabled,
                    state='active' if rs.shop.active else 'scheduled' if rs.shopping_enabled else 'off',
                    next_due_at=epoch if status.get('continuation_ready') else epoch + rs.last_shop + interval - now,
                    window_ends_at=None, last_outcome=status)


class RewardsGate(BindingBase):
    """The reward flows' gate: a change waits while the flow owns the screen (the owned flow finishes first)."""

    def __init__(self, rs):
        self.rs = rs

    def configure(self, values):
        if self.rs.mission.active:
            return False
        self.rs.rewards_enabled = values['enabled']
        return True

    def busy(self):
        return self.rs.mission is not None and self.rs.mission.active

    def routine(self, *, battle_visible, now, epoch):
        rs = self.rs
        return dict(enabled=rs.rewards_enabled, state='active' if rs.mission.active else 'watching' if rs.rewards_enabled else 'off',
                    next_due_at=None, window_ends_at=None)


class WatchGate(BindingBase):
    """The abilities and rescue gates, held by the session; switching abilities on re-normalizes the run's ability state."""

    def __init__(self, name, rs, session):
        self.name, self.rs, self.session = name, rs, session

    def configure(self, values):
        value = values['enabled']
        self.session.gates[self.name] = value
        if self.name == 'abilities' and value:
            self.rs.cl_normalized = False
            self.rs.uw_normalized = False
            self.rs.uw_done.clear()
        return True

    def routine(self, *, battle_visible, now, epoch):
        enabled = self.session.gates.get(self.name, True)
        return dict(enabled=enabled, state='watching' if enabled else 'off', next_due_at=None, window_ends_at=None)


class RepeatGate(BindingBase):
    def __init__(self, rs):
        self.rs = rs

    def configure(self, values):
        self.rs.repeat_enabled = values['enabled']
        return True

    def routine(self, *, battle_visible, now, epoch):
        rs = self.rs
        return dict(enabled=rs.repeat_enabled, state='boundary_wait' if rs.repeat_enabled else 'off', next_due_at=None, window_ends_at=None)


def _attribute(root, path):
    for name in path.split('.'):
        root = getattr(root, name, None)
        if root is None:
            return None
    return root


# routine key, where the host keeps its handler, the binding in front of it, and whether the host binds it only when it holds one
# (a collector host publishes its gem controls whether or not its collectors are attached yet; only a host with a shopper shops)
PILOT_HANDLERS = (('ad_gems', 'gems.ad_gems', lambda rs, session, path: CollectorBinding(rs, path, 'ad_gems'), False),
                  ('floating_gems', 'gems.orbit', lambda rs, session, path: CollectorBinding(rs, path, 'floating_gems'), False),
                  ('shopping', 'shop', lambda rs, session, path: ShoppingBinding(rs, session), True))


def host_bindings(rs, session) -> dict:
    """The bindings of a host, in the order its routines are published: the pilot actions it holds a handler for, the runtime
    gates of a host that runs the whole battle (the shopper marks it), then whatever the host registered itself."""
    bindings = {}
    for key, path, build, only_with_handler in PILOT_HANDLERS:
        if only_with_handler and _attribute(rs, path) is None:
            continue
        bindings[key] = build(rs, session, path)
    if getattr(rs, 'shop', None) is not None:
        bindings['rewards'] = RewardsGate(rs)
        bindings['abilities'] = WatchGate('abilities', rs, session)
        bindings['rescue'] = WatchGate('rescue', rs, session)
        bindings['repeat'] = RepeatGate(rs)
    catalog = action_contract.catalog()
    for key, binding in (getattr(rs, 'action_bindings', None) or {}).items():
        if routine_key_valid(key) and catalog.find(split_key(key)[0]) is not None:
            bindings[key] = binding
    return bindings


def sensing_of(bindings) -> dict:
    """What each routine's recognizer has seen so far ({routine key: snapshot}); a routine with no tally is left out."""
    seen = {key: getattr(binding, 'sensing', lambda: None)() for key, binding in bindings.items()}
    return {key: snapshot for key, snapshot in seen.items() if snapshot is not None}


def reports_progress(binding) -> bool:
    """Does the binding say how far a request has come? Without that a request would be acknowledged from some other handler's
    state (the shopper's, a collector's), so a binding with no `progress()` of its own has no Run now."""
    progress = getattr(type(binding), 'progress', None)
    return progress is not None and progress is not BindingBase.progress


def run_now_tokens(key, binding):
    """The Run now tokens a binding admits: its key, then `<key>:<variant>` for each variant. None when the binding cannot run now,
    when it does not say how a request has come along, and whatever the binding claims, when the action's definition does not offer a
    Run now (`capabilities.run_now` is not True) or marks the action restricted (never user-triggered: a tournament entry, a ticket, a
    surrender). The definition decides what a person may start; the binding only how."""
    definition = action_contract.catalog().find(split_key(key)[0])
    if definition is not None and (definition.restricted or definition.capabilities.run_now is not True):
        return []
    if not binding.runnable() or not reports_progress(binding):
        return []
    return [key] + [f'{key}:{variant}' for variant in binding.variants()]
