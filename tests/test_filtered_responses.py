import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.prompts import build_messages
from test_app import configure, events


@pytest.mark.parametrize('filtered_pass', [1, 2])
def test_content_filter_is_reported_without_retry_history_commit_or_pin_renewal(tmp_path, monkeypatch, filtered_pass):
    configure(monkeypatch)
    calls = []
    route_before_filter = []
    async def transport(cfg, payload):
        calls.append(payload)
        if len(calls) == filtered_pass:
            route_before_filter.append(app.state.store.route(sid))
            yield {'provider': 'Test Provider', 'choices': [{'delta': {'content': ''},
                    'finish_reason': 'content_filter', 'native_finish_reason': 'refusal'}]}
        else:
            yield {'provider': 'Test Provider', 'choices': [{'delta': {
                'content': '1_Ready 2_Checked 1_!done 2_!done'}, 'finish_reason': 'stop'}]}
        yield {'done': True}
    app = create_app(tmp_path, transport)
    with TestClient(app) as client:
        sid = client.post('/api/sessions', json={}).json()['id']
        output = events(client.post(f'/api/sessions/{sid}/stream', json={'prompt': 'Explain the supplied framework', 'two_pass': True}))
        assert output[-1]['status'] == 'failed' and len(calls) == filtered_pass
        session = client.get(f'/api/sessions/{sid}').json()
        turn = session['turns'][0]
        assert turn['canonical'] is None
        assert turn['consolidation']['status'] == ('skipped' if filtered_pass == 1 else 'failed')
        filtered = turn if filtered_pass == 1 else turn['consolidation']
        assert filtered['finish_reason'] == 'content_filter'
        assert filtered['native_finish_reason'] == 'refusal'
        assert 'content filter' in turn['error']
        assert 'does not address this stop' in turn['error']
        assert app.state.store.route(sid) == route_before_filter[0]
        if filtered_pass == 1:
            assert app.state.store.route(sid)['provider_slug'] is None
        messages = build_messages(session, [turn], 'Follow-up', 'automatic')
        assert [m['role'] for m in messages] == ['system', 'user', 'user']
