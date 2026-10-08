import asyncio
import json

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.prompts import build_messages, catalog_from_framework, user_record
from backend.provider import sse_objects


def configure(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-never-expose")
    monkeypatch.setenv("OPENROUTER_MODEL", "test/model")
    monkeypatch.setenv("MC_CACHE_MODE", "explicit")


def events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]


def test_three_turns_reuse_canonical_history_and_report_cache(tmp_path, monkeypatch):
    configure(monkeypatch)
    payloads = []

    async def transport(cfg, payload):
        payloads.append(payload)
        # A spontaneous existing channel must be accepted even when not selected.
        for c in '2_Public 1_Result 2_check. 1_= 2_!done 1_5. 1_< 1_4. ':
            yield {"choices": [{"delta": {"content": c}}]}
        yield {"choices": [], "usage": {"prompt_tokens": 2000, "completion_tokens": 80,
                                        "prompt_tokens_details": {"cached_tokens": 1000, "cache_write_tokens": 20}}}
        yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}
        yield {"done": True}

    app = create_app(tmp_path, transport)
    with TestClient(app) as client:
        sid = client.post('/api/sessions', json={}).json()['id']
        for i in range(3):
            response = client.post(f'/api/sessions/{sid}/stream', json={"prompt": f"Question {i}", "channels": ["answer"]})
            output = events(response)
            assert output[-1]['status'] == 'complete'
            assert output[-1]['channels']['answer'] == 'Result = 4.'
            assert output[-1]['usage']['prompt_tokens_details']['cached_tokens'] == 1000
        session = client.get(f'/api/sessions/{sid}').json()
        assert len(session['turns']) == 3
        correction = next(e for e in session['turns'][0]['events'] if e.get('word_count'))
        assert correction['removed'] == ' 5.'
        assert correction['source'] == '1_<'
        assert session['protocol'] == 'MC/2'
        assert 'test-key-never-expose' not in client.get('/api/config').text
        assert 'test-key-never-expose' not in client.get(f'/api/sessions/{sid}/export').text
    second, third = payloads[1]['messages'], payloads[2]['messages']
    assert second[:3] == third[:3]  # Exact old breakpoint and prefix are retained.
    assert second[0] == payloads[0]['messages'][0]
    assert second[1] == payloads[0]['messages'][1]
    canonical = second[2]['content'][0]['text']
    assert 'Result = 4.' in canonical and 'Result = 5.' not in canonical
    assert 'backspace' not in canonical
    assert payloads[0]['session_id'] == payloads[2]['session_id']
    # Database survives creation of another app process.
    with TestClient(create_app(tmp_path, transport)) as reopened:
        assert len(reopened.get(f'/api/sessions/{sid}').json()['turns']) == 3


def test_invalid_or_truncated_stream_is_saved_but_excluded(tmp_path, monkeypatch):
    configure(monkeypatch)

    async def transport(cfg, payload):
        yield {"choices": [{"delta": {"content": '1_Partial not valid '}, "finish_reason": "length"}]}
        yield {"done": True}

    with TestClient(create_app(tmp_path, transport)) as client:
        sid = client.post('/api/sessions', json={}).json()['id']
        output = events(client.post(f'/api/sessions/{sid}/stream', json={"prompt": "test"}))
        assert output[-1]['status'] == 'failed'
        session = client.get(f'/api/sessions/{sid}').json()
        turn = session['turns'][0]
        assert turn['channels']['answer'] == 'Partial'
        assert turn['canonical'] is None
        messages = build_messages(session, [turn], 'next', 'automatic')
        assert [m['role'] for m in messages] == ['system', 'user', 'user']
        assert messages[1]['content'] == turn['user_content']
        assert 'Partial' not in str(messages)


def test_pinned_framework_and_user_preferences_do_not_rewrite_prefix(tmp_path, monkeypatch):
    configure(monkeypatch)
    with TestClient(create_app(tmp_path)) as client:
        s = client.post('/api/sessions', json={}).json()
        assert len(catalog_from_framework(s['framework'])) == 38
        assert len(s['framework_hash']) == 64
        first = user_record('hello', ['answer', 'truth'], {})
        second = user_record('next', ['answer', 'green_it'], {'green_it': 'Peak RAM'})
        turn = dict(status='complete', user_content=first, canonical=json.dumps({'format':'mc-history/1','channels':{'answer':'fixed history'}}))
        a = build_messages(s, [turn], second, 'explicit')
        b = build_messages(s, [turn], 'different request', 'explicit')
        assert a[:-1] == b[:-1]


def test_local_origin_and_validation(tmp_path, monkeypatch):
    configure(monkeypatch)
    with TestClient(create_app(tmp_path)) as client:
        assert client.post('/api/sessions', json={}, headers={'Origin': 'https://example.org'}).status_code == 403
        assert client.get('/', headers={'Host': 'evil.example'}).status_code == 400
        sid = client.post('/api/sessions', json={}).json()['id']
        assert client.post(f'/api/sessions/{sid}/stream', json={'prompt': 'x', 'channels': ['made_up']}).status_code == 422
        assert client.post(f'/api/sessions/{sid}/stream', json={'prompt': '   '}).status_code == 422
        assert client.get('/.env').status_code == 404
        assert client.get('/static/../.env').status_code == 404
        assert client.get('/').status_code == 200


