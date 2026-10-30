"""Dashboard-side access to the action history: the refresher's settlement and the read route's query.

Plain values in and out; nothing here builds an HTTP response. The query reads through `operations.readonly`, so a
GET on a checkout with no ledger (or none of this schema) answers an empty history and creates nothing.
"""
from control import operations
from towerpilot_workflows import action_contract, action_history
from towerpilot_workflows.action_contract import SCHEMA_VERSION
from towerpilot_workflows.actions import Actions, SchemaTooNew

DEFAULT_LIMIT, MAX_LIMIT = 50, 500
MAX_REFUSED_PAGES = 10          # pages `for_battle` reads past ones the validator refused whole, so older history behind them is not hidden


def settle(root, event=None):
    """The refresher's settlement: close the open rows whose host is provably gone. Skipped (and nothing created)
    until some execution has ever been recorded here, and never a change to a ledger a newer build wrote. A damaged
    row is skipped and reported through `event(kind, **fields)`; it is never an error, and the caller still has to
    contain whatever else can go wrong (a locked or unreadable ledger raises)."""
    path = operations.ledger_path(root)
    if Actions.read_meta(path) is None:
        return []
    store = Actions(path)
    try:
        settled = store.settle_open()
    except SchemaTooNew:
        return []
    if store.unusable and event is not None:
        event('action_history_unusable', rows=store.unusable, where='settlement')
    return settled


def _limit(value):
    if value is None:
        return DEFAULT_LIMIT
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError('limit must be a whole number') from None
    if not 1 <= number <= MAX_LIMIT:
        raise ValueError(f'limit must be between 1 and {MAX_LIMIT}')
    return number


def _before(value):
    """The cursor a previous page handed out (or a bare start time); anything else is a ValueError, a 400."""
    if value is None:
        return None
    Actions.parse_cursor(value)
    return str(value)


def _newer(meta):
    return meta is not None and meta['schema_version'] is not None and meta['schema_version'] > SCHEMA_VERSION


def _labelled(rows):
    """Each row with the label its action has in the catalog in force (None for an action the catalog does not hold), so a page names an action
    without a table of its own. Added when a row is read; nothing is stored."""
    catalog = action_contract.catalog()
    return [dict(row, label=getattr(catalog.find(row.get('action')), 'label', None)) for row in rows]


def for_battle(root, battle, *, limit=DEFAULT_LIMIT, action=None):
    """The executions that ran under one battle, newest first (only those of `action` when one is named).

    The rows decide. A battle that has executions was recorded, whenever it was created: the first battle a driver
    tracks is created a moment BEFORE its action store, and must not hide what it recorded. Only a battle with no
    execution that was created before the store existed, or on a ledger that never had one, is `items` None with
    its reason: it has no history to show, which is not a battle where nothing ran. A ledger a newer build wrote is
    not interpreted. (Rows of an adopted battle carry their own `coverage`, `since_adoption`.)

    Rows are validated before they are shown (`action_history.validate_history`): a row that cannot belong to this battle (another account, battle or generation, a newer schema, an
    impossible duration) and a damaged body are left out, as the generic read leaves out a damaged one. The wire shape is unchanged: no key is added for them."""
    path = operations.ledger_path(root)
    meta = Actions.read_meta(path)
    if _newer(meta):
        return dict(items=None, reason='newer_schema', truncated=False)
    items, truncated, cursor, seen = [], False, None, False
    if meta is not None:
        before = None
        for _ in range(MAX_REFUSED_PAGES):
            rows, truncated, damaged, cursor = Actions.read_page(path, device=battle['context']['device_key'], battle_id=battle['id'], action=action, before=before, limit=limit)
            # A damaged row is still a row this battle recorded: the battle has history (items may be empty), it was
            # not recorded before the store existed.
            seen = seen or bool(rows or damaged)
            items = action_history.validate_history(battle, rows, ledger=meta)['items'] if rows else []
            if items or not truncated:
                break
            before = cursor          # this page held nothing the battle can show (foreign or damaged rows): the page behind it may
    if seen:
        block = dict(items=_labelled(items), reason=None, truncated=truncated)
        if truncated:        # the rest is one generic read away: GET /api/actions/executions?battle_id=<id>&before=<next_before>
            block['next_before'] = cursor
        return block
    reason = action_history.unrecorded_reason(battle, meta)
    if reason:
        return dict(items=None, reason=reason, truncated=False)
    return dict(items=[], reason=None, truncated=False)


def executions(root, context, *, action=None, instance=None, battle_id=None, before=None, limit=None):
    """The executions of the selected emulator and account, newest first. A row of another account on the same
    device is never returned, and the account is part of the query, not a filter on its page: a page is a page of this
    account's executions. `next_before` is a cursor in the order (start time, id) that continues the page exactly where
    it ended; `truncated` says there is more. `unusable` counts damaged rows that were skipped."""
    limit, before = _limit(limit), _before(before)
    path = operations.ledger_path(root)
    meta = Actions.read_meta(path)
    if _newer(meta):
        # written by a newer build: this one does not interpret it (status stays readable, controls stay off)
        return dict(schema_version=SCHEMA_VERSION, ledger_schema_version=meta['schema_version'], ledger_id=meta['ledger_id'], context=context,
                    executions=[], truncated=False, next_before=None, unusable=0, reason='newer_schema')
    rows, truncated, unusable, cursor = Actions.read_page(path, device=context['device_key'], action=action, instance=instance,
                                                          battle_id=battle_id, account=context.get('account'), before=before, limit=limit)
    return dict(schema_version=SCHEMA_VERSION, ledger_id=None if meta is None else meta['ledger_id'], context=context,
                executions=_labelled(rows), truncated=truncated, next_before=cursor, unusable=unusable)