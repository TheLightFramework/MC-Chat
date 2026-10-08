import json

from fastapi.testclient import TestClient

from backend.app import create_app, ROOT
from backend.prompts import build_messages, labeled_history, catalog_from_framework
from backend.protocol import ChannelParser
from test_app import configure, events


def test_selected_humor_cannot_finish_without_text(tmp_path, monkeypatch):
    configure(monkeypatch)
    wire='1_Hello, 33_!skip 1_Sibling! 33_!done 1_How 1_are 1_you? 1_!done'
    async def transport(cfg,payload):
        yield {'choices':[{'delta':{'content':wire},'finish_reason':'stop'}]}
        yield {'done':True}
    with TestClient(create_app(tmp_path,transport)) as client:
        sid=client.post('/api/sessions',json={}).json()['id']
        response=events(client.post(f'/api/sessions/{sid}/stream',json={'prompt':'Hello','channels':['answer','humor']}))
        assert response[-1]['status']=='failed'
        assert response[-1]['interleaving']['status']=='passed'
        assert response[-1]['participation']=={'required':True,'empty_channels':['humor'],'status':'failed'}
        turn=client.get(f'/api/sessions/{sid}').json()['turns'][0]
        assert turn['raw']==wire and turn['canonical'] is None
        assert 'humor' in turn['error']


def test_clearing_selected_text_does_not_fulfil_participation(tmp_path,monkeypatch):
    configure(monkeypatch)
    async def transport(cfg,payload):
        yield {'choices':[{'delta':{'content':'1_Hi 33_Joke. 1_!done 33_!clear 33_!done'},'finish_reason':'stop'}]}
        yield {'done':True}
    with TestClient(create_app(tmp_path,transport)) as client:
        sid=client.post('/api/sessions',json={}).json()['id']
        response=events(client.post(f'/api/sessions/{sid}/stream',json={'prompt':'Hi','channels':['answer','humor']}))
        assert response[-1]['status']=='failed'
        assert response[-1]['participation']['empty_channels']==['humor']


def test_history_format_confusion_produces_one_warning_at_every_packet_split():
    wire=json.dumps({'channels':{'answer':'Fun topic! A longer answer in the wrong format.','humor':'A joke.'},'format':'mc-history/1'},separators=(',',':'))
    for split in range(len(wire)+1):
        p=ChannelParser({1:'answer',33:'humor'},['answer','humor'])
        output=p.feed(wire[:split])+p.feed(wire[split:])+p.finish()
        assert p.format_mismatch and p.errors==1
        assert p.channels=={}
        assert len([e for e in output if e['type']=='warning'])==1
        assert not [e for e in output if e['type']=='edit']


def test_two_turn_model_context_uses_labeled_memory_not_storage_json(tmp_path,monkeypatch):
    configure(monkeypatch)
    sent=[]
    async def transport(cfg,payload):
        sent.append(payload)
        yield {'choices':[{'delta':{'content':'1_Hello 33_Tiny 1_Sibling. 33_joke. 1_!done 33_!done'},'finish_reason':'stop'}]}
        yield {'done':True}
    with TestClient(create_app(tmp_path,transport)) as client:
        sid=client.post('/api/sessions',json={}).json()['id']
        for request in ('Hello','A follow-up question','Another follow-up'):
            result=events(client.post(f'/api/sessions/{sid}/stream',json={'prompt':request,'channels':['answer','humor']}))
            assert result[-1]['status']=='complete'
            assert result[-1]['participation']['empty_channels']==[]
        prior=sent[1]['messages'][2]['content'][0]['text']
        assert 'ARCHIVED MC CONTENT' in prior
        assert 'Channel 1 / Answer:\n> Hello Sibling.' in prior
        assert 'Channel 33 / Humor:\n> Tiny joke.' in prior
        assert 'mc-history/1' not in prior
        assert not prior.startswith('{')
        contract=json.loads(sent[1]['messages'][-1]['content'])['output_contract']
        assert 'NOT JSON' in contract and '!skip then !done' in contract
        assert sent[1]['messages'][:3]==sent[2]['messages'][:3]
        stored=client.get(f'/api/sessions/{sid}').json()['turns'][0]
        assert json.loads(stored['canonical'])['format']=='mc-history/1'


