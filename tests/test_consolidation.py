import asyncio
import json
import threading

import pytest
from fastapi.testclient import TestClient

from backend.app import ROOT, create_app
from backend.consolidation import combined_usage
from backend.prompts import build_messages
from backend.protocol import ChannelParser
from test_app import configure, events


WIRE = r'1_Findings 2_Checked 1_!done 2_!done 39_~\s\sWrite 39_a 39_draft. 39_<_1_final. 39_~\n\n 39_~Keep 39_café. 39_!done'
SELF_PROMPT = '  Write a final.\n\nKeep café.'
FINAL = '# Final answer\n\n**Checked.**\n\n```python\nprint(2 + 2)\n```\n'


@pytest.fixture(autouse=True)
def legacy_self_prompt_profile(monkeypatch):
    framework = (ROOT/'tests/fixtures/framework_2_4.md').read_text(encoding='utf-8')
    monkeypatch.setattr('backend.app.read_framework', lambda: framework)


def is_final(payload):
    return 'same assistant producing the final answer' in str(payload['messages'][0]['content'])


async def packets(text, reason='stop', done=True, usage=None):
    # Character chunks exercise split prefixes, Unicode, escapes and Markdown fences.
    for char in text:
        yield {'choices': [{'delta': {'content': char}}]}
    yield {'choices': [{'delta': {}, 'finish_reason': reason}]}
    if usage is not None:
        yield {'usage': usage}
    if done:
        yield {'done': True}


def test_exact_self_prompt_two_calls_streaming_history_and_usage(tmp_path, monkeypatch):
    configure(monkeypatch)
    sent = []
    async def transport(cfg, payload):
        sent.append(payload)
        final = is_final(payload)
        usage = {'prompt_tokens': 20 if final else 10, 'completion_tokens': 8 if final else 5,
                 'cost': .002 if final else .001, 'completion_tokens_details': {'reasoning_tokens': 3},
                 'prompt_tokens_details': {'cached_tokens': 4}}
        async for event in packets(FINAL if final else WIRE, usage=usage):
            yield event
    with TestClient(create_app(tmp_path, transport)) as client:
        sid = client.post('/api/sessions', json={}).json()['id']
        for prompt in ('My original request', 'Continue with details'):
            output = events(client.post(f'/api/sessions/{sid}/stream', json={'prompt': prompt, 'two_pass': True}))
            assert output[-1]['status'] == 'complete'
            assert output[-1]['consolidation']['prompt'] == SELF_PROMPT
            assert ''.join(e['text'] for e in output if e['type'] == 'final_delta') == FINAL
            assert next(e for e in output if e['type'] == 'final_start')['prompt'] == SELF_PROMPT
            assert output[-1]['total_usage']['prompt_tokens'] == 30
            assert output[-1]['total_usage']['cost'] == .003
            assert output[-1]['total_usage']['completion_tokens_details']['reasoning_tokens'] == 6
        exported = client.get(f'/api/sessions/{sid}/export').json()
        first = exported['turns'][0]
        assert first['consolidation']['raw'] == FINAL
        assert first['consolidation']['status'] == first['exploration_status'] == 'complete'
        assert first['raw'] == WIRE
        assert first['usage']['prompt_tokens'] == 10  # Original per-pass accounting retained.
        assert json.loads(first['canonical'])['final_answer'] == FINAL
    assert len(sent) == 4
    for payload in (sent[1], sent[3]):
        assert payload['messages'][-1] == {'role': 'user', 'content': SELF_PROMPT}
        system = payload['messages'][0]['content'][0]['text']
        assert 'ordinary Markdown' in system and 'protocol: MC/2' not in system
        assert 'output_contract' not in str(payload['messages'])
        assert 'self_prompt:' not in payload['messages'][-2]['content']
    current = json.loads(sent[0]['messages'][-1]['content'])
    assert current['round_channels'] == [1, 2]
    assert 'self_prompt' in current['preferred_channels'] and current['self_prompt_required']
    assert 'Consolidated final answer' in str(sent[2]['messages'])
    previous_final = next(m for m in sent[3]['messages'][:-3] if m['role'] == 'assistant')
    assert previous_final['content'][0]['text'] == FINAL
    for key in ('model', 'temperature', 'reasoning', 'max_tokens'):
        assert sent[0][key] == sent[1][key]


