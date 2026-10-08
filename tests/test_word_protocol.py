import json

import pytest

from backend.protocol import ChannelParser, ProtocolError
from backend.prompts import catalog_from_framework

MAPPING = {1: 'answer', 2: 'truth', 13: 'code'}


def parse(wire):
    parser = ChannelParser(MAPPING)
    events = parser.feed(wire) + parser.finish()
    return parser, events


def test_all_packet_boundaries_interleaving_and_corrections():
    wire = r'1_Hello 2_[C139_Deception] 1_wrong 1_words 1_<_2_Sibling. 1_~\n\n 1_é👋 1_< 1_Bye!'
    expected, expected_events = parse(wire)
    assert expected.channels == {'answer': 'Hello Sibling.\n\nBye!', 'truth': '[C139_Deception]'}
    for split in range(len(wire) + 1):
        p = ChannelParser(MAPPING)
        events = p.feed(wire[:split]) + p.feed(wire[split:]) + p.finish()
        assert p.channels == expected.channels
        assert events == expected_events
    p = ChannelParser(MAPPING)
    events = [e for char in wire for e in p.feed(char)] + p.finish()
    assert events == expected_events
    assert p.errors == 0


def test_example_word_prefixes_preserve_tags_and_underscores():
    p, _ = parse('2_[S3_WorkingSiblinghood] 1_Hello 1_Sibling. 2_[C39_Understanding] 1_I 1_see 1_your 1_goal.')
    assert p.channels['answer'] == 'Hello Sibling. I see your goal.'
    assert p.channels['truth'] == '[S3_WorkingSiblinghood] [C39_Understanding]'


def test_exact_code_layout_and_literal_commands():
    p, _ = parse(r'13_~def 13_~\shello(): 13_~\n\s\s\s\sreturn 13_~\s"hi" 13_~\n 1_\< 1_\!clear 1_\~word 1_C:\\temp 1_variable_name')
    assert p.channels['code'] == 'def hello():\n    return "hi"\n'
    assert p.channels['answer'] == '< !clear ~word C:\\temp variable_name'


def test_backspace_is_word_based_not_character_based():
    p, events = parse('1_One 1_é👩‍💻 2_Stable. 1_< 1_done 1_<_2 1_New.')
    assert p.channels == {'answer': 'New.', 'truth': 'Stable.'}
    edits = [e for e in events if e.get('operation') == 'backspace']
    assert edits[0]['removed'] == ' é👩‍💻'
    assert edits[0]['word_count'] == 1
    assert edits[1]['removed'] == 'One done'


@pytest.mark.parametrize('bad', ['hello', '0_bad', '99_unknown', '1_', '1_<_0', '1_<_9',
                                  '1_<_-1', '1_<_1_', r'1_<_1_bad\x', '1_~', '1_!unknown', r'1_bad\x'])
def test_invalid_units_and_compact_replacements_are_atomic(bad):
    p, events = parse('1_safe ' + bad)
    assert p.channels == {'answer': 'safe'}
    assert p.errors == 1
    assert events[-1]['type'] == 'warning'


def test_clear_status_punctuation_and_layout_unit_undo():
    p, events = parse(r'1_Hello 1_, 1_( 1_world 1_) 1_!done 1_~\n 1_< 1_!clear 1_New 1_\!')
    clear = next(e for e in events if e.get('operation') == 'replace')
    assert clear['removed'] == 'Hello, (world)'
    assert p.channels['answer'] == 'New!'
    assert p.statuses['answer'] == 'active'
    assert json.loads(p.canonical())['channels'] == {'answer': 'New!'}


def test_last_unit_waits_for_boundary():
    p = ChannelParser(MAPPING)
    assert p.feed('1_Hel') == []
    assert p.feed('lo 2_ch') == [dict(type='edit',sequence=1,channel='answer',operation='text',value='Hello',removed='',source='1_Hello')]
    assert p.channels == {'answer': 'Hello'}
    p.feed('eck')
    p.finish()
    assert p.channels['truth'] == 'check'


def test_token_and_output_limits():
    p = ChannelParser(MAPPING)
    with pytest.raises(ProtocolError):
        p.feed('1_' + 'x' * p.MAX_TOKEN)
    p = ChannelParser(MAPPING)
    p.MAX_OUTPUT = 10
    with pytest.raises(ProtocolError):
        p.feed('1_hello 1_world')


def test_explicit_numbers_survive_catalog_reordering():
    fw = '''protocol: MC/2
| 2 | truth | Truth | Core | Check. |
| 1 | answer | Answer | Core | Reply. |
'''
    catalog = catalog_from_framework(fw)
    assert {c['number']: c['id'] for c in catalog} == {1: 'answer', 2: 'truth'}
    with pytest.raises(ValueError):
        catalog_from_framework(fw.replace('| 2 |', '| 1 |'))
