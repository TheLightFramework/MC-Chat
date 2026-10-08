import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import routing
from backend.app import create_app
from backend.provider import ProviderError
from backend.routing import resolve_provider as real_resolve_provider
from backend.storage import Store
from test_app import configure, events


def test_lease_persists_renews_and_expires_to_a_fresh_unpinned_identity(tmp_path):
    store = Store(tmp_path/'routes.sqlite3')
    route = routing.prepare_route(store, 'chat', 'test/model', 1800, at=100)
    initial = route['affinity_id']
    assert 'provider' not in routing.apply_route({}, route)
    route = asyncio.run(routing.confirm_route(store, 'chat', route, {'provider_name':'DeepInfra'}, 'secret', at=200))
    assert route['expires_at'] == 2000
    reopened = Store(tmp_path/'routes.sqlite3')
    same = routing.prepare_route(reopened, 'chat', 'test/model', 1800, at=1999)
    assert same['affinity_id'] == initial and same['provider_name'] == 'DeepInfra'
    payload = routing.apply_route({'session_id':'wrong'}, same)
    assert payload['provider'] == {'only':['test-provider'],'allow_fallbacks':False}
    expired = routing.prepare_route(reopened, 'chat', 'test/model', 1800, at=2000)
    assert expired['affinity_id'] != initial and expired['provider_slug'] is None
    other = routing.prepare_route(store, 'other-chat', 'test/model', 1800, at=100)
    assert other['affinity_id'] != initial


def test_model_change_starts_another_routing_epoch(tmp_path):
    store = Store(tmp_path/'routes.sqlite3')
    old = routing.prepare_route(store, 'chat', 'model/a', 1800, at=100)
    new = routing.prepare_route(store, 'chat', 'model/b', 1800, at=101)
    assert old['affinity_id'] != new['affinity_id']


def test_cache_ttl_requests_are_model_specific_and_do_not_claim_deepseek_retention():
    deepseek = routing.cache_policy('deepseek/deepseek-v4.1-flash','explicit')
    assert deepseek['requested_ttl'] is None and deepseek['root'] == {}
    assert deepseek['idle_seconds'] == 1800 and 'ttl' not in deepseek['block']
    claude = routing.cache_policy('anthropic/claude-sonnet-4.6','explicit')
    assert claude['block'] == {'type':'ephemeral','ttl':'1h'}
    assert claude['idle_seconds'] == 3600
    openai = routing.cache_policy('openai/gpt-6-luna','explicit')
    assert openai['root'] == {'prompt_cache_options':{'mode':'explicit','ttl':'30m'}}
    assert 'ttl' not in openai['block']
    assert routing.cache_policy('anthropic/claude-sonnet-4.6','automatic')['requested_ttl'] is None


@pytest.mark.parametrize('tags,expected,scope', [(['deepinfra/fp8'],'deepinfra/fp8','endpoint'),
                                              (['deepinfra/fp8','deepinfra/turbo'],'deepinfra','provider')])
def test_slug_resolved_from_catalog_without_guessing_variant(monkeypatch,tags,expected,scope):
    async def catalog(path):
        if path == 'providers':return [{'name':'DeepInfra','slug':'deepinfra'}]
        return {'endpoints':[{'provider_name':'DeepInfra','tag':tag,'supports_implicit_caching':False} for tag in tags]}
    monkeypatch.setattr(routing,'catalog',catalog)
    result = asyncio.run(real_resolve_provider('test/model',{'provider_name':'DeepInfra'},'secret'))
    assert result['provider_slug'] == expected and result['scope'] == scope


def test_generation_metadata_lookup_when_stream_omits_provider(monkeypatch):
    original_client = httpx.AsyncClient
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200,json={'data':{'provider_name':'DeepInfra'}})
    monkeypatch.setattr(routing.httpx,'AsyncClient',lambda **kw:original_client(transport=httpx.MockTransport(handler),**kw))
    async def catalog(path):return {'endpoints':[{'provider_name':'DeepInfra','tag':'deepinfra/fp8'}]}
    monkeypatch.setattr(routing,'catalog',catalog)
    metadata={'generation_id':'gen-123'}
    result=asyncio.run(real_resolve_provider('test/model',metadata,'secret'))
    assert result['provider_slug']=='deepinfra/fp8'
    assert requests[0].url.params['id']=='gen-123'
    assert requests[0].headers['authorization']=='Bearer secret'
    assert 'secret' not in str(result) and metadata['provider_name']=='DeepInfra'


def test_unknown_provider_is_not_converted_to_an_invented_slug(monkeypatch):
    async def catalog(path):return [] if path=='providers' else {'endpoints':[]}
    monkeypatch.setattr(routing,'catalog',catalog)
    assert asyncio.run(real_resolve_provider('test/model',{'provider_name':'Unknown Vendor'},'secret')) is None


def test_provider_change_is_detected_without_renewing_lock(tmp_path):
    store=Store(tmp_path/'routes.sqlite3')
    route=routing.prepare_route(store,'chat','model',1800,at=100)
    route.update(provider_name='DeepInfra',provider_slug='deepinfra/fp8')
    store.save_route('chat',route)
    with pytest.raises(ProviderError,match='differs'):
        asyncio.run(routing.confirm_route(store,'chat',route,{'provider_name':'Other'},'secret',at=200))
    assert store.route('chat')['expires_at']==1900


def test_pinned_provider_failure_is_not_retried_or_renewed(tmp_path,monkeypatch):
    configure(monkeypatch)
    sent=[]
    async def transport(cfg,payload):
        sent.append(payload)
        if len(sent)>1:raise ProviderError('Pinned provider unavailable')
        yield {'provider':'DeepInfra','choices':[{'delta':{'content':'1_Hi 2_Check 1_!done 2_!done'},'finish_reason':'stop'}]}
        yield {'done':True}
    app=create_app(tmp_path,transport)
    with TestClient(app) as client:
        sid=client.post('/api/sessions',json={}).json()['id']
        assert events(client.post(f'/api/sessions/{sid}/stream',json={'prompt':'First'}))[-1]['status']=='complete'
        expiry=app.state.store.route(sid)['expires_at']
        result=events(client.post(f'/api/sessions/{sid}/stream',json={'prompt':'Second'}))
        assert result[-1]['status']=='failed' and len(sent)==2
        assert sent[1]['provider']=={'only':['test-provider'],'allow_fallbacks':False}
        assert app.state.store.route(sid)['expires_at']==expiry


def test_failed_identity_lookup_prevents_an_unpinned_second_call(tmp_path,monkeypatch):
    configure(monkeypatch)
    async def unresolved(*args):return None
    monkeypatch.setattr(routing,'resolve_provider',unresolved)
    calls=[]
    async def transport(cfg,payload):
        calls.append(payload)
        yield {'choices':[{'delta':{'content':'1_Hi 2_Check 1_!done 2_!done'},'finish_reason':'stop'}]}
        yield {'done':True}
    with TestClient(create_app(tmp_path,transport)) as client:
        sid=client.post('/api/sessions',json={}).json()['id']
        result=events(client.post(f'/api/sessions/{sid}/stream',json={'prompt':'Test','two_pass':True}))
        assert result[-1]['consolidation']['status']=='skipped'
        assert result[-1]['exploration_status']=='complete' and len(calls)==1
        assert any('could not be identified' in e.get('message','') for e in result)
