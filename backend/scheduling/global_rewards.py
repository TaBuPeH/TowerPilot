"""Account-scoped reward clocks; independent from per-run gathering policies."""
import datetime
import hashlib
import time

import settings
from scheduling import daystate

KEYS = ('free_store_gems', 'daily_missions', 'event_missions', 'guild_progress')


def enabled(body, name):
    return body.get('global_behaviors', {}).get(name, False) is True


def account_key():
    return 'global_rewards:' + str(settings.instance().get('account') or settings.CONFIG['active_instance'])


def store_due(now=None):
    now = time.time() if now is None else now
    day = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).date()
    key = account_key() + ':store'
    if daystate.get_raw(key) == day.isoformat():
        return False
    # Stable daily random offset survives restarts without moving the deadline.
    seed = hashlib.sha256((key + day.isoformat()).encode()).digest()
    offset = int.from_bytes(seed[:4], 'big') % 1201 - 600
    midnight = datetime.datetime.combine(day, datetime.time(), datetime.timezone.utc).timestamp()
    return now >= midnight + 3600 + offset


def store_claimed():
    daystate.set_raw(account_key() + ':store', datetime.datetime.now(datetime.timezone.utc).date().isoformat())


def check_due(name, now=None):
    now = time.time() if now is None else now
    return now >= float(daystate.get_raw(account_key() + ':' + name + ':next', 0))


def checked(name, now=None):
    now = time.time() if now is None else now
    daystate.set_raw(account_key() + ':' + name + ':next', now + 300)
