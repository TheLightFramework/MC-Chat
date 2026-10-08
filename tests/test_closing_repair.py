import json
import asyncio
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.protocol import ChannelParser
from test_app import configure, events


WIRE = (Path(__file__).parent / 'fixtures/missing_terminal_close.mc').read_text().strip()
MAPPING = {1: 'answer', 2: 'truth', 3: 'uncertainty', 14: 'correctness',
           22: 'sources', 23: 'evidence', 25: 'counterexamples'}
SELECTED = list(MAPPING.values())


def parse(wire):
    parser = ChannelParser(MAPPING, SELECTED)
    edits = parser.feed(wire) + parser.finish()
    return parser, edits


def test_export_16_repair_preserves_strict_audit_and_text_at_every_packet_split():
    reference, original_events = parse(WIRE)
    canonical = reference.canonical()
    assert reference.rounds.violations == 2
    assert reference.rounds.slots == 77
    expected = reference.repair_missing_close()
    assert len(expected) == 1 and expected[0]['inferred_source'] == '3_!done'
    assert reference.canonical() == canonical
    assert reference.statuses['uncertainty'] == 'active'
    summary = reference.interleaving_summary()
    assert summary['status'] == 'repaired' and summary['original_status'] == 'failed'
    assert summary['violations'] == 2
    assert summary['closing_repair']['validated']['violations'] == 0
    assert summary['closing_repair']['validated']['slots'] == 78
    assert reference.repair_missing_close() == [] and reference.repairs == 1
    for split in range(len(WIRE) + 1):
        parser = ChannelParser(MAPPING, SELECTED)
        output = parser.feed(WIRE[:split]) + parser.feed(WIRE[split:]) + parser.finish()
        assert output == original_events
        assert parser.repair_missing_close() == expected
        assert parser.canonical() == canonical
        assert parser.interleaving_summary() == summary


@pytest.mark.parametrize('wire', [
    WIRE.replace('3_verification.', '3_verification'),  # Unfinished-looking text.
    WIRE.replace('22_sources.', '22_more 22_sources.'),  # Broader rotation defect.
    WIRE.replace('23_!done', ''),  # Two missing closes.
    WIRE.replace('1_MC', 'unprefixed'),
    WIRE.replace('2_Checked', r'2_Checked\sagain'),  # Multiword payload.
    WIRE.replace('3_How', '3_!skip').replace('3_the', '3_!skip')
        .replace('3_backend', '3_!skip').replace('3_repairs', '3_!skip')
        .replace('3_missing', '3_!skip').replace('3_underscores', '3_!skip')
        .replace('3_is', '3_!skip').replace('3_outside', '3_!skip')
        .replace('3_my', '3_!skip').replace('3_verification.', '3_!skip'),
    WIRE + ' 3_Rejoined.',  # Cannot infer an earlier close if it speaks again.
    '1_Whole 1_sentence. 2_Other 2_sentence. 1_!done',
])
def test_repair_does_not_tolerate_unrelated_or_ambiguous_errors(wire):
    parser, _ = parse(wire)
    assert parser.repair_missing_close() == []
    assert parser.closing_repair is None and parser.repairs == 0


def test_repair_is_not_eager_and_legacy_terminal_mode_stays_strict():
    parser = ChannelParser(MAPPING, SELECTED)
    parser.feed(WIRE + ' ')
    assert parser.repair_missing_close() == []  # Provider has not ended yet.
    parser.feed('3_More. 3_!done')
    parser.finish()
    assert parser.repair_missing_close() == []
    legacy = ChannelParser(MAPPING, SELECTED, terminal_channel='self_prompt')
    legacy.feed(WIRE)
    legacy.finish()
    assert legacy.repair_missing_close() == []


def test_inference_at_eof_can_complete_a_short_closing_tail():
    parser = ChannelParser({1: 'answer', 2: 'truth'}, ['answer', 'truth'])
    parser.feed('1_Hello 2_All 1_there. 2_checked. 1_!done')
    parser.finish()
    repaired = parser.repair_missing_close()
    assert repaired[0]['before_unit'] == 6
    assert repaired[0]['inferred_source'] == '2_!done'
    assert parser.rounds.violations == 1


