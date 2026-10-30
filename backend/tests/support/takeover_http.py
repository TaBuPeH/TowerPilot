"""HTTP scaffolding for the takeover tests and the recorded contract cases: the real dashboard over a temporary root (harvest_http.make_app), attached readiness
as a controllable boundary, and the request a person's dialog would send, built from the real preview. No device, no network."""
from harvest_http import KEY, make_app  # noqa: F401 - re-exported for the tests
from player import readiness

TIER_UNKNOWN = {'value': None, 'provenance': 'unknown'}
ATTESTATION = 'current_battle_takeover'
URL = '/api/takeover/preview'


class Readiness:
    """readiness.check as the takeover calls it (`attached=True`). `missing` is what a blocking gap reports; a test sets it, never the filesystem."""

    def __init__(self):
        self.missing, self.calls = [], []

    def __call__(self, root, cfg, preset, attached=False):
        self.calls.append(dict(attached=attached, preset=preset))
        return {'ready': not self.missing, 'missing': list(self.missing), 'advisory': []}


def install_readiness(monkeypatch):
    fake = Readiness()
    monkeypatch.setattr(readiness, 'check', fake)
    return fake


def preview(app, preset='bp_farm'):
    response = app.client.get(URL, query_string={'preset': preset} if preset else None)
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def attach_for(shown, *, tier=None, **changes):
    """The `attach` object the dialog sends for a preview it just read."""
    battle = shown['current_battle']
    attach = {'mode': 'current_battle', 'policy_revision': shown['run']['policy_revision'],
              'expected_battle': {name: battle[name] for name in ('battle_id', 'generation', 'observed_wave')},
              'tier': tier or dict(TIER_UNKNOWN), 'attestation': ATTESTATION}
    attach.update(changes)
    return attach


def body(app, *, preset='bp_farm', key=None, tier=None, shown=None, attach_changes=None, **changes):
    shown = shown or preview(app, preset)
    result = {'action': 'takeover', 'preset': preset, 'idempotency_key': key or f'take-{next(KEY):06d}',
              'expected_context': dict(shown['context']), 'attach': {**attach_for(shown, tier=tier), **(attach_changes or {})}}
    result.update(changes)
    return result


def post(app, **kwargs):
    return app.client.post('/api/control', json=body(app, **kwargs))


def missing_row(*alternatives):
    return {'alternatives': list(alternatives), 'reasons': ['needed'], 'blocking': True}
