"""OpenRouter streaming transport. Only public completion content is consumed."""
import json
import httpx


class ProviderError(RuntimeError):
    pass


def finish_reason_message(reason, stage='Generation'):
    """Explain normalized stop reasons without treating every failure as truncation."""
    if reason == 'content_filter':
        return (f'{stage} was stopped by a provider content filter (content_filter). '
                'The stop code does not explain what triggered it. Increasing MC_MAX_TOKENS '
                'does not address this stop. No automatic retry or provider switch was made.')
    if reason == 'length':
        return (f'{stage} reached the model/provider token limit (length). Request a shorter '
                'response or adjust MC_MAX_TOKENS within the model limits; reasoning may '
                'also consume the output budget. Partial output stays local.')
    if reason in ('tool_calls', 'function_call'):
        return f'{stage} requested a tool call ({reason}), but this chat does not execute model tools. No tool was run.'
    if reason == 'error':
        return f'{stage} ended with a provider error (error). Partial output stays local; no automatic retry was made.'
    return f'{stage} did not finish normally ({reason or "unreported finish reason"}). Partial output stays local.'


async def sse_objects(lines):
    data = []
    async for line in lines:
        if line == "":
            if data:
                value = "\n".join(data)
                data = []
                if value == "[DONE]":
                    yield {"done": True}
                    return
                try:
                    yield json.loads(value)
                except ValueError as exc:
                    raise ProviderError("Provider sent malformed SSE data.") from exc
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
        # SSE comments/keepalives and other fields do not contain completion text.
    if data:
        value = "\n".join(data)
        if value == "[DONE]":
            yield {"done": True}
        else:
            try:
                yield json.loads(value)
            except ValueError as exc:
                raise ProviderError("Provider sent malformed SSE data.") from exc


async def openrouter_stream(settings, payload):
    timeout = httpx.Timeout(settings["timeout"], connect=15)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "POST", "https://openrouter.ai/api/v1/chat/completions", json=payload,
            headers={"Authorization": f"Bearer {settings['key']}",
                     "Content-Type": "application/json", "X-Title": "MC Chat",
                     "HTTP-Referer": "http://localhost:8765"},
        ) as response:
            if response.status_code != 200:
                if (payload.get('provider') or {}).get('only') and response.status_code in (404, 429, 500, 502, 503, 504):
                    raise ProviderError(f'OpenRouter HTTP {response.status_code}. The pinned provider could not serve this request. No provider fallback was allowed; retry later or after the routing lock expires.')
                descriptions = {401: "Check OPENROUTER_API_KEY in .env.",
                                402: "OpenRouter credits are insufficient.",
                                429: "OpenRouter rate limit reached; try again later.",
                                400: "Check the model slug and thinking settings: some models require thinking or reject certain efforts. Try MC_CACHE_MODE=automatic if this provider rejects cache markers.",
                                404: "Check OPENROUTER_MODEL in .env and create a new chat."}
                raise ProviderError(f"OpenRouter HTTP {response.status_code}. " + descriptions.get(
                    response.status_code, "The provider could not complete this request."))
            async for obj in sse_objects(response.aiter_lines()):
                if not isinstance(obj, dict):
                    raise ProviderError("Provider returned an invalid stream object.")
                if obj.get("error"):
                    raise ProviderError("OpenRouter reported an error during generation. Partial output was kept locally.")
                yield obj
