import json

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.prompts import build_messages
from test_app import configure, events


WIRE = '1_A 2_Check 1_!done 2_!done'
FINAL = '# Final answer\n\n**Useful content.**\n'


def content(message):
    value=message['content']
    return value if isinstance(value,str) else value[0]['text']


def plain_messages(messages):
    return [{'role':m['role'],'content':content(m)} for m in messages]


def test_both_passes_and_followups_share_exact_content_prefix_and_provider(tmp_path,monkeypatch):
    configure(monkeypatch)
    sent=[]
    async def transport(cfg,payload):
        sent.append(payload)
        final=json.loads(content(payload['messages'][-1]))['phase']=='consolidation'
        yield {'id':f'gen-{len(sent)}','provider':'DeepInfra',
               'choices':[{'delta':{'content':FINAL if final else WIRE},'finish_reason':'stop'}]}
        yield {'done':True}
    with TestClient(create_app(tmp_path,transport)) as client:
        sid=client.post('/api/sessions',json={}).json()['id']
        for request in ('Original user request','A follow-up'):
            result=events(client.post(f'/api/sessions/{sid}/stream',json={'prompt':request,'two_pass':True}))
            assert result[-1]['status']=='complete'
            assert result[-1]['consolidation']['mode']=='channels'
        exported=client.get(f'/api/sessions/{sid}/export').json()
        first=exported['turns'][0]
        assert 'self_prompt' not in first['selected'] and 'self_prompt' not in first['channels']
        assert first['provider_metadata']['provider_name']=='DeepInfra'
        assert first['provider_request'] is None
        assert first['consolidation']['provider_request']=={'only':['test-provider'],'allow_fallbacks':False}
        assert first['consolidation']['request']==first['consolidation']['prompt']
        record=json.loads(first['consolidation']['request'])
        assert record['request']=='Original user request' and record['phase']=='consolidation'
    assert len(sent)==4 and len({p['session_id'] for p in sent})==1
    assert 'provider' not in sent[0]
    assert all(p['provider']=={'only':['test-provider'],'allow_fallbacks':False} for p in sent[1:])
    p1,p2,p3,p4=[plain_messages(p['messages']) for p in sent]
    assert p2[:len(p1)]==p1
    assert p3[:len(p2)]==p2  # Consolidation instruction also stays in history.
    assert p3[len(p2)]=={'role':'assistant','content':FINAL}
    assert p4[:len(p3)]==p3
    # Each new exploration ends with the full-framework reminder, while the
    # final request remains an ordinary Markdown consolidation instruction.
    for payload in (sent[0],sent[2]):
        raw=content(payload['messages'][-1])
        request=json.loads(raw)
        assert list(request)[-1]=='framework_reread'
        assert raw.index('"request":')<raw.index('"framework_reread":')
        assert 'ENTIRE initial MCFramework.md' in request['framework_reread']
        assert 'N_!done' in request['framework_reread']
    for payload in (sent[1],sent[3]):
        assert 'framework_reread' not in json.loads(content(payload['messages'][-1]))
    assert 'Channel 1 / Answer:\n> A' in p2[-2]['content']
    assert p2[-2]['content']==first['consolidation']['rendered_channels']
    assert all(p['messages'][0]==sent[0]['messages'][0] for p in sent)
    # At most three cache breakpoints; metadata placement may move, text may not.
    for p in sent:
        assert sum(isinstance(m['content'],list) for m in p['messages'])<=3


def test_empty_optional_channels_are_omitted_and_rendered_text_is_frozen():
    from backend.app import read_framework
    from backend.prompts import rendered_channels,catalog_from_framework
    framework=read_framework()
    canonical=json.dumps({'channels':{'answer':'Result','humor':'   ','self_prompt':'Old prompt'}})
    rendered=rendered_channels(canonical,catalog_from_framework(framework))
    assert 'Result' in rendered and 'Humor' not in rendered and 'Old prompt' not in rendered
    turn={'status':'complete','prompt':'Task','user_content':'original record','canonical':canonical,
          'consolidation':{'status':'complete','rendered_channels':'Frozen channel text',
                           'request':'Frozen mode instruction','raw':FINAL}}
    messages=build_messages({'framework':framework},[turn],'next','automatic')
    assert messages[2]['content']=='Frozen channel text'
    assert messages[3]['content']=='Frozen mode instruction'
    assert messages[4]['content']==FINAL


def test_invalid_first_pass_never_consolidates_and_user_request_stays(tmp_path,monkeypatch):
    configure(monkeypatch)
    calls=[]
    async def transport(cfg,payload):
        calls.append(payload)
        yield {'provider':'DeepInfra','choices':[{'delta':{'content':'1_A broken 2_Check'},'finish_reason':'stop'}]}
        yield {'done':True}
    with TestClient(create_app(tmp_path,transport)) as client:
        sid=client.post('/api/sessions',json={}).json()['id']
        result=events(client.post(f'/api/sessions/{sid}/stream',json={'prompt':'Remember me','two_pass':True}))[-1]
        assert result['status']=='failed' and len(calls)==1
        assert result['consolidation']['status']=='skipped'
        session=client.get(f'/api/sessions/{sid}').json()
        context=build_messages(session,session['turns'],'next','automatic')
        assert [m['role'] for m in context]==['system','user','user']


