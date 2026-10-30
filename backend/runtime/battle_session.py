"""Driver adapter for the durable battle ledger; checkpoints never send input."""
import copy
import os
import time
import uuid

from player import saved_controls
from player.run_controls import flatten, resolve
from runtime import action_bindings
from towerpilot_workflows import action_contract
from towerpilot_workflows.battles import Battles
from towerpilot_workflows.control_settings import FIELDS, documents


class BattleSession:
    def __init__(self, store, operation_id, preset, config, *, envelope=None, origin='started', previous_battle_id=None, prepared=None):
        self.store, self.operation_id = store, operation_id
        self.actions = None            # the host's ActionSession, attached by ActionSession.for_battle
        self.generation = str(uuid.uuid4())
        self.battle = None
        self.attach = None             # the envelope's `attach` block of an explicit takeover
        self.base_preset = copy.deepcopy(preset)
        self.config = copy.deepcopy(config)
        self.effective = copy.deepcopy(preset)
        self.manual = None
        self._published_at = 0
        self._last_observation = None
        self._last_visible = False
        self._observed_duration = 0.0
        self._gap_started_at = None
        self._observation_gaps = []
        self._setup_owed = None         # the setup report of a battle created but not yet written
        if prepared is None:
            self.prepare_next(preset, config, envelope=envelope, origin=origin, previous_battle_id=previous_battle_id)
        else:
            self.battle = copy.deepcopy(prepared)
            self.generation = prepared['generation']
            self.base_preset = copy.deepcopy(prepared['envelope']['preset'])
            self.effective = copy.deepcopy(self.base_preset)
            self.config = copy.deepcopy(prepared['envelope']['config'])
            self.gates = {key: value['enabled'] for key, value in prepared['envelope']['controls']['routines'].items()}
            self._tag()
        self.attach = copy.deepcopy((self.battle['envelope'] or {}).get('attach'))

    @classmethod
    def from_environment(cls, preset, config, *, origin='started', attach=None):
        """attach: the worker's `attach_worker.Attach` of an explicit takeover. Its named collector record is validated and closed here instead of being
        rediscovered, and only that record is ever linked as the predecessor."""
        from runtime.operations import environment_operation, validate_worker_context
        operations, operation_id = environment_operation()
        if operations is None:
            return None
        path = operations.path
        store = Battles(path)
        from towerpilot_workflows.operations import Operations
        operation = operations.get(operation_id)
        validate_worker_context(operation, config.get('active_instance'), config)
        if os.environ.get('TOWER_BATTLE_ID'):
            from runtime.battle_source import accepted_envelope
            envelope = accepted_envelope(operation)
            prepared = store.get(os.environ['TOWER_BATTLE_ID'])
            if envelope.get('preset_key') != config.get('preset'):
                raise ValueError('Delegated child selected a different frozen preset')
            return cls(store, operation_id, envelope['preset'], envelope['config'], prepared=prepared)
        previous = None
        if attach is not None:
            from runtime import attach_worker
            linked = attach_worker.settle_predecessor(store, Operations(path), attach.predecessor, operation['context'])
        for old in store.active(operation['context']['device_key']):
            owner = Operations(path).get(old['operation_id'])
            if owner['status'] not in ('accepted', 'running'):
                store.finish(old['id'], old['generation'], reason='driver_operation_ended', status='interrupted')
                previous = old['id']
        if attach is not None:
            previous = linked              # never a record this process found by itself
        envelope = operation['request'].get('battle_envelope')
        if envelope and envelope.get('preset_key', config.get('preset')) != config.get('preset'):
            envelope = None
        if envelope:
            preset = envelope['preset']
            config = envelope.get('config', config)
        session = cls(store, operation_id, preset, config, envelope=envelope,
                      origin='partial_adoption' if previous else origin, previous_battle_id=previous)
        return session

    def prepare_next(self, preset=None, config=None, *, envelope=None, origin='started', previous_battle_id=None):
        if self.battle and self.battle['status'] in ('prepared', 'active'):
            if self._setup_owed is None:
                raise ValueError('Cannot replace a live battle envelope')
            self.set_setup(self._setup_owed)     # a retry of a prepare whose setup write was locked out: continue it
            return self.battle
        if preset is not None:
            self.base_preset = copy.deepcopy(preset)
        if config is not None:
            self.config = copy.deepcopy(config)
        from towerpilot_workflows.operations import Operations
        operation = Operations(self.store.path).get(self.operation_id)
        frozen = copy.deepcopy(envelope) if envelope is not None else {
            'preset': copy.deepcopy(self.base_preset), 'config': copy.deepcopy(self.config),
            'controls': resolve(self.base_preset, self.config),
            'source': copy.deepcopy(self.base_preset.get('_source')), 'source_revision': None,
        }
        held = saved_controls.frozen_documents(self.base_preset)
        if held:
            frozen.setdefault('controls', {}).update(held)
        self.battle = self.store.create(self.operation_id, operation['context'], frozen,
                                        generation=self.generation, origin=origin,
                                        previous_battle_id=previous_battle_id)
        self.base_preset = copy.deepcopy(frozen['preset'])
        self.effective = copy.deepcopy(self.base_preset)
        self.gates = {key: value['enabled'] for key, value in frozen['controls'].get('routines', {}).items()}
        self.manual = None
        self._last_observation = None
        self._last_visible = False
        self._observed_duration = 0.0
        self._gap_started_at = None
        self._observation_gaps = []
        self._tag()
        from interactions.loadout import unknown_setup
        self._setup_owed = unknown_setup(
            self.base_preset.get('loadout'),
            detail='adopted' if origin in ('adopted', 'partial_adoption') else 'as_is',
            reason='Setup has not been verified for this battle')
        self.set_setup(self._setup_owed)
        return self.battle

    def set_setup(self, report):
        """Keep observed setup evidence in durable runtime history, outside source."""
        self.store.validate_driver(self.battle['id'], self.generation, self.operation_id)
        self.battle = self.store.update_status(self.battle['id'], self.generation,
                                               {'setup': copy.deepcopy(report)})
        self._setup_owed = None
        return copy.deepcopy(self.battle['runtime']['setup'])

    def _tag(self):
        from runtime import logger
        if hasattr(logger, 'set_battle_context'):
            logger.set_battle_context(dict(battle_id=self.battle['id'], driver_segment=self.generation,
                                          envelope_hash=self.battle['envelope_hash']))

    def effective_preset(self):
        return copy.deepcopy(self.effective)

    def bindings(self, rs):
        """The routine key -> binding of this host: what the generic control, Run now and status seams ask instead of naming an action."""
        bindings = action_bindings.host_bindings(rs, self)
        if self.attach is not None:
            from runtime import attach_worker
            for key in attach_worker.NOT_OFFERED:      # an attached battle never repeats into another one: not offered, so a patch for it is refused
                bindings.pop(key, None)
        return bindings

    def _control_paths(self, key):
        """The live controls a bound routine publishes: the live fields of its catalog entry, or the gate's own `enabled`."""
        definition = action_contract.catalog().find(action_contract.split_key(key)[0])
        if definition is None:
            names = list(FIELDS[key])
        elif not definition.capabilities.live_controls:
            names = []
        else:
            names = [item.path for item in definition.fields if item.live]
        return [f'routines.{key}.{name}' for name in names]

    def capabilities(self, rs):
        bindings = self.bindings(rs)
        controls = [path for key in bindings for path in self._control_paths(key)]
        controls += [name for binding in bindings.values() for name in binding.documents()]
        actions = [token for key, binding in bindings.items() for token in action_bindings.run_now_tokens(key, binding)]
        return dict(controls=controls, actions=actions)

    def observe(self, rs, wave):
        """Call after boundary classification, before any action-producing helper."""
        self.battle = self.store.validate_driver(self.battle['id'], self.generation, self.operation_id)
        if wave is not None and self.battle['status'] == 'prepared':
            self.battle = self.store.activate(self.battle['id'], self.generation, self.capabilities(rs))
        return self.battle['status'] == 'active'

    def finish(self, reason, outcome='unknown', evidence=None, status='ended'):
        """One ledger write that can raise: the battle record. Settling the actions after it is contained
        (ActionSession.settle never raises; a lock there leaves them to the refresher), so a retried finish is a repeat."""
        self.battle = self.store.finish(self.battle['id'], self.generation, reason=reason,
                                        outcome=outcome, evidence=evidence, status=status)
        if self.actions is not None:
            self.actions.settle()      # what this driver left open is closed by its own normal finalization
        return self.battle

    def _apply_control(self, rs, path, value):
        """Install one control through the binding that owns it; False means it cannot be installed yet (the command stays pending)."""
        bindings = self.bindings(rs)
        if path in documents():
            owner = next((binding for binding in bindings.values() if path in binding.documents()), None)
            if owner is None:
                raise ValueError('Runtime gate is unsupported')
            return owner.configure_document(path, value)
        _, key, field = path.split('.')
        binding = bindings.get(key)
        if binding is None:
            raise ValueError('Runtime gate is unsupported')
        return binding.configure({field: value})

    def checkpoint(self, rs, *, battle_visible, observed=()):
        """observed: routine keys the host still observes on a screen that is not the battle (the collector's Ad Gem
        offer on Home); their own visibility is kept instead of being marked unobserved."""
        if self.battle['status'] not in ('prepared', 'active'):
            return
        self.battle = self.store.validate_driver(self.battle['id'], self.generation, self.operation_id)
        self._manual_progress(rs)
        commands = self.store.pending_commands(self.battle['id'], self.generation)
        for command in commands:
            if command['kind'] != 'controls':
                continue
            self.battle = self.store.get(self.battle['id'])
            target = flatten(self.battle['requested_controls'])
            applied = list(command['applied_paths'])
            for path in flatten(command['payload']):
                if path not in applied and self._apply_control(rs, path, target.get(path)):
                    applied.append(path)
            self._acknowledge(command, applied_paths=applied,
                              reason='finishing_owned_flow' if len(applied) < len(flatten(command['payload'])) else None)
        # Manual requests run through the same foreground, battle and collector
        # evidence checks as scheduled actions. No menu recovery is introduced.
        bindings = self.bindings(rs)
        for command in commands:
            if command['kind'] != 'action':
                continue
            action = command['payload']['action']
            if command['state'] == 'requested':
                if self.manual is not None:
                    continue
                if not battle_visible or any(binding.busy() for binding in bindings.values()):
                    continue
                key, _, variant = action.partition(':')
                binding = bindings.get(key)
                if binding is None or not binding.request_now(variant or None, command_id=command['id']):
                    continue
                self.manual = dict(id=command['id'], action=action, binding=binding, began=False, command=command)
            self._manual_progress(rs)
        self.publish(rs, battle_visible=battle_visible, observed=observed)

    def _acknowledge(self, before, *, diagnostic=None, **changes):
        """Mirror durable command progress once, after the ledger accepts it."""
        command = self.store.acknowledge(self.battle['id'], self.generation, before['id'], **changes)
        progress = ('state', 'applied_paths', 'reason', 'evidence')
        if all(before.get(key) == command.get(key) for key in progress):
            return command
        from runtime import logger
        evidence = command.get('evidence')
        identifiers = ('window_id', 'offer_id', 'claim_attempt_id', 'attempt_id', 'sweep_id')
        correlation = {key: value for key, value in (diagnostic or {}).items()
                       if key in identifiers and value is not None}
        if isinstance(evidence, dict):
            correlation.update({key: value for key, value in evidence.items()
                                if key in identifiers and value is not None})
        outcome = command['state']
        reason = command.get('reason')
        if isinstance(evidence, dict):
            if isinstance(evidence.get('outcome'), str):
                outcome = evidence['outcome']
            elif evidence.get('confirmed') is not None:
                outcome = 'confirmed' if evidence['confirmed'] else 'unconfirmed'
            elif isinstance((diagnostic or {}).get('last_sweep'), dict):
                outcome = diagnostic['last_sweep']['outcome']
            if reason is None:
                reason = evidence.get('reason')
                if reason is None and isinstance((diagnostic or {}).get('last_sweep'), dict):
                    reason = diagnostic['last_sweep'].get('reason')
        logger.event('battle_command_acknowledged', command_id=command['id'],
                     command_kind=command['kind'], action=command['payload'].get('action'),
                     battle_id=self.battle['id'], operation_id=self.operation_id,
                     driver_segment=self.generation, revision=command['revision'],
                     state=command['state'], outcome=outcome, reason=reason,
                     applied_paths=command.get('applied_paths', []), evidence=evidence,
                     **correlation)
        return command

    def _manual_progress(self, rs):
        """Acknowledge what the binding reports about the request in flight: started when its execution begins, completed with
        its own evidence when it ends. A binding with no `progress()` is never acknowledged from another handler's state."""
        if self.manual is None:
            return
        manual = self.manual
        result = manual['binding'].progress()
        if result is None:
            return
        diagnostic = result.diagnostic if result.diagnostic is not None else dict(result.refs)
        if result.state == 'started' and not manual['began']:
            manual['command'] = self._acknowledge(manual['command'], state='started', evidence=result.acknowledgement(),
                                                  diagnostic=diagnostic)
            manual['began'] = True
        elif result.state == 'terminal':
            if not manual['began']:
                manual['command'] = self._acknowledge(manual['command'], state='started', diagnostic=diagnostic)
            self._acknowledge(manual['command'], state='completed', evidence=result.acknowledgement(), diagnostic=diagnostic)
            self.manual = None

    def publish(self, rs, *, battle_visible, observed=()):
        now = time.monotonic()
        if self._last_observation is not None:
            delta = now-self._last_observation
            if battle_visible and self._last_visible and delta <= 10:
                self._observed_duration += delta
            elif self._gap_started_at is None:
                self._gap_started_at = time.time()-delta
        if battle_visible and self._gap_started_at is not None:
            self._observation_gaps.append(dict(started_at=self._gap_started_at, ended_at=time.time()))
            self._gap_started_at = None
        self._last_observation, self._last_visible = now, battle_visible
        if now-self._published_at < 1:
            return
        self._published_at = now
        self.battle = self.store.get(self.battle['id'])
        epoch = time.time()
        bindings = self.bindings(rs)
        routines = {key: binding.routine(battle_visible=battle_visible, now=now, epoch=epoch) for key, binding in bindings.items()}
        if not battle_visible:
            for key, binding in bindings.items():
                if key not in observed and binding.mark_unobserved('waiting_for_game_state'):
                    routines[key] = binding.routine(battle_visible=battle_visible, now=now, epoch=epoch)
        for state in routines.values():
            state.update(blocker=state.get('blocker') if battle_visible else 'waiting_for_game_state', server_time=epoch)
        collection = self._collection_evidence(routines, epoch, sensing=action_bindings.sensing_of(bindings))
        status = dict(routines=routines, server_time=epoch,
            collection_evidence=collection,
            observed_wave=rs.tracker.last, last_wave_provenance='observed',
            observed_duration_sec=self._observed_duration, observation_gaps=copy.deepcopy(self._observation_gaps),
            observation_gap_started_at=self._gap_started_at,
            driver_generation=self.generation, observation_status='observing' if battle_visible else 'waiting_for_game_state')
        if self.actions is not None:     # additive: `routines` above stays as the compatibility projection
            try:
                listed = {(action_contract.split_key(key)[1] or key): binding.descriptor_variants()
                          for key, binding in bindings.items() if binding.descriptor_variants()}
                status['actions'] = self.actions.project(routines, observed_at=epoch, control_revision=self.battle.get('control_revision'),
                                                         variants=listed or None, effective=(self.battle.get('applied_controls') or {}).get('routines'))
            except Exception as exc:     # noqa: BLE001 - a projection that cannot be built must never stop the driver
                from runtime import logger
                logger.event('action_projection_failed', error=str(exc), error_type=type(exc).__name__)
        self.store.update_status(self.battle['id'], self.generation, status)

    def _collection_evidence(self, routines, epoch, sensing=None):
        """Count sampled, distinct confirmations inside this battle/segment.

        Collector counters may span battles. Neither command completion nor
        offer disappearance proves a gem balance total. These are durable lower
        bounds on observed confirmations, not reconstructed historical totals.

        sensing: what each routine's recognizer has seen so far this battle ({routine key: snapshot}); carried
        as evidence only. Whether it means a broken recognizer is decided in control/harvest.py.
        """
        segment_start = self.battle['segments'][-1]['started_at']
        start = max(self.battle['created_at'], segment_start)
        previous = self.battle.get('runtime', {}).get('collection_evidence', {})
        if previous.get('generation') == self.generation and previous.get('started_at') == start:
            summary = copy.deepcopy(previous)
        else:
            summary = dict(generation=self.generation, started_at=start,
                           coverage='sampled_driver_observations', ad_claim_confirmations=0,
                           orbit_balance_confirmations=0, orbit_recorded_increase=0,
                           latest_confirmations={})
        # An Ad Gem claim is proven by the offer disappearing in battle, or by a stable exact +6 on the Home counter.
        for name, expected_reasons, counter in (
                ('ad_gems', ('offer_disappeared', 'balance_increased'), 'ad_claim_confirmations'),
                ('floating_gems', ('balance_increased',), 'orbit_balance_confirmations')):
            outcome = routines.get(name, {}).get('last_outcome') or {}
            observed = outcome.get('observed_at')
            if (outcome.get('confirmed') is not True or outcome.get('reason') not in expected_reasons
                    or type(observed) not in (int, float) or not start <= observed <= epoch):
                continue
            identity = outcome.get('claim_attempt_id') or outcome.get('window_id') or outcome.get('attempt_id')
            if not identity:
                continue
            last = summary['latest_confirmations'].get(name, {})
            if last and (last.get('id') == identity or observed <= last['observed_at']):
                continue
            summary[counter] += 1
            summary['latest_confirmations'][name] = dict(id=identity, observed_at=observed)
        summary['orbit_recorded_increase'] = 2*summary['orbit_balance_confirmations']
        if sensing:
            summary['sensing'] = copy.deepcopy(sensing)
        else:
            summary.pop('sensing', None)
        summary['observed_through_at'] = epoch
        return summary

    def manual_for(self, key):
        """Is the Run now request in flight one for this routine (any variant of it)?"""
        return self.manual is not None and self.manual['action'].partition(':')[0] == key

    @property
    def manual_shopping(self):
        return self.manual_for('shopping')