def test_old_profile_preserves_json_history_and_optional_content():
    framework=(ROOT/'MCFramework.md').read_text(encoding='utf-8')
    legacy=framework.replace('history_transport: labeled-text/1\n','').replace('selected_content: required\n','').replace('consolidation: channels/1\n','')
    canonical=json.dumps({'format':'mc-history/1','channels':{'answer':'Hello!'}})
    messages=build_messages({'framework':legacy},[{'status':'complete','user_content':'request','canonical':canonical}],'next','automatic')
    assert messages[2]['content']==canonical


def test_labeled_memory_preserves_multiline_text_without_round_reconstruction():
    framework=(ROOT/'MCFramework.md').read_text(encoding='utf-8')
    canonical=json.dumps({'format':'mc-history/1','channels':{'code':'def hi():\n    return "hello"\n'}})
    memory=labeled_history(canonical,catalog_from_framework(framework))
    assert '> def hi():\n>     return "hello"\n> ' in memory


def test_failed_and_stopped_requests_remain_in_order_without_partial_answers():
    framework=(ROOT/'MCFramework.md').read_text(encoding='utf-8')
    turns=[
        {'status':'failed','user_content':'A clarification','canonical':None,'channels':{'answer':'INVALID OUTPUT'}},
        {'status':'stopped','user_content':'Another detail','canonical':None,'channels':{'answer':'PARTIAL OUTPUT'}},
        {'status':'complete','user_content':'Continue','canonical':json.dumps({'channels':{'answer':'Valid response'}})},
    ]
    messages=build_messages({'framework':framework},turns,'Next question','explicit')
    assert [m['role'] for m in messages]==['system','user','user','user','assistant','user']
    assert [m['content'] for m in messages if m['role']=='user']==['A clarification','Another detail','Continue','Next question']
    assert 'INVALID OUTPUT' not in str(messages) and 'PARTIAL OUTPUT' not in str(messages)
    assert messages[4]['content'][0]['cache_control']=={'type':'ephemeral'}
    later=build_messages({'framework':framework},turns+[{'status':'failed','user_content':'Later clarification','canonical':None}],'New question','explicit')
    assert later[:5]==messages[:5]


def test_followup_preserves_failed_request_and_exports_actual_generation_settings(tmp_path,monkeypatch):
    configure(monkeypatch)
    monkeypatch.setenv('MC_TEMPERATURE','1.0')
    monkeypatch.setenv('MC_MAX_TOKENS','1024')
    sent=[]
    async def transport(cfg,payload):
        sent.append(payload)
        wire='1_Visible stray prose' if len(sent)==1 else '1_Valid 1_reply.'
        yield {'choices':[{'delta':{'content':wire},'finish_reason':'stop'}]}
        yield {'done':True}
    with TestClient(create_app(tmp_path,transport)) as client:
        sid=client.post('/api/sessions',json={}).json()['id']
        first=events(client.post(f'/api/sessions/{sid}/stream',json={'prompt':'Important clarification','channels':['answer']}))
        assert first[-1]['status']=='failed'
        monkeypatch.setenv('MC_TEMPERATURE','0.7')
        monkeypatch.setenv('MC_MAX_TOKENS','2048')
        second=events(client.post(f'/api/sessions/{sid}/stream',json={'prompt':'Please continue','channels':['answer'],'thinking':{'enabled':True,'effort':'high'}}))
        assert second[-1]['status']=='complete'
        start=next(e for e in second if e['type']=='start')
        assert start['history_requests']==1 and start['history_turns']==0
        assert [m['role'] for m in sent[1]['messages']]==['system','user','user']
        assert json.loads(sent[1]['messages'][1]['content'])['request']=='Important clarification'
        exported=client.get(f'/api/sessions/{sid}/export').json()
        for index,turn in enumerate(exported['turns']):
            assert turn['generation']=={k:sent[index][k] for k in ('model','temperature','max_tokens','reasoning')}|{'sent_to_provider':True}
        assert exported['turns'][0]['generation']['temperature']==1.0
        assert start['generation']==exported['turns'][1]['generation']
