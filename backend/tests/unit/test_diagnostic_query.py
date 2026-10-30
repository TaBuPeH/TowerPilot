"""Query real JSONL files without importing or touching emulator code."""
import json

import pytest

from control import diagnostics


def write_events(root, directory, rows):
    path = root / 'logs' / directory / 'events_test.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
    return path


def test_trace_query_joins_request_and_worker_and_preserves_raw_records(tmp_path):
    write_events(tmp_path, '_dashboard', [dict(t=1, kind='request_started', trace_id='trace-one', request_id='request-one')])
    write_events(tmp_path, 'emulator', [dict(t=2, kind='tap', trace_id='trace-one', operation_id='op-one', outcome='refused'),
                                        dict(t=3, kind='tap', trace_id='other', outcome='dispatched')])
    result = diagnostics.query(tmp_path, {'trace_id': 'trace-one'})
    assert [r['kind'] for r in result['events']] == ['request_started', 'tap']
    assert result['events'][1]['operation_id'] == 'op-one'
    assert result['events'][1]['_log']['failure'] is True
    assert result['coverage']['truncated'] is False
    assert result['coverage']['files_scanned'] == 2


def test_failure_filter_includes_silent_domain_failures_and_exact_instance(tmp_path):
    write_events(tmp_path, 'emu', [dict(t=1, kind='claim', confirmed=False),
                                 dict(t=2, kind='ad_gems_window_ended', reason='timeout'),
                                 dict(t=3, kind='claim', confirmed=True)])
    result = diagnostics.query(tmp_path, {'instance': 'emu', 'failures': '1', 'since': '1.5'})
    assert [r['kind'] for r in result['events']] == ['ad_gems_window_ended']
    assert diagnostics.query(tmp_path, {'instance': '../emu'})['events'] == []


def test_partial_and_malformed_lines_are_visible_coverage_gaps(tmp_path):
    path = write_events(tmp_path, 'emu', [dict(t=2, kind='good')])
    with path.open('ab') as stream:
        stream.write(b'bad json\n[]\n{"t":3')
    result = diagnostics.query(tmp_path, {})
    assert len(result['events']) == 1
    assert result['coverage']['malformed_records'] == 2
    assert result['coverage']['truncated'] is True


def test_limits_and_byte_budget_do_not_claim_a_complete_history(tmp_path, monkeypatch):
    write_events(tmp_path, 'emu', [dict(t=i, kind='event', extra='x' * 40) for i in range(10)])
    result = diagnostics.query(tmp_path, {'limit': '2'})
    assert [row['t'] for row in result['events']] == [8, 9]
    assert result['coverage']['truncated'] is True
    monkeypatch.setattr(diagnostics, 'MAX_FILE_BYTES', 200)
    result = diagnostics.query(tmp_path, {})
    assert result['coverage']['bytes_read'] == 200
    assert result['coverage']['truncated'] is True
    assert result['events'][-1]['t'] == 9


@pytest.mark.parametrize('params', [{'since': 'nan'}, {'limit': '0'}, {'limit': '1001'},
                                    {'failures': 'yes'}, {'path': '../config.yaml'}])
def test_invalid_queries_are_rejected(tmp_path, params):
    with pytest.raises(ValueError):
        diagnostics.query(tmp_path, params)


def test_missing_log_directory_is_empty_not_an_error(tmp_path):
    result = diagnostics.query(tmp_path, {})
    assert result['events'] == []
    assert result['coverage']['files_scanned'] == 0


def test_claim_filter_matches_even_when_worker_or_device_attempt_is_inherited(tmp_path):
    write_events(tmp_path, 'emu', [dict(t=1, kind='claim_start', attempt_id='worker-one', claim_attempt_id='claim-one'),
                                 dict(t=2, kind='tap_end', attempt_id='device-one', claim_attempt_id='claim-one'),
                                 dict(t=3, kind='unrelated', attempt_id='worker-one')])
    assert [row['kind'] for row in diagnostics.query(tmp_path, {'attempt_id': 'claim-one'})['events']] == ['claim_start', 'tap_end']
    assert [row['kind'] for row in diagnostics.query(tmp_path, {'attempt_id': 'device-one'})['events']] == ['tap_end']