def test_new_two_pass_demo_does_not_need_self_prompt(tmp_path,monkeypatch):
    configure(monkeypatch)
    async def no_wait(_):pass
    monkeypatch.setattr('backend.app.asyncio.sleep',no_wait)
    with TestClient(create_app(tmp_path)) as client:
        sid=client.post('/api/sessions',json={'mode':'demo'}).json()['id']
        result=events(client.post(f'/api/sessions/{sid}/stream',json={'prompt':'Demo','two_pass':True}))[-1]
        assert result['status']=='complete' and 'self_prompt' not in result['channels']
        assert result['consolidation']['mode']=='channels'
        assert 'original request' in result['consolidation']['raw']


def test_selected_styles_and_findings_reach_final_pass_without_changing_history(tmp_path, monkeypatch):
    configure(monkeypatch)
    sent = []
    creative_wire = ('1_One 29_A 31_Colored 2_Output '
                     '1_stream. 29_loom. 31_threads. 2_format. '
                     '1_!done 29_!done 31_!done 2_!done')

    async def transport(cfg, payload):
        sent.append(payload)
        request = json.loads(content(payload['messages'][-1]))
        if request['phase'] == 'consolidation':
            wire = 'Picture colored threads on a loom: one stream, distinct strands.'
        else:
            wire = creative_wire if len(sent) == 1 else '1_Result. 14_Checked. 1_!done 14_!done'
        yield {'provider': 'Test Provider', 'choices': [{'delta': {'content': wire}, 'finish_reason': 'stop'}]}
        yield {'done': True}

    with TestClient(create_app(tmp_path, transport)) as client:
        sid = client.post('/api/sessions', json={}).json()['id']
        first = events(client.post(f'/api/sessions/{sid}/stream', json={
            'prompt': 'Explain the system.', 'channels': ['answer', 'creativity', 'imagery'],
            'notes': {'imagery': 'Keep the colored threads analogy.'}, 'two_pass': True}))[-1]
        assert first['status'] == 'complete'
        second = events(client.post(f'/api/sessions/{sid}/stream', json={
            'prompt': 'Now check correctness.', 'channels': ['answer', 'correctness'], 'two_pass': True}))[-1]
        assert second['status'] == 'complete'
        exported = client.get(f'/api/sessions/{sid}/export').json()

    final_request = json.loads(content(sent[1]['messages'][-1]))
    assert final_request['selected_channels'] == ['answer', 'creativity', 'imagery']
    assert final_request['channel_notes'] == {'imagery': 'Keep the colored threads analogy.'}
    guidance = {c['id']: c for c in final_request['channel_guidance']}
    assert set(guidance) == {'answer', 'truth', 'creativity', 'imagery'}
    assert guidance['truth']['selected'] is False
    assert guidance['imagery']['selected'] is True and guidance['imagery']['number'] == 31
    assert 'metaphor' in guidance['imagery']['purpose'].lower()
    assert len(final_request['presentation_guidance']) == 2
    assert any('concrete imagery and metaphors' in rule for rule in final_request['presentation_guidance'])
    assert 'substance and presentation' in final_request['instruction']
    assert 'Colored threads.' in content(sent[1]['messages'][-2])
    assert 'A loom.' in content(sent[1]['messages'][-2])
    # Full corrected texts occur once, in the preceding assistant message.
    assert 'Colored threads.' not in content(sent[1]['messages'][-1])
    next_request = json.loads(content(sent[3]['messages'][-1]))
    assert next_request['selected_channels'] == ['answer', 'correctness']
    assert next_request['presentation_guidance'] == []
    p1, p2, p3, p4 = [plain_messages(p['messages']) for p in sent]
    assert p2[:len(p1)] == p1 and p3[:len(p2)] == p2 and p4[:len(p3)] == p3
    assert exported['turns'][0]['consolidation']['request'] == content(sent[1]['messages'][-1])
    reminder = json.loads(content(sent[0]['messages'][-1]))['framework_reread']
    assert 'Even when only Answer remains' in reminder
    assert 'standalone exclamation is N_\\!' in reminder
    assert 'poem layout' in reminder


def test_channel_guidance_omits_empty_channels_and_preserves_legacy_fallback():
    from backend.app import read_framework
    from backend.prompts import consolidation_request, catalog_from_framework
    legacy = json.loads(consolidation_request('Task'))
    assert set(legacy) == {'phase', 'request', 'channel_notes', 'instruction'}
    request = json.loads(consolidation_request('Task', selected=['answer'],
        canonical=json.dumps({'channels': {'answer': 'Result', 'imagery': '  ', 'humor': '', 'self_prompt': 'legacy'}}),
        catalog=catalog_from_framework(read_framework())))
    assert [c['id'] for c in request['channel_guidance']] == ['answer']
    assert request['presentation_guidance'] == []
