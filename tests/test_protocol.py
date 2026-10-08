import json
import pytest

from backend.protocol import JSONChannelParser as ChannelParser, ProtocolError


def test_every_possible_packet_boundary_preserves_unicode_and_corrections():
    wire = '\n'.join(json.dumps(event, ensure_ascii=False) for event in [
        {"channel": "answer", "text": "Hi 👋e\u0301"},
        {"channel": "truth", "text": "Independent channel."},
        {"channel": "answer", "backspace": 2},
        {"channel": "answer", "text": "!\ncode = 'yes'"},
        {"channel": "truth", "replace": "Checked."},
    ])
    for split in range(len(wire) + 1):
        parser = ChannelParser(["answer", "truth"])
        events = parser.feed(wire[:split]) + parser.feed(wire[split:]) + parser.finish()
        assert parser.channels == {"answer": "Hi 👋!\ncode = 'yes'", "truth": "Checked."}
        assert events[2]["removed"] == "e\u0301"
        assert len(events) == 5
        assert json.loads(parser.canonical())["channels"] == parser.channels


@pytest.mark.parametrize("event", [
    '{"channel":"unknown","text":"bad"}',
    '{"channel":"answer","backspace":9}',
    '{"channel":"answer","backspace":true}',
    '{"channel":"answer","backspace":0}',
    '{"channel":"answer","backspace":-1}',
    '{"channel":"answer","text":"bad","replace":"worse"}',
    '{"channel":"answer","text":4}',
    '{"channel":"answer","text":"\\ud800"}',
    '{"channel":"answer","text":"x","text":"y"}',
    '{"channel":"answer","status":"finished"}',
    '{"channel":"answer","execute":"print(1)"}',
    'not json', '[]', 'null', '{}',
])
def test_invalid_event_never_mutates_text(event):
    parser = ChannelParser(["answer"])
    parser.feed('{"channel":"answer","text":"ok"}\n')
    result = parser.feed(event + '\n')
    assert result[0]["type"] == "warning"
    assert parser.channels == {"answer": "ok"}
    assert parser.errors == 1


def test_empty_replace_and_status_are_valid():
    parser = ChannelParser(["answer"])
    for event in [{"channel": "answer", "text": "old"},
                  {"channel": "answer", "replace": ""},
                  {"channel": "answer", "status": "done"}]:
        parser.feed(json.dumps(event) + '\n')
    assert parser.channels["answer"] == ""
    assert parser.statuses["answer"] == "done"
    assert json.loads(parser.canonical())["channels"] == {}


def test_unterminated_line_is_bounded():
    parser = ChannelParser(["answer"])
    with pytest.raises(ProtocolError):
        parser.feed('x' * (parser.MAX_LINE + 1))
