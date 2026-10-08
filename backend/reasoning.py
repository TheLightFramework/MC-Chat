"""Public OpenRouter model capabilities, cached independently of chat context."""
import time
import httpx

EFFORTS = ['minimal', 'low', 'medium', 'high', 'xhigh', 'max']
_models = {}
_expires = 0.0


def capabilities(model):
    info = model.get('reasoning') if model else None
    if isinstance(info, dict):
        supported = info.get('supported_efforts', [])
        efforts = EFFORTS if supported is None else [e for e in EFFORTS if e in supported]
        return dict(known=True, mandatory=bool(info.get('mandatory')), efforts=efforts,
                    default_effort=info.get('default_effort'))
    return dict(known=False, mandatory=False, efforts=EFFORTS, default_effort=None)


async def reasoning_capabilities(model_id):
    global _models, _expires
    if time.monotonic() >= _expires:
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                response = await client.get('https://openrouter.ai/api/v1/models')
                response.raise_for_status()
                _models = {m['id']: m for m in response.json()['data']}
            _expires = time.monotonic() + 600
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            # Cached data may still be useful when the public catalog is unavailable.
            _expires = time.monotonic() + 30
    return capabilities(_models.get(model_id))


def reasoning_request(enabled, effort):
    result = {'enabled': enabled, 'exclude': True}
    if not enabled:
        result['effort'] = 'none'
    elif effort != 'default':
        result['effort'] = effort
    return result
