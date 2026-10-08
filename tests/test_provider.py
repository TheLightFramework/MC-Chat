import asyncio
import json

import httpx
import pytest

from backend import provider


def test_http_transport_uses_authorization_and_accepts_usage_only_chunk(monkeypatch):
    original_client = httpx.AsyncClient
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, text=': ping\n\ndata: {"choices": [{"delta": {"content": "public"}}]}\n\n'
                              'data: {"choices": [], "usage": {"prompt_tokens": 100}}\n\n'
                              'data: [DONE]\n\n')

    monkeypatch.setattr(provider.httpx, 'AsyncClient', lambda **kw: original_client(
        transport=httpx.MockTransport(handler), **kw))

    async def run():
        return [x async for x in provider.openrouter_stream({'timeout': 10, 'key': 'secret'}, {'model': 'test', 'stream': True})]

    result = asyncio.run(run())
    assert captured[0].headers['authorization'] == 'Bearer secret'
    assert json.loads(captured[0].content)['stream'] is True
    assert result[-1] == {'done': True}
    assert result[1]['usage']['prompt_tokens'] == 100


@pytest.mark.parametrize('status', [400, 401, 402, 404, 429, 500])
def test_provider_errors_do_not_echo_sensitive_body(monkeypatch, status):
    original_client = httpx.AsyncClient
    monkeypatch.setattr(provider.httpx, 'AsyncClient', lambda **kw: original_client(
        transport=httpx.MockTransport(lambda _: httpx.Response(status, text='secret from upstream')), **kw))

    async def run():
        return [x async for x in provider.openrouter_stream({'timeout': 10, 'key': 'secret'}, {})]

    with pytest.raises(provider.ProviderError) as exc:
        asyncio.run(run())
    assert str(status) in str(exc.value)
    assert 'secret' not in str(exc.value)


def test_midstream_error_is_detected(monkeypatch):
    original_client = httpx.AsyncClient
    monkeypatch.setattr(provider.httpx, 'AsyncClient', lambda **kw: original_client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text='data: {"error": {"message": "private provider error"}}\n\n')), **kw))

    async def run():
        return [x async for x in provider.openrouter_stream({'timeout': 10, 'key': 'secret'}, {})]

    with pytest.raises(provider.ProviderError, match='during generation'):
        asyncio.run(run())


@pytest.mark.parametrize('reason', ['content_filter', 'tool_calls', 'error', None, 'unknown'])
def test_non_length_stops_do_not_recommend_more_tokens(reason):
    message = provider.finish_reason_message(reason)
    assert 'increase MC_MAX_TOKENS' not in message
    assert 'Request a shorter' not in message
    if reason == 'content_filter':
        assert 'content filter' in message and 'does not explain what triggered it' in message


def test_length_stop_has_token_budget_guidance():
    message = provider.finish_reason_message('length')
    assert 'token limit' in message and 'MC_MAX_TOKENS' in message
