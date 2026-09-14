"""Shipped behavior recipes instantiated into an account's existing profile.

Templates own no equipment, images or device coordinates. The existing profile
compiler and launch readiness checks remain authoritative.
"""
from copy import deepcopy
import json
from pathlib import Path
import re


def catalogue():
    return json.loads(Path(__file__).with_suffix('.json').read_text(encoding='utf-8'))


def starter_runs(profile):
    """New accounts receive all three text-only recipes without an import step."""
    result = deepcopy(profile)
    result['blueprints'] = {}
    result.pop('plan', None)
    for template, name in [('farm', 'coin_default'), ('tournament', 'tourney_main'),
                           ('shard', 'shard_run')]:
        result = instantiate(result, template, name,
                             {'loadout': 'as_is' if template == 'farm' else 'my_equipment'})
    return result


def instantiate(profile, template_id, name, options=None):
    if not isinstance(profile,dict):
        raise ValueError('Choose a valid local profile.')
    if not isinstance(name,str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}',name):
        raise ValueError('Run identifier must start with a letter and contain only lowercase letters, numbers and underscores.')
    if name in profile.get('blueprints',{}):
        raise ValueError('That run already exists. Choose a different identifier.')
    template=next((t for t in catalogue()['templates'] if t['id']==template_id),None)
    if template is None:
        raise ValueError('Unknown run template.')
    options={} if options is None else options
    if not isinstance(options,dict):
        raise ValueError('Run choices must be an object.')
    allowed={'label','tier','loadout','enable_uw','enable_rescue'}
    kind=template['blueprint']['kind']
    if kind=='shard': allowed.add('count')
    if kind=='tournament': allowed.add('gem_entry_max')
    if template['blueprint'].get('dissonant_tab'):
        allowed.update({'dissonant_tab','perk_bans'})
    if set(options)-allowed:
        raise ValueError('Unsupported choices: '+', '.join(sorted(set(options)-allowed)))
    if 'dissonant_tab' in options:
        from player.playerprofile import DISSONANT_TABS
        if options['dissonant_tab'] not in DISSONANT_TABS:
            raise ValueError('Choose the workshop tab to disable: '+', '.join(DISSONANT_TABS)+'.')
    if 'perk_bans' in options:
        bans=options['perk_bans']
        if not isinstance(bans,list) or not all(isinstance(b,str) for b in bans):
            raise ValueError('Perk bans must be a list of perk texts.')
        options['perk_bans']=[b.strip() for b in bans if b.strip()]
        if not options['perk_bans']:
            options.pop('perk_bans')     # nothing named = leave the game's bans alone
    for key in ('enable_uw','enable_rescue'):
        if key in options and not isinstance(options[key],bool):
            raise ValueError(key+' must be true or false.')
        if options.get(key) and key.removeprefix('enable_') not in template['optional_policies']:
            raise ValueError('This template does not provide '+key.removeprefix('enable_')+' rules.')
    result=deepcopy(profile)
    bp=deepcopy(template['blueprint'])
    for key,value in options.items():
        if key.startswith('enable_'):continue
        bp[key]=value
    refs=bp.setdefault('policies',{})
    for group,ref in template['optional_policies'].items():
        if options.get('enable_'+group):refs[group]=ref
    # Give every copied policy its own identity. Editing this run must not
    # change another run or overwrite the account's existing policy library.
    for group,section in [('gather','gather'),('uw','uw_policies'),('rescue','rescue_policies'),('shopping','shopping_lists')]:
        old=bp.get('shopping') if group=='shopping' else refs.get(group)
        if not old:continue
        new=name+'__'+old
        target=result.setdefault('policies',{}).setdefault(section,{})
        if new in target:raise ValueError('A policy for that run identifier already exists.')
        target[new]=deepcopy(template['policies'][section][old])
        if group=='shopping':bp['shopping']=new
        else:refs[group]=new
    result.setdefault('blueprints',{})[name]=bp
    return result