def test_sse_comments_usage_and_multiline_data():
    async def lines():
        for line in [': OPENROUTER PROCESSING', '', 'data: {"choices": [],',
                     'data: "usage": {"prompt_tokens": 3}}', '', 'data: [DONE]', '']:
            yield line

    async def collect():
        return [item async for item in sse_objects(lines())]

    assert asyncio.run(collect()) == [{'choices': [], 'usage': {'prompt_tokens': 3}}, {'done': True}]


def test_cancel_closes_upstream_and_rejects_concurrent_turn(tmp_path, monkeypatch):
    configure(monkeypatch)
    import threading
    started = threading.Event()
    closed = threading.Event()

    async def transport(cfg, payload):
        try:
            yield {"choices": [{"delta": {"content": '1_Partial '}}]}
            started.set()
            await asyncio.sleep(30)
        finally:
            closed.set()

    with TestClient(create_app(tmp_path, transport)) as client:
        sid = client.post('/api/sessions', json={}).json()['id']
        results = []
        worker = threading.Thread(target=lambda: results.append(client.post(f'/api/sessions/{sid}/stream', json={'prompt': 'wait'})))
        worker.start()
        assert started.wait(5)
        assert client.post(f'/api/sessions/{sid}/stream', json={'prompt': 'conflict'}).status_code == 409
        assert client.post(f'/api/sessions/{sid}/cancel').json()['stopping']
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert closed.is_set()
        assert events(results[0])[-1]['status'] == 'stopped'
        assert client.get(f'/api/sessions/{sid}').json()['turns'][0]['canonical'] is None


def test_abrupt_eof_and_protocol_errors_do_not_commit(tmp_path, monkeypatch):
    configure(monkeypatch)

    async def transport(cfg, payload):
        yield {"choices": [{"delta": {"content": '1_Not 1_committed '}, 'finish_reason': 'stop'}]}

    with TestClient(create_app(tmp_path, transport)) as client:
        sid = client.post('/api/sessions', json={}).json()['id']
        output = events(client.post(f'/api/sessions/{sid}/stream', json={'prompt': 'test'}))
        assert output[-1]['status'] == 'failed'
        assert 'unexpectedly' in next(e['message'] for e in output if e['type'] == 'error')


def test_legacy_chat_can_continue_and_upgrade_without_mutating_records(tmp_path, monkeypatch):
    configure(monkeypatch)
    legacy = '# MC Framework · 1.0\n| answer | Answer | Core | Reply. |\n| truth | Truth | Core | Check. |\n'
    payloads = []

    async def transport(cfg, payload):
        payloads.append(payload)
        framework = payload['messages'][0]['content'][0]['text']
        content = ('1_Now 2_Checked. 1_using 2_!done 1_words.' if 'protocol: MC/2' in framework
                   else '{"channel":"answer","text":"Original answer."}\n')
        yield {'choices': [{'delta': {'content': content}, 'finish_reason': 'stop'}]}
        yield {'done': True}

    app = create_app(tmp_path, transport)
    with TestClient(app) as client:
        old = app.state.store.create_session('test/model', 'live', legacy, 'old-hash')
        sid = old['id']
        assert events(client.post(f'/api/sessions/{sid}/stream', json={'prompt': 'First'}))[-1]['status'] == 'complete'
        before = client.get(f'/api/sessions/{sid}').json()
        upgraded = client.post(f'/api/sessions/{sid}/upgrade').json()
        assert upgraded['id'] != sid
        new_id = upgraded['id']
        result = client.get(f'/api/sessions/{new_id}').json()
        assert result['protocol'] == 'MC/2'
        assert result['model'] == old['model']
        copied = result['turns'][0]
        original = before['turns'][0]
        for key in ('raw', 'events', 'canonical', 'user_content', 'status', 'usage'):
            assert copied[key] == original[key]
        assert copied['source_turn_id'] == original['id']
        assert copied['protocol'] == 'MC/1'
        assert client.get(f'/api/sessions/{sid}').json() == before
        assert events(client.post(f'/api/sessions/{new_id}/stream', json={'prompt': 'Second'}))[-1]['status'] == 'complete'
        assert 'Original answer.' in payloads[-1]['messages'][2]['content'][0]['text']
        exported = client.get(f'/api/sessions/{new_id}/export').json()
        assert exported['turns'][-1]['raw'] == '1_Now 2_Checked. 1_using 2_!done 1_words.'
        assert exported['turns'][-1]['channels']['answer'] == 'Now using words.'
        assert exported['session']['protocol'] == 'MC/2'
        # Already-upgraded calls do not create redundant copies.
        assert client.post(f'/api/sessions/{new_id}/upgrade').json()['id'] == new_id


def test_numbered_demo_uses_production_parser(tmp_path, monkeypatch):
    configure(monkeypatch)
    async def no_wait(_):
        pass
    monkeypatch.setattr('backend.app.asyncio.sleep', no_wait)
    with TestClient(create_app(tmp_path)) as client:
        sid = client.post('/api/sessions', json={'mode':'demo'}).json()['id']
        output = events(client.post(f'/api/sessions/{sid}/stream', json={'prompt':'Demo', 'channels':['answer','code']}))
        assert output[-1]['status'] == 'complete'
        assert '2 + 2 = 4.' in output[-1]['channels']['answer']
        assert any(e.get('source') == '1_<' for e in output)
        assert 'code' in output[-1]['channels']
        saved = client.get(f'/api/sessions/{sid}').json()['turns'][0]
        assert not saved['raw'].startswith('{')
        assert saved['canonical'] is not None
