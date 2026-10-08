"""Persistent provider affinity; routing leases are not evidence of a live KV cache."""
import copy
import re
import time
import uuid
from urllib.parse import quote

import httpx

from .provider import ProviderError


def cache_policy(model, mode, idle_seconds=1800):
    block = {'type': 'ephemeral'}
    root = {}
    ttl = None
    # Request the longest TTL documented through OpenRouter for these families.
    if mode == 'explicit' and model.startswith('anthropic/'):
        block['ttl'] = ttl = '1h'
        idle_seconds = max(idle_seconds, 3600)
    match = re.match(r'^openai/gpt-(\d+)(?:\.(\d+))?', model)
    if mode == 'explicit' and match and (int(match[1]), int(match[2] or 0)) >= (5, 6):
        root['prompt_cache_options'] = {'mode': 'explicit', 'ttl': '30m'}
        ttl = '30m'
        idle_seconds = max(idle_seconds, 1800)
    return dict(block=block, root=root, requested_ttl=ttl, idle_seconds=idle_seconds,
                ttl_status='requested' if ttl else 'provider-managed; expiry unknown')


def prepare_route(store, sid, model, idle_seconds, at=None):
    at = time.time() if at is None else at
    route = store.route(sid)
    if not route or route['model'] != model or route['expires_at'] <= at:
        route = dict(model=model, affinity_id=f'{sid}:{uuid.uuid4().hex}',
                     provider_name=None, provider_slug=None, scope=None,
                     expires_at=at + idle_seconds, idle_seconds=idle_seconds,
                     last_success_at=None, implicit_caching=None)
        store.save_route(sid, route)
    return route


def apply_route(payload, route):
    result = copy.deepcopy(payload)
    result['session_id'] = route['affinity_id']
    if route.get('provider_slug'):
        result['provider'] = {'only': [route['provider_slug']], 'allow_fallbacks': False}
    return result


def capture_metadata(target, obj):
    if isinstance(obj.get('id'), str):
        target['generation_id'] = obj['id']
    if isinstance(obj.get('provider'), str):
        target['provider_name'] = obj['provider']
    cached = ((obj.get('usage') or {}).get('prompt_tokens_details') or {}).get('cached_tokens')
    if isinstance(cached, (int, float)):
        target['cache_read_tokens'] = cached


_catalog_cache = {}


async def catalog(path):
    cached = _catalog_cache.get(path)
    if cached and time.monotonic() - cached[0] < 600:
        return cached[1]
    async with httpx.AsyncClient(timeout=8) as client:
        response = await client.get('https://openrouter.ai/api/v1/' + path)
        response.raise_for_status()
        data = response.json()['data']
    _catalog_cache[path] = (time.monotonic(), data)
    return data


async def resolve_provider(model, metadata, key):
    """Resolve reported identity via official catalogs, never guess a routing slug."""
    name = metadata.get('provider_name')
    if not name and metadata.get('generation_id'):
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                response = await client.get('https://openrouter.ai/api/v1/generation',
                                            params={'id': metadata['generation_id']},
                                            headers={'Authorization': f'Bearer {key}'})
                response.raise_for_status()
                name = response.json()['data'].get('provider_name')
                if isinstance(name, str):
                    metadata['provider_name'] = name
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            pass
    if not isinstance(name, str) or not name:
        return None
    try:
        data = await catalog('models/' + quote(model, safe='/') + '/endpoints')
        matches = [e for e in data['endpoints'] if e.get('provider_name', '').casefold() == name.casefold()]
        tags = {e['tag'] for e in matches if isinstance(e.get('tag'), str)}
        if len(tags) == 1:
            return dict(provider_name=name, provider_slug=next(iter(tags)), scope='endpoint',
                        implicit_caching=matches[0].get('supports_implicit_caching'))
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        pass
    try:
        providers = await catalog('providers')
        matches = [p for p in providers if p.get('name', '').casefold() == name.casefold()]
        if len(matches) == 1:
            return dict(provider_name=name, provider_slug=matches[0]['slug'], scope='provider', implicit_caching=None)
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        pass
    return None


async def confirm_route(store, sid, route, metadata, key, at=None):
    """Called only after a completed provider stream; failed calls do not renew."""
    at = time.time() if at is None else at
    if route.get('provider_name') and metadata.get('provider_name'):
        if route['provider_name'].casefold() != metadata['provider_name'].casefold():
            raise ProviderError('The reported provider differs from the pinned provider. The response stays local; no fallback call was made by MC Chat.')
    resolved = None
    if not route.get('provider_slug'):
        resolved = await resolve_provider(route['model'], metadata, key)
    updated = dict(route, **(resolved or {}), expires_at=at + route['idle_seconds'], last_success_at=at,
                   cache_observed=bool(route.get('cache_observed') or metadata.get('cache_read_tokens', 0) > 0))
    store.save_route(sid, updated)
    return updated
