"""Read-only projection of a battle's takeover facts: the `attach` block it was launched with and the chain of records it continues.

Pure over a battle store and a battle body: no device, no web framework. `control.battles.public` calls it; rules: control/takeover.rules.md.
"""
from player import attach_policy

ENTRY_KEY = 'attach_entry'   # battle.runtime[ENTRY_KEY] = {'observed_wave': int}: what the worker's entry gate read from the screen (written by the takeover worker, T3b)
LINEAGE_LIMIT = 20       # records followed back from a battle; a longer chain is reported as truncated, never silently cut


def entry_wave(battle):
    """The wave the worker's entry gate observed on screen, or None. Only that: the wave the collector had published, the wave the person confirmed and the battle's
    later waves are never substituted for it, so a missing observation reads as unknown."""
    entry = (battle.get('runtime') or {}).get(ENTRY_KEY)
    wave = entry.get('observed_wave') if isinstance(entry, dict) else None
    return wave if type(wave) is int and wave >= 0 else None


def attach_view(battle):
    """The takeover block of a battle for the Dashboard, or None for a battle that was not launched by a takeover.

    A block that no longer validates is reported as `{"invalid": <code>}`: a read never fails because of one stored record, and it never claims a tier."""
    block = (battle.get('envelope') or {}).get('attach')
    if block is None:
        return None
    try:
        checked = attach_policy.validate_envelope_attach(block)
    except attach_policy.AttachPolicyError as exc:
        return {'invalid': exc.code}
    predecessor = checked['predecessor']
    return {'mode': checked['mode'], 'policy_revision': checked['policy_revision'], 'tier': checked['tier'],
            'stripped': checked['stripped'], 'predecessor_battle_id': predecessor['battle_id'] if predecessor else None,
            'observed_wave': entry_wave(battle), 'observed_wave_source': 'entry_gate' if entry_wave(battle) is not None else None}


def lineage(store, battle):
    """The records this battle continues, nearest first: `{predecessors: [{id, origin, status, reason}], truncated}`; None when it continues none.

    Computed on read from `previous_battle_id`, never stored. Only records of the same device are followed; an unknown or foreign record ends the chain
    (`truncated` False: there is nothing more that is ours to show), and a cycle ends it too."""
    previous = battle.get('previous_battle_id')
    if not previous:
        return None
    device = battle['context'].get('device_key')
    seen, found, truncated = {battle['id']}, [], False
    while previous and previous not in seen:
        if len(found) >= LINEAGE_LIMIT:
            truncated = True
            break
        seen.add(previous)
        try:
            record = store.get(previous)
        except KeyError:
            break
        if record['context'].get('device_key') != device:
            break
        found.append({'id': record['id'], 'origin': record['origin'], 'status': record['status'], 'reason': record.get('reason')})
        previous = record.get('previous_battle_id')
    return {'predecessors': found, 'truncated': truncated}

