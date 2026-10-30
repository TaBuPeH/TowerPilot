"""Allowlisted, pure battle control patch validation and immutable merging."""
import copy
import math
from collections.abc import Mapping

from towerpilot_workflows.action_contract import catalog, routine_key_valid, split_key

# The controls of every migrated action are generated from its catalog entry, so a range exists once. The four
# gates below have no catalog entry yet (they are not timed actions of the pilot); each moves with its migration.
_GATES = {'rewards': {'enabled': bool}, 'abilities': {'enabled': bool}, 'rescue': {'enabled': bool}, 'repeat': {'enabled': bool}}


class _Fields(Mapping):
    """`{routine: {field: bool | (min, max)}}`: the controls of the active catalog's actions plus the gates not migrated
    yet, read at call time so an extension registered in a catalog copy is validated like any pilot action. A key naming one
    of several configured instances (`<action>@<instance>`) resolves to its action's fields; iteration lists the actions."""

    @staticmethod
    def _table():
        return {**catalog().control_fields(), **_GATES}

    def __getitem__(self, routine):
        table = self._table()
        if routine in table:
            return table[routine]
        if routine_key_valid(routine) and split_key(routine)[1] is not None and split_key(routine)[0] in catalog().control_fields():
            return table[split_key(routine)[0]]
        raise KeyError(routine)

    def __iter__(self):
        return iter(self._table())

    def __len__(self):
        return len(self._table())


FIELDS = _Fields()

# name -> callable(value) that raises ValueError for a document the action refuses. The document's owner registers it.
_DOCUMENT_VALIDATORS = {}


def register_document_validator(name, validator):
    _DOCUMENT_VALIDATORS[name] = validator


def _validate_shopping_plan(plan):
    from towerpilot_workflows.shopping_plan import normalize_plan
    normalize_plan(plan)


register_document_validator('shopping_plan', _validate_shopping_plan)


def documents() -> frozenset:
    """The whole-object controls of the active catalog's actions: replaced wholesale, never flattened or merged."""
    return catalog().documents()


def flatten(value: dict, prefix: str = '', _documents=None) -> dict:
    docs = documents() if _documents is None else _documents
    result = {}
    for key, item in value.items():
        path = f'{prefix}.{key}' if prefix else key
        if isinstance(item, dict) and path not in docs:
            result.update(flatten(item, path, docs))
        else:
            result[path] = item
    return result


def validate_patch(patch: dict) -> dict:
    """Return an independent allowlisted patch, rejecting coercion and NaN."""
    docs = documents()
    if not isinstance(patch, dict) or set(patch) - {'routines', *docs}:
        raise ValueError('Unknown control patch fields')
    for name in sorted(docs):
        if patch.get(name) is not None:
            validator = _DOCUMENT_VALIDATORS.get(name)
            if validator is None:
                raise ValueError(f'No validator for control: {name}')
            validator(patch[name])
    routines = patch.get('routines', {})
    if not isinstance(routines, dict):
        raise ValueError('routines must be a mapping')
    for routine, values in routines.items():
        if routine not in FIELDS or not isinstance(values, dict):
            raise ValueError(f'Unknown routine or invalid mapping: {routine}')
        for field, value in values.items():
            spec = FIELDS[routine].get(field)
            if spec is None:
                raise ValueError(f'Unknown control: routines.{routine}.{field}')
            if value is None:
                continue
            valid = (type(value) is bool if spec is bool else
                     type(value) in (int, float) and math.isfinite(value)
                     and spec[0] <= value <= spec[1])
            if not valid:
                raise ValueError(f'Invalid value: routines.{routine}.{field}')
    if not flatten(patch, '', docs):
        raise ValueError('Empty control patch')
    return copy.deepcopy(patch)


def merge_patch(base: dict, overrides: dict, _documents=None) -> dict:
    """Apply sparse overrides to an independent copy; null inherits base."""
    docs = documents() if _documents is None else _documents
    out = copy.deepcopy(base)
    for key, value in overrides.items():
        if value is None:
            continue
        if key in docs:
            out[key] = copy.deepcopy(value)
        elif isinstance(value, dict):
            out[key] = merge_patch(out.get(key, {}), value, docs)
        else:
            out[key] = copy.deepcopy(value)
    return out