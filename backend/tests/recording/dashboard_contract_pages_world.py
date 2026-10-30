"""Shared pieces of the page-contract generator (dashboard_contract_pages_v1.json).

The page contract is generated exactly like dashboard_contract_v1.json: the REAL Flask routes of
backend/dashboard.py driven over synthetic state (the World of test_dashboard_contract.py), never the live
backend, device, lease, process table or a running dashboard. This module only adds what the page families
need on top of that world:

  * PagesGenerator - the base Generator whose case table refuses a duplicate case name (the base table
    overwrites silently, which would let a later case replace an earlier one unnoticed);
  * raw_post - a POST whose body is not JSON (the request is recorded as the text that was sent);
  * the small readers every family uses.
"""
import contextlib
import datetime
import json
import os
from types import SimpleNamespace

from test_dashboard_contract import Generator


class FamilyTable(dict):
    """One family's cases: a name may be recorded once."""

    def __init__(self, family):
        super().__init__()
        self.family = family

    def __setitem__(self, case, value):
        if case in self:
            raise AssertionError(f'case {self.family}.{case} is recorded twice')
        super().__setitem__(case, value)


class Families(dict):
    def setdefault(self, family, default=None):
        if family not in self:
            super().__setitem__(family, FamilyTable(family))
        return self[family]


def pin_datetime(w):
    """The dashboard stamps backups with datetime.datetime.now(): on the world's fixed clock (UTC), so a backup's name
    is the same on every run and on every machine. (The ledger modules already run on that clock.)"""
    class Fixed(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            moment = datetime.datetime.fromtimestamp(w.clock.now, datetime.timezone.utc)
            return moment if tz else moment.replace(tzinfo=None)
    w.mp.setattr(w.dash, 'datetime', SimpleNamespace(datetime=Fixed))


def pin_mtimes(w, folder='profiles'):
    """File modification times are the machine's; the listings publish them. Pin each file of `folder` one hour
    apart in name order (deterministic, and distinct so a client can sort by it)."""
    base = os.path.join(w.root, folder)
    for number, name in enumerate(sorted(os.listdir(base))):
        path = os.path.join(base, name)
        if os.path.isfile(path):
            stamp = w.clock.now - 86400 + 3600 * number
            os.utime(path, (stamp, stamp))


class PagesGenerator(Generator):
    def __init__(self, dash, base, monkeypatch):
        super().__init__(dash, base, monkeypatch)
        self.cases = Families()

    @contextlib.contextmanager
    def world(self, name, **kwargs):
        with super().world(name, **kwargs) as w:
            pin_datetime(w)
            yield w


def raw_post(w, family, case, path, data, *, content_type='application/json'):
    """POST `data` (text) as it is, so a body that is not JSON can be a case. The request is recorded as that text."""
    response = w.client.post(path, data=data, content_type=content_type)
    return w.case(family, case, response, 'POST', path, data)


def json_of(response):
    return response.get_json()


def dump(value):
    return json.dumps(value, sort_keys=True)


# ------------------------------------------------------------ a populated account
import copy as _copy

import yaml as _yaml

PLAN = {
    'week': {'default': 'farm_day', 'saturday': 'tourney_day'},
    'days': {
        'farm_day': [{'block': 'shards', 'blueprint': 'shards', 'after': '08:00', 'count': 100},
                     {'block': 'coin', 'blueprint': 'farm'}],
        'tourney_day': [{'block': 'tournament', 'blueprint': 'tourney', 'after': '19:00', 'count': 1},
                        {'block': 'shards', 'blueprint': 'shards', 'after': '08:00', 'count': 100},
                        {'block': 'coin', 'blueprint': 'farm'}]}}

POPULATED_PROFILE = {
    'player': {},
    'blueprints': {
        'farm': {'kind': 'coin', 'tier': 1, 'loadout': 'as_is', 'shopping': 'main_list', 'policies': {'gather': 'all_on'}},
        'farm_deep': {'kind': 'coin', 'tier': 14, 'loadout': 'plain', 'restart_via_home': True,
                      'shopping': 'default_sweep', 'policies': {'gather': 'gems_only'}},
        'shards': {'kind': 'shard', 'tier': 18, 'loadout': 'plain', 'count': 100, 'policies': {'gather': 'gems_only'}},
        'tourney': {'kind': 'tournament', 'tier': 14, 'loadout': 'plain', 'gem_entry_max': 0, 'policies': {'gather': 'hands_off'}}},
    'policies': {
        'gather': {
            'all_on': {'flying_gem': True, 'gem_delay_sec': [3, 10], 'ad_gems': True, 'quests_8h': True, 'quest_rewards': True, 'guild': True},
            'gems_only': {'flying_gem': True, 'gem_delay_sec': [3, 10], 'ad_gems': False, 'quests_8h': False, 'quest_rewards': False, 'guild': False},
            'hands_off': {'flying_gem': False, 'gem_delay_sec': [3, 10], 'ad_gems': False, 'quests_8h': False, 'quest_rewards': False, 'guild': False}},
        'shopping_lists': {
            'default_sweep': {'enabled': True, 'directives': [
                {'enabled': True, 'tab': 'defense', 'stats': ['health', 'health_regen'], 'mode': 'best_cost'},
                {'enabled': True, 'tab': 'attack', 'stats': ['damage'], 'mode': 'clicks', 'clicks': 3}]},
            'main_list': {'version': 2, 'enabled': True, 'interval_sec': 30, 'cash_reserve': 500,
                          'queue': [{'id': 'damage', 'enabled': True, 'tap_limit': 3},
                                    {'id': 'health', 'enabled': True, 'tap_limit': 2, 'from_wave': 5}]}},
        'global_behaviors': {'free_store_gems': True, 'daily_missions': False},
        'chores': [{'name': 'quest_scan', 'enabled': True}]},
    'plan': _copy.deepcopy(PLAN)}

LOADOUTS = {'plain': {}}


def populate(w, *, plan=True, mutate=None, config=None):
    """The world's account with several runs, three gathering policies, two shopping lists, a plan and a loadout
    in config.yaml - a profile the real validator accepts (see test_the_populated_profile_is_valid)."""
    profile = _copy.deepcopy(POPULATED_PROFILE)
    if not plan:
        profile.pop('plan')
    if mutate:
        mutate(profile)
    (w.root / 'profiles' / 'mine.yaml').write_text(_yaml.safe_dump(profile, sort_keys=False), encoding='utf-8')

    def configure(cfg):
        cfg['loadouts'] = _copy.deepcopy(LOADOUTS)
        if config:
            config(cfg)
    w.edit_config(configure)
    return profile