@pytest.mark.parametrize('finish,marker,expected_calls', [
    ('stop', True, 2), ('length', True, 1), ('content_filter', True, 1),
    ('error', True, 1), ('stop', False, 1),
])
def test_app_gates_recovery_on_normal_completion_and_persists_audit(
        tmp_path, monkeypatch, finish, marker, expected_calls):
    configure(monkeypatch)
    calls = []

    async def transport(cfg, payload):
        calls.append(payload)
        wire = WIRE if len(calls) == 1 else '# Consolidated answer\n\nReady.'
        yield {'provider': 'Test Provider', 'choices': [{'delta': {'content': wire},
                                                       'finish_reason': finish}]}
        if marker:
            yield {'done': True}

    with TestClient(create_app(tmp_path, transport)) as client:
        sid = client.post('/api/sessions', json={}).json()['id']
        output = events(client.post(f'/api/sessions/{sid}/stream', json={
            'prompt': 'Explain the protocol.', 'channels': SELECTED, 'two_pass': True}))
        assert len(calls) == expected_calls
        turn = client.get(f'/api/sessions/{sid}').json()['turns'][0]
        assert turn['raw'] == WIRE
        strict, _ = parse(WIRE)
        assert turn['channels'] == strict.channels
        assert turn['statuses'] == strict.statuses
        assert turn['interleaving']['violations'] == 2
        repairs = [e for e in turn['events'] if e['type'] == 'repair']
        if expected_calls == 2:
            assert output[-1]['status'] == 'complete' and len(repairs) == 1
            assert turn['interleaving']['status'] == 'repaired'
            assert turn['consolidation']['status'] == 'complete'
            assert json.loads(turn['canonical'])['channels'] == strict.channels
            assert any(e['type'] == 'repair' for e in output)
            exported = client.get(f'/api/sessions/{sid}/export').json()['turns'][0]
            assert exported['interleaving'] == turn['interleaving']
            assert exported['raw'] == WIRE
        else:
            assert output[-1]['status'] == 'failed' and repairs == []
            assert turn['canonical'] is None and turn['repairs'] == 0
            assert turn['consolidation']['status'] == 'skipped'


def test_cancelled_repairable_output_is_not_repaired_or_consolidated(tmp_path, monkeypatch):
    configure(monkeypatch)
    started = threading.Event()
    calls = []

    async def transport(cfg, payload):
        calls.append(payload)
        yield {'choices': [{'delta': {'content': WIRE + ' '}}]}
        started.set()
        await asyncio.sleep(30)

    with TestClient(create_app(tmp_path, transport)) as client:
        sid = client.post('/api/sessions', json={}).json()['id']
        results = []
        worker = threading.Thread(target=lambda: results.append(client.post(
            f'/api/sessions/{sid}/stream', json={
                'prompt': 'Test', 'channels': SELECTED, 'two_pass': True})))
        worker.start()
        try:
            assert started.wait(5)
        finally:
            client.post(f'/api/sessions/{sid}/cancel')
            worker.join(timeout=5)
        assert not worker.is_alive()
        assert events(results[0])[-1]['status'] == 'stopped'
        turn = client.get(f'/api/sessions/{sid}').json()['turns'][0]
        assert len(calls) == 1 and turn['repairs'] == 0
        assert turn['canonical'] is None
        assert not any(e['type'] == 'repair' for e in turn['events'])


def test_unfilled_selected_channel_cannot_be_repaired(tmp_path, monkeypatch):
    configure(monkeypatch)
    calls = []

    async def transport(cfg, payload):
        calls.append(payload)
        yield {'choices': [{'delta': {'content': WIRE.replace('1_MC', '33_!done 1_MC')},
                            'finish_reason': 'stop'}]}
        yield {'done': True}

    with TestClient(create_app(tmp_path, transport)) as client:
        sid = client.post('/api/sessions', json={}).json()['id']
        output = events(client.post(f'/api/sessions/{sid}/stream', json={
            'prompt': 'Test', 'channels': SELECTED + ['humor'], 'two_pass': True}))
        assert output[-1]['status'] == 'failed' and len(calls) == 1
        assert output[-1]['repairs'] == 0
        assert output[-1]['participation']['empty_channels'] == ['humor']
