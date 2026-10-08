import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend import reasoning
from test_app import configure, events


@pytest.mark.parametrize('enabled,effort,expected', [
    (False,'max',{'enabled':False,'effort':'none','exclude':True}),
    (True,'default',{'enabled':True,'exclude':True}),
    *[(True,e,{'enabled':True,'effort':e,'exclude':True}) for e in reasoning.EFFORTS],
])
def test_thinking_payload_and_saved_turn(tmp_path,monkeypatch,enabled,effort,expected):
    configure(monkeypatch)
    sent=[]
    async def transport(cfg,payload):
        sent.append(payload)
        yield {'choices':[{'delta':{'content':'1_Ready! 2_Checked.'},'finish_reason':'stop'}]}
        yield {'usage':{'completion_tokens_details':{'reasoning_tokens':0}}}
        yield {'done':True}
    with TestClient(create_app(tmp_path,transport)) as client:
        sid=client.post('/api/sessions',json={}).json()['id']
        body={'prompt':'Test','thinking':{'enabled':enabled,'effort':effort}}
        output=events(client.post(f'/api/sessions/{sid}/stream',json=body))
        assert output[-1]['status']=='complete'
        assert sent[0]['reasoning']==expected
        turn=client.get(f'/api/sessions/{sid}').json()['turns'][0]
        assert turn['reasoning']==expected
        assert turn['thinking']==body['thinking']
        assert 'thinking' not in json.loads(turn['user_content'])
        assert turn['usage']['completion_tokens_details']['reasoning_tokens']==0


def test_default_off_and_effort_validation(tmp_path,monkeypatch):
    configure(monkeypatch)
    sent=[]
    async def transport(cfg,payload):
        sent.append(payload)
        yield {'choices':[{'delta':{'content':'1_Ready! 2_Checked.'},'finish_reason':'stop'}]}
        yield {'done':True}
    with TestClient(create_app(tmp_path,transport)) as client:
        sid=client.post('/api/sessions',json={}).json()['id']
        client.post(f'/api/sessions/{sid}/stream',json={'prompt':'test'})
        assert sent[0]['reasoning']['effort']=='none'
        response=client.post(f'/api/sessions/{sid}/stream',json={'prompt':'bad','thinking':{'enabled':True,'effort':'unlimited'}})
        assert response.status_code==422
        assert len(sent)==1


def test_effort_change_does_not_rewrite_message_history(tmp_path,monkeypatch):
    configure(monkeypatch)
    sent=[]
    async def transport(cfg,payload):
        sent.append(payload)
        yield {'choices':[{'delta':{'content':'1_Ready! 2_Checked.'},'finish_reason':'stop'}]}
        yield {'done':True}
    with TestClient(create_app(tmp_path,transport)) as client:
        sid=client.post('/api/sessions',json={}).json()['id']
        for effort in ('low','high','max'):
            client.post(f'/api/sessions/{sid}/stream',json={'prompt':'test','thinking':{'enabled':True,'effort':effort}})
        assert sent[1]['messages'][:3]==sent[2]['messages'][:3]
        assert sent[0]['messages'][0]==sent[2]['messages'][0]
        assert [s['reasoning']['effort'] for s in sent]==['low','high','max']


def test_capability_normalization():
    assert reasoning.capabilities({'reasoning':{'mandatory':False,'supported_efforts':['max','high','low']}})['efforts']==['low','high','max']
    assert reasoning.capabilities({'reasoning':{'mandatory':True,'supported_efforts':None}})['mandatory']
    assert reasoning.capabilities({'reasoning':{}})['efforts']==[]
    assert not reasoning.capabilities(None)['known']


def test_public_catalog_is_cached_and_does_not_send_key(monkeypatch):
    monkeypatch.setattr(reasoning,'_models',{})
    monkeypatch.setattr(reasoning,'_expires',0)
    requests=[]
    original=httpx.AsyncClient
    def handler(request):
        requests.append(request)
        assert 'authorization' not in request.headers
        return httpx.Response(200,json={'data':[{'id':'test/model','reasoning':{'mandatory':True,'supported_efforts':['high']}}]})
    monkeypatch.setattr(reasoning.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    async def run():
        a=await reasoning.reasoning_capabilities('test/model')
        b=await reasoning.reasoning_capabilities('test/model')
        return a,b
    a,b=asyncio.run(run())
    assert a==b and a['mandatory'] and a['efforts']==['high']
    assert len(requests)==1
