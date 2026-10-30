"""The published shapes the action module must not change (roadmap item 6): `capabilities`, `runtime.routines`,
the collection evidence, and the Dashboard route table, captured on the unchanged tree into
fixtures/action_golden_v1.json (`baseline`). Replaying them after the migration must give the same bytes; the one
deliberate difference is the route list, which may differ from the baseline only by ADDED_ROUTES."""
import action_golden as golden

# The routes added on purpose: item 6's one generic read route (a new action adds none) and item 3's read-only takeover preview (T3c).
ADDED_ROUTES = ['/api/actions/executions', '/api/takeover/preview']


def test_capabilities_and_runtime_routines_equal_the_captured_snapshots(tmp_path, dash):
    captured = golden.load()['baseline']
    current = golden.roundtrip(golden.baseline_snapshots(tmp_path, dash))
    for name in ('capabilities_full', 'capabilities_gems_only', 'runtime_idle', 'runtime_not_visible', 'routine_names'):
        assert current[name] == captured[name], name


def test_the_route_table_differs_from_the_baseline_only_by_the_declared_additions(tmp_path, dash):
    captured = golden.load()['baseline']['routes']
    current = golden.roundtrip(golden.baseline_snapshots(tmp_path, dash))['routes']
    removed = [route for route in captured if route not in current]
    added = [route for route in current if route not in captured]
    assert removed == []
    assert [route[0] for route in added] == ADDED_ROUTES
