import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.protocol import ChannelParser
from test_app import configure, events

MAPPING = {1:'answer',2:'truth',7:'synthetic_feelings',33:'humor'}


def decode(wire, selected=('answer','humor')):
    p = ChannelParser(MAPPING, selected)
    es = p.feed(wire) + p.finish()
    return p, es


def test_missing_underscore_repair_is_auditable_and_packet_independent():
    wire = '1_Hello 33_That 1_Sibling. 33“amazing” 1_!done 33_!done'
    reference, expected_events = decode(wire)
    for i in range(len(wire)+1):
        p = ChannelParser(MAPPING, ['answer','humor'])
        actual = p.feed(wire[:i])+p.feed(wire[i:])+p.finish()
        assert actual == expected_events
        assert p.channels == reference.channels
        assert p.errors == 0 and p.repairs == 1
        assert p.rounds.violations == 0
    repaired = next(e for e in expected_events if e['type']=='repair')
    assert repaired['source']=='33“amazing”'
    assert repaired['normalized_source']=='33_“amazing”'
    assert reference.channels['humor']=='That “amazing”'


@pytest.mark.parametrize('bad', ['33/ambiguous','33<_1','33!clear','99“word”','1<','2026-05-09','unassigned', '33“open', '33“closed"'])
def test_repair_does_not_guess_other_formats(bad):
    p = ChannelParser(MAPPING)
    p.feed('33_Start ')
    result = p.feed(bad+' ')+p.finish()
    assert p.errors == 1 and p.repairs == 0
    assert p.channels == {'humor':'Start'}
    assert result[-1]['type']=='warning'


def test_known_attached_word_punctuation_and_finish_recover_without_false_round_errors():
    wire='1_Hi 33_Mycelium 1_there 33— 1_today 33energy. 1_!done 33!done'
    p, es = decode(wire)
    assert p.repairs==3 and p.errors==0 and p.rounds.violations==0
    assert [e['source'] for e in es if e['type']=='repair']==['33—','33energy.','33!done']
    for split in range(len(wire)+1):
        other=ChannelParser(MAPPING,['answer','humor'])
        actual=other.feed(wire[:split])+other.feed(wire[split:])+other.finish()
        assert actual==es


def test_quote_repair_requires_a_previously_written_channel():
    p, _ = decode('33“amazing” 1_Hello')
    assert p.repairs==0 and p.errors==1


def test_rounds_skip_finish_join_and_compact_correction():
    p, es = decode('1_Hello 33_Wrong 1_!skip 33_<_1_Fun 2_Check 1_Sibling. 33_!done 2_!done 1_!done')
    assert p.rounds.violations == 0
    assert p.channels == {'answer':'Hello Sibling.','humor':'Fun','truth':'Check'}
    assert sum(e.get('operation')=='backspace' for e in es)==1
    assert p.rounds.slots == 9  # Compact correction counts once, not twice.


def test_finished_channel_can_rejoin_in_a_later_round():
    p, _ = decode('1_Hi 33_!done 1_there 33_Back 1_again 33_!done 1_!done')
    assert p.rounds.violations==0


def test_duplicate_close_does_not_occupy_a_slot_or_reopen_a_round():
    wire='1_A 33_Joke 2_Check 1_B 33_!done 2_fact 1_C 2_more 33_!done 1_!done 2_!done 2_!done'
    for split in range(len(wire)+1):
        p=ChannelParser(MAPPING,['answer','humor','truth'])
        output=p.feed(wire[:split])+p.feed(wire[split:])+p.finish()
        assert p.rounds.violations==0
        assert p.rounds.slots==10
        assert p.channels=={'answer':'A B C','humor':'Joke','truth':'Check fact more'}
        # Preserve redundant wire commands in the audit, despite their no-op effect.
        assert len([e for e in output if e.get('operation')=='status'])==5


@pytest.mark.parametrize('rejoin',['33_Back','33_!active'])
def test_close_after_rejoining_is_not_mistaken_for_duplicate(rejoin):
    p,_=decode(f'1_Hi 33_Joke 1_there 33_!done 1_Again {rejoin} 1_!done 33_!done')
    assert p.rounds.violations==0 and p.rounds.slots==8
    assert not p.rounds.active


def test_blocks_and_missing_final_slots_are_detected_without_reordering():
    wire='1_Whole 1_sentence. 33_Another 33_sentence.'
    p, es = decode(wire)
    assert p.rounds.violations > 0
    assert p.channels == {'answer':'Whole sentence.','humor':'Another sentence.'}
    assert [e['source'] for e in es if e['type']=='edit']==wire.split()
    p, _ = decode('1_Only')
    assert p.rounds.violations == 1


def test_escaped_multiple_words_cannot_bypass_rounds():
    p, _ = decode(r'1_~Whole\ssentence 33_Check')
    assert p.rounds.violations == 1


def test_single_channel_and_code_layout_unit():
    p, _ = decode(r'1_One 1_~\n\n 1_Two', ('answer',))
    assert p.rounds.violations == 0 and p.channels['answer']=='One\n\nTwo'


def test_legacy_profile_still_accepts_nonrotating_words():
    p = ChannelParser(MAPPING)
    p.feed('1_One 1_Two 33_Fun ')
    p.finish()
    assert p.rounds is None and p.errors==0


@pytest.mark.parametrize('wire,expected_status', [
    ('1_We 33_say 1_create 33“amazing” 1_!done 33_!done','complete'),
    ('1_We 1_create 33_say 33“amazing”','failed'),
])
def test_app_reports_rounds_repairs_and_keeps_only_valid_history(tmp_path,monkeypatch,wire,expected_status):
    configure(monkeypatch)
    sent=[]
    async def transport(cfg,payload):
        sent.append(payload)
        yield {'choices':[{'delta':{'content':wire},'finish_reason':'stop'}]}
        yield {'done':True}
    with TestClient(create_app(tmp_path,transport)) as client:
        sid=client.post('/api/sessions',json={}).json()['id']
        result=events(client.post(f'/api/sessions/{sid}/stream',json={'prompt':'test','channels':['answer','humor']}))
        assert result[-1]['status']==expected_status
        assert result[-1]['repairs']==1
        turn=client.get(f'/api/sessions/{sid}').json()['turns'][0]
        assert turn['raw']==wire
        assert turn['channels']['humor']=='say “amazing”'
        assert (turn['canonical'] is not None)==(expected_status=='complete')
        assert turn['interleaving']['status']==('passed' if expected_status=='complete' else 'failed')
        assert json.loads(sent[0]['messages'][-1]['content'])['round_channels']==[1,33]


def test_old_mc2_can_upgrade_to_rounds_without_changing_original(tmp_path,monkeypatch):
    configure(monkeypatch)
    from backend.app import ROOT
    current=(ROOT/'MCFramework.md').read_text(encoding='utf-8')
    old=current.replace('interleaving: rounds/1\n','')
    app=create_app(tmp_path)
    prior=app.state.store.create_session('test/model','live',old,'old-hash')
    with TestClient(app) as client:
        result=client.post(f'/api/sessions/{prior["id"]}/upgrade').json()
        assert result['id']!=prior['id']
        assert 'interleaving: rounds/1' in result['framework']
        assert client.get(f'/api/sessions/{prior["id"]}').json()['framework']==old