@pytest.mark.parametrize('wire', [
    '1_A 2_B 1_!done 2_!done',  # Missing self prompt.
    '1_A 2_B 39_Too 39_early 1_!done 2_!done 39_!done',
    '1_A 2_B 1_!done 2_!done 39_Write',  # Missing terminal close.
    '1_A 2_B 1_!done 2_!done 39_Write 1_Reopened 39_!done 1_!done',
    '1_A 2_B 1_!done 2_!done 39_!done',  # Empty self prompt.
    '1_A 2_B unprefixed 1_!done 2_!done 39_Write 39_!done',
])
def test_invalid_exploration_never_triggers_second_call(tmp_path, monkeypatch, wire):
    configure(monkeypatch)
    calls = []
    async def transport(cfg, payload):
        calls.append(payload)
        async for event in packets(wire):
            yield event
    with TestClient(create_app(tmp_path, transport)) as client:
        sid = client.post('/api/sessions', json={}).json()['id']
        result = events(client.post(f'/api/sessions/{sid}/stream', json={'prompt': 'Test', 'two_pass': True}))
        assert result[-1]['status'] == 'failed'
        assert result[-1]['consolidation']['status'] == 'skipped'
        assert result[-1]['consolidation']['generation']['sent_to_provider'] is False
        assert len(calls) == 1
        assert not any(e['type'] == 'final_start' for e in result)


@pytest.mark.parametrize('text,reason,done', [('', 'stop', True), ('Partial', 'length', True), ('Partial', 'stop', False)])
def test_failed_final_stays_local_preserving_user_request(tmp_path, monkeypatch, text, reason, done):
    configure(monkeypatch)
    async def transport(cfg, payload):
        final = is_final(payload)
        async for event in packets(text if final else WIRE, reason if final else 'stop', done if final else True):
            yield event
    with TestClient(create_app(tmp_path, transport)) as client:
        sid = client.post('/api/sessions', json={}).json()['id']
        result = events(client.post(f'/api/sessions/{sid}/stream', json={'prompt': 'Remember this', 'two_pass': True}))
        assert result[-1]['status'] == 'failed'
        assert result[-1]['exploration_status'] == 'complete'
        assert result[-1]['consolidation']['status'] == 'failed'
        assert result[-1]['consolidation']['raw'] == text
        session = client.get(f'/api/sessions/{sid}').json()
        turn = session['turns'][0]
        assert turn['canonical'] is None
        messages = build_messages(session, [turn], 'Next', 'automatic')
        assert [m['role'] for m in messages] == ['system', 'user', 'user']
        assert json.loads(messages[1]['content'])['request'] == 'Remember this'


@pytest.mark.parametrize('cancel_phase', [1, 2])
def test_cancel_during_either_pass_closes_provider(tmp_path, monkeypatch, cancel_phase):
    configure(monkeypatch)
    started, closed = threading.Event(), threading.Event()
    calls = []
    async def transport(cfg, payload):
        calls.append(payload)
        if len(calls) == cancel_phase:
            try:
                yield {'choices': [{'delta': {'content': 'Partial' if cancel_phase == 2 else '1_Partial '}}]}
                started.set()
                await asyncio.sleep(30)
            finally:
                closed.set()
        else:
            async for event in packets(WIRE):
                yield event
    with TestClient(create_app(tmp_path, transport)) as client:
        sid = client.post('/api/sessions', json={}).json()['id']
        responses = []
        worker = threading.Thread(target=lambda: responses.append(client.post(f'/api/sessions/{sid}/stream', json={'prompt': 'Wait', 'two_pass': True})))
        worker.start()
        assert started.wait(5)
        assert client.post(f'/api/sessions/{sid}/stream', json={'prompt': 'Concurrent'}).status_code == 409
        client.post(f'/api/sessions/{sid}/cancel')
        worker.join(5)
        assert not worker.is_alive() and closed.is_set()
        assert len(calls) == cancel_phase
        turn = client.get(f'/api/sessions/{sid}').json()['turns'][0]
        assert turn['status'] == 'stopped' and turn['canonical'] is None
        assert turn['consolidation']['status'] == ('stopped' if cancel_phase == 2 else 'skipped')


