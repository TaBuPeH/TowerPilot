"""Read canonical scheduler observations without importing the device runtime.

No eligibility is computed here. Old observations remain inspectable, while a
stale or unavailable observation never supplies a current winning block.
"""
import copy
import time

from control import operations


FRESH_FOR_SEC = 90.0


def read(root, *, instance: str, account=None, serial=None, profile=None,
         device_key=None, now: float | None = None) -> dict:
    """Read only an active scheduler matching the requested account/profile."""
    epoch = time.time() if now is None else now
    context = dict(instance=instance, account=account, serial=serial, profile=profile)
    result = dict(availability='unavailable', available=False,
                  reason='scheduler_not_observed', winner=None, observed_winner=None,
                  observed_at=None, timezone=None, utc_offset=None, context=context,
                  next_clock_boundary=None, handoff='natural_run_boundary',
                  operation_id=None, operation_status=None, step=None, recovery_action=None,
                  server_time=epoch, fresh_for_sec=FRESH_FOR_SEC)
    candidates = [op for op in operations.Operations.active_readonly(operations.ledger_path(root), device_key)
                  if op['kind'] == 'run'
                  and all(op['context'].get(key) == value for key, value in context.items())
                  and isinstance(op.get('progress', {}).get('schedule'), dict)]
    if not candidates:
        return result
    operation = max(candidates, key=lambda op: op['updated_at'])
    snapshot = copy.deepcopy(operation['progress']['schedule'])
    result.update(snapshot, operation_id=operation['id'], operation_status=operation['status'],
                  step=operation['step'], recovery_action=operation.get('recovery_action'),
                  observed_winner=snapshot.get('winner'))
    observed = snapshot.get('observed_at')
    age = epoch - observed if type(observed) in (int, float) else None
    if operation['status'] != 'running':
        result.update(availability='unavailable', reason='scheduler_not_running')
    elif operations.worker_alive(operation.get('worker')) is not True:   # gone or unverifiable: no worker is vouched for
        result.update(availability='unavailable', reason='scheduler_worker_unavailable')
    elif age is None or not 0 <= age <= FRESH_FOR_SEC:
        result.update(availability='stale', reason='scheduler_observation_stale')
    result['available'] = result['availability'] == 'available'
    if not result['available']:
        result['winner'] = None
        result['next_clock_boundary'] = None
    return result