def test_terminal_channel_corrections_and_duplicate_close_are_packet_independent():
    wire = WIRE + ' 39_!done'
    for split in range(len(wire)+1):
        parser = ChannelParser({1:'answer',2:'truth',39:'self_prompt'}, ['answer','truth'], terminal_channel='self_prompt')
        parser.feed(wire[:split]);parser.feed(wire[split:]);parser.finish()
        assert not parser.errors and not parser.rounds.violations
        assert parser.channels['self_prompt'] == SELF_PROMPT


def test_solo_code_layout_is_allowed_only_after_other_channels_finish():
    good = r'1_Plan 2_Check 1_!skip 2_!done 1_~\ndef\sadd(a,\sb): 1_!done 39_~Write\sa\sfinal. 39_!done'
    parser = ChannelParser({1:'answer',2:'truth',39:'self_prompt'}, ['answer','truth'], terminal_channel='self_prompt')
    parser.feed(good);parser.finish()
    assert not parser.errors and not parser.rounds.violations
    assert parser.channels['self_prompt'] == 'Write a final.'
    early = r'1_~Two\swords 2_Check 1_!done 2_!done 39_Write 39_!done'
    parser = ChannelParser({1:'answer',2:'truth',39:'self_prompt'}, ['answer','truth'], terminal_channel='self_prompt')
    parser.feed(early);parser.finish()
    assert parser.rounds.violations == 1


def test_two_pass_demo_makes_no_provider_calls(tmp_path, monkeypatch):
    configure(monkeypatch)
    async def no_wait(_): pass
    monkeypatch.setattr('backend.app.asyncio.sleep', no_wait)
    async def forbidden(cfg, payload):
        raise AssertionError('Demo attempted a provider call')
        yield
    with TestClient(create_app(tmp_path, forbidden)) as client:
        sid = client.post('/api/sessions', json={'mode':'demo'}).json()['id']
        result = events(client.post(f'/api/sessions/{sid}/stream', json={'prompt':'Demo','two_pass':True}))[-1]
        assert result['status'] == 'complete'
        assert '```python' in result['consolidation']['raw']
        assert result['consolidation']['generation']['sent_to_provider'] is False


def test_old_chat_requires_upgrade_and_single_pass_remains_available(tmp_path, monkeypatch):
    configure(monkeypatch)
    app = create_app(tmp_path)
    old = (ROOT/'tests/fixtures/framework_2_4.md').read_text(encoding='utf-8').replace('consolidation: self-prompt/1\n','')
    session = app.state.store.create_session('test/model','live',old,'old-hash')
    with TestClient(app) as client:
        assert client.post(f'/api/sessions/{session["id"]}/stream', json={'prompt':'Test','two_pass':True}).status_code == 422
        assert client.get(f'/api/sessions/{session["id"]}').json()['supports_two_pass'] is False


def test_partial_usage_does_not_invent_missing_cost_or_reasoning():
    result = combined_usage({'prompt_tokens':10,'cost':.1}, {'prompt_tokens':20})
    assert result['prompt_tokens'] == 30 and 'cost' not in result
    assert result['reported_passes'] == 2
    partial = combined_usage({'prompt_tokens':10}, None)
    assert partial['reported_passes'] == 1 and partial['expected_passes'] == 2
    assert combined_usage(None, None) is None
