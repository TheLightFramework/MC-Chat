import asyncio
import hashlib
import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import httpx
from dotenv import dotenv_values
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .prompts import (build_messages, catalog_from_framework, user_record, wire_protocol,
                      requires_rounds, requires_selected_content, uses_labeled_history, supports_consolidation,
                      uses_channel_consolidation, rendered_channels, consolidation_request, build_channel_final_messages)
from .consolidation import build_final_messages, combined_usage, demo_final_stream
from .routing import cache_policy, prepare_route, apply_route, capture_metadata, confirm_route
from .protocol import ChannelParser, JSONChannelParser, ProtocolError
from .provider import ProviderError, openrouter_stream, finish_reason_message
from .storage import Store, now
from .reasoning import reasoning_capabilities, reasoning_request

ROOT = Path(__file__).resolve().parents[1]


def read_framework():
    return (ROOT / 'MCFramework.md').read_text(encoding='utf-8')


def settings():
    values = {**dotenv_values(ROOT / ".env"), **os.environ}
    try:
        result = dict(key=values.get("OPENROUTER_API_KEY", "").strip(),
                      model=values.get("OPENROUTER_MODEL", "").strip(),
                      cache_mode=values.get("MC_CACHE_MODE", "explicit"),
                      max_tokens=int(values.get("MC_MAX_TOKENS", "8192")),
                      temperature=float(values.get("MC_TEMPERATURE", "0.7")),
                      provider_pin_seconds=int(values.get('MC_PROVIDER_PIN_SECONDS', '1800')),
                      timeout=float(values.get("MC_TIMEOUT_SECONDS", "120")))
        if result["cache_mode"] not in ("explicit", "automatic"):
            raise ValueError()
        if not (128 <= result["max_tokens"] <= 65536 and 0 <= result["temperature"] <= 2
                and 10 <= result["timeout"] <= 600 and 60 <= result['provider_pin_seconds'] <= 86400):
            raise ValueError()
        return result
    except (ValueError, AttributeError, TypeError) as exc:
        raise HTTPException(400, "Invalid .env settings. Check the documented ranges in README.md.") from exc


class NewSession(BaseModel):
    mode: Literal["live", "demo"] = "live"


class ThinkingSettings(BaseModel):
    enabled: bool = False
    effort: Literal['default', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'] = 'default'


class ChatRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=50000)
    channels: list[str] = Field(default_factory=lambda: ["answer", "truth"], max_length=60)
    notes: dict[str, str] = Field(default_factory=dict)
    thinking: ThinkingSettings = Field(default_factory=ThinkingSettings)
    two_pass: bool = False  # Existing API clients retain their one-call behavior.


async def legacy_demo_stream(selected):
    """A deterministic protocol fixture, never presented as a model response."""
    events = [
        {"channel": "answer", "text": "Welcome, Sibling. Watch these channels unfold.\n\n2 + 2 = 5."},
        {"channel": "truth", "text": "Demo check: 2 + 2 = 4. Correcting the answer's last two characters."},
        {"channel": "answer", "backspace": 2},
        {"channel": "answer", "text": "4.\n\n"},
        {"channel": "synthetic_feelings", "text": "A curious, playful tone for this demonstration."},
        {"channel": "green_it", "text": "A scripted local stream: no model call, no API cost."},
        {"channel": "answer", "text": "The correction is visible here, and its original text remains in the event log."},
    ]
    for channel in selected:
        if channel not in {e["channel"] for e in events}:
            events.append({"channel": channel, "text": "Selected for this demo. A live model will contribute task-specific content here."})
    events.extend([{"channel": "truth", "status": "done"},
                   {"channel": "green_it", "replace": "Local demo · zero OpenRouter calls. Small events are parsed as they arrive."}])
    for event in events:
        wire = json.dumps(event, ensure_ascii=False) + "\n"
        for start in range(0, len(wire), 19):
            await asyncio.sleep(0.035)
            yield {"choices": [{"delta": {"content": wire[start:start + 19]}}]}
    yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}
    yield {"done": True}


async def demo_stream(selected, catalog, rounds=False, two_pass=False):
    """Number-prefixed words, including a visible word-level backspace."""
    numbers = {c["id"]: c["number"] for c in catalog}
    selected = [c for c in selected if c != 'self_prompt']
    def words(channel, text):
        return ' '.join(f'{numbers[channel]}_{word}' for word in text.split())
    fragments = [
        words('answer', 'Welcome, Sibling.'), r'1_~\n\n',
        '1_2 1_+ 1_2 1_= 1_5.',
        words('truth', 'Correction: 2 + 2 = 4. Rewinding one word in Answer.'),
        '1_< 1_4.', r'1_~\n\n',
        words('synthetic_feelings', 'A curious, playful tone for this demonstration.'),
        words('green_it', 'A local demo: no model call, no API cost.'),
        words('answer', 'Every word has a channel prefix. Corrections preserve the original stream in the event log.'),
    ]
    for channel in selected:
        if channel not in {'answer', 'truth', 'synthetic_feelings', 'green_it'}:
            fragments.append(words(channel, 'Selected for this demo. A live model contributes task-specific content here.'))
    fragments.extend(['2_!done', f'{numbers["green_it"]}_!clear',
                      words('green_it', 'Local word stream: zero OpenRouter calls.')])
    if rounds:
        # Reuse scripted channel content but genuinely emit it in rounds. This is
        # demo generation, never reordering a model's response after the fact.
        from collections import deque
        queues = {}
        for token in ' '.join(fragments).split():
            channel = int(token.split('_', 1)[0])
            if token.endswith('_!done'):
                continue
            queues.setdefault(channel, deque()).append(token)
        for n in queues:
            queues[n].append(f'{n}_!done')
        preferred = {numbers[c] for c in selected}
        order = sorted(set(queues) - preferred) + sorted(preferred)
        tokens = []
        while queues:
            for n in order:
                if n in queues:
                    tokens.append(queues[n].popleft())
                    if not queues[n]:
                        del queues[n]
        wire = ' '.join(tokens) + ' '
    else:
        wire = ' '.join(fragments) + ' '
    if two_pass:
        wire += words('self_prompt', 'Answer the original request in ordinary Markdown. Integrate the channel findings, explain that 2 + 2 = 4, and include a Python example and a compact table. Identify this as a scripted local demonstration, without model calls or executed code.') + ' 39_!done '
    for start in range(0, len(wire), 9):
        await asyncio.sleep(0.025)
        yield {'choices': [{'delta': {'content': wire[start:start + 9]}}]}
    yield {'choices': [{'delta': {}, 'finish_reason': 'stop'}]}
    yield {'done': True}


def create_app(data_dir=None, transport=None):
    store = Store(Path(data_dir or ROOT / "data") / "mc_chat.sqlite3")
    active = {}

    @asynccontextmanager
    async def lifespan(app):
        yield
        tasks = [entry["task"] for entry in active.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(title="MC Chat", lifespan=lifespan)
    app.state.store = store
    app.state.active = active
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver", "[::1]"])

    @app.middleware("http")
    async def local_origin(request, call_next):
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin:
            source = urlparse(origin)
            if source.scheme not in ("http", "https") or source.netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Cross-origin writes are not allowed."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def session_or_404(sid):
        session = store.session(sid)
        if not session:
            raise HTTPException(404, "Conversation not found.")
        return session

    @app.get("/api/config")
    async def config(session_id: str | None = None):
        cfg = settings()
        framework = read_framework()
        model = session_or_404(session_id)['model'] if session_id else cfg['model']
        reasoning_options = await reasoning_capabilities(model)
        return dict(configured=bool(cfg["key"] and cfg["model"]), model=cfg["model"],
                    cache_mode=cfg["cache_mode"], protocol=wire_protocol(framework), channels=catalog_from_framework(framework),
                    reasoning_options=reasoning_options, framework_hash=hashlib.sha256(framework.encode()).hexdigest(),
                    supports_two_pass=supports_consolidation(session_or_404(session_id)['framework'] if session_id else framework),
                    consolidation_mode='channels' if uses_channel_consolidation(session_or_404(session_id)['framework'] if session_id else framework) else 'self_prompt',
                    cache_policy=cache_policy(model, cfg['cache_mode'], cfg['provider_pin_seconds']))

    @app.get("/api/sessions")
    async def sessions():
        return store.sessions()

    @app.post("/api/sessions")
    async def new_session(body: NewSession):
        cfg = settings()
        if body.mode == "live" and not (cfg["key"] and cfg["model"]):
            raise HTTPException(400, "Set OPENROUTER_API_KEY and OPENROUTER_MODEL in MC_Chat/.env, then create a new chat.")
        framework = read_framework()
        catalog_from_framework(framework)
        session = store.create_session(cfg["model"] if body.mode == "live" else "Local scripted demo",
                                       body.mode, framework, hashlib.sha256(framework.encode()).hexdigest())
        return session

    @app.get("/api/sessions/{sid}")
    async def get_session(sid: str):
        session = session_or_404(sid)
        return dict(**session, channels=catalog_from_framework(session["framework"]),
                    protocol=wire_protocol(session["framework"]), turns=store.turns(sid), active=sid in active,
                    supports_two_pass=supports_consolidation(session['framework']),
                    consolidation_mode='channels' if uses_channel_consolidation(session['framework']) else 'self_prompt',
                    routing=store.route(sid))

    @app.post("/api/sessions/{sid}/upgrade")
    async def upgrade_session(sid: str):
        session = session_or_404(sid)
        if sid in active:
            raise HTTPException(409, "Stop the running response before continuing in a word-stream chat.")
        framework = read_framework()
        catalog_from_framework(framework)
        framework_hash = hashlib.sha256(framework.encode()).hexdigest()
        if session['framework_hash'] == framework_hash:
            return session
        return store.fork_session(sid, framework, framework_hash)

    @app.get("/api/sessions/{sid}/export")
    async def export_session(sid: str):
        session = session_or_404(sid)
        session['protocol'] = wire_protocol(session['framework'])
        return JSONResponse(dict(format="mc-export/1", session=session, turns=store.turns(sid)),
                            headers={"Content-Disposition": 'attachment; filename="mc-chat.json"'})

    @app.post("/api/sessions/{sid}/cancel")
    async def cancel(sid: str):
        session_or_404(sid)
        entry = active.get(sid)
        if entry:
            entry["task"].cancel()
        return {"stopping": bool(entry)}

    @app.post("/api/sessions/{sid}/stream")
    async def stream(sid: str, body: ChatRequest):
        session = session_or_404(sid)
        if sid in active:
            raise HTTPException(409, "This conversation already has a running response.")
        catalog = catalog_from_framework(session["framework"])
        ids = {c["id"] for c in catalog}
        selected = sorted(set(body.channels) | {"answer"})
        direct = uses_channel_consolidation(session['framework'])
        if body.two_pass:
            if not supports_consolidation(session['framework']):
                raise HTTPException(422, 'Update this chat framework to use two-pass answers.')
            if not direct:
                selected = sorted(set(selected) | {'self_prompt'})
        elif 'self_prompt' in selected:
            raise HTTPException(422, 'Self Prompt requires two-pass mode.')
        if not set(selected).issubset(ids) or not set(body.notes).issubset(selected):
            raise HTTPException(422, "Select catalog channels; notes must belong to selected channels.")
        if any(len(v) > 2000 for v in body.notes.values()) or not body.prompt.strip():
            raise HTTPException(422, "Prompt cannot be blank; channel notes are limited to 2,000 characters each.")
        cfg = settings()
        if session["mode"] == "live" and not cfg["key"]:
            raise HTTPException(400, "Set OPENROUTER_API_KEY in .env.")
        rounds_required = requires_rounds(session['framework'])
        policy = cache_policy(session['model'], cfg['cache_mode'], cfg['provider_pin_seconds'])
        initial_channels = [c for c in selected if c != 'self_prompt']
        encoded_user = user_record(body.prompt, selected, body.notes,
                                  [c['number'] for c in catalog if c['id'] in initial_channels] if rounds_required else None,
                                  reinforce_format=uses_labeled_history(session['framework']), two_pass=body.two_pass, direct=direct)
        prior_turns = store.turns(sid)
        messages = build_messages(session, prior_turns, encoded_user, cfg["cache_mode"], policy['block'])
        payload = dict(model=session["model"], messages=messages, stream=True,
                       stream_options={"include_usage": True}, session_id=sid,
                       max_tokens=cfg["max_tokens"], temperature=cfg["temperature"],
                       reasoning=reasoning_request(body.thinking.enabled, body.thinking.effort))
        payload.update(policy['root'])
        route = prepare_route(store, sid, session['model'], policy['idle_seconds']) if session['mode'] == 'live' else None
        if route:
            payload = apply_route(payload, route)
        queue = asyncio.Queue()
        protocol = wire_protocol(session['framework'])
        parser = (ChannelParser({c['number']: c['id'] for c in catalog}, initial_channels if rounds_required else None,
                                terminal_channel='self_prompt' if body.two_pass and not direct else None,
                                allow_solo_layout=body.two_pass) if protocol == 'MC/2'
                  else JSONChannelParser(ids))
        turn = dict(id=str(uuid.uuid4()), created_at=now(), prompt=body.prompt, selected=selected,
                    notes=body.notes, user_content=encoded_user, status="streaming", events=[], raw="",
                    usage=None, channels={}, canonical=None, finish_reason=None, native_finish_reason=None, cache_mode=cfg["cache_mode"], protocol=protocol,
                    thinking=body.thinking.model_dump(), reasoning=payload['reasoning'], interleaving=None, repairs=0, participation=None,
                    generation={"model": payload['model'], "temperature": payload['temperature'],
                                "max_tokens": payload['max_tokens'], "reasoning": payload['reasoning'],
                                "sent_to_provider": session['mode'] == 'live'})
        turn.update(two_pass=body.two_pass, exploration_status='streaming', total_usage=None,
                    routing=dict(route) if route else None, provider_metadata={}, cache_policy=policy,
                    consolidation={'status': 'pending', 'prompt': None, 'raw': '', 'usage': None,
                                   'mode': 'channels' if direct else 'self_prompt', 'provider_metadata': {},
                                   'routing': None, 'finish_reason': None, 'native_finish_reason': None,
                                   'generation': dict(turn['generation'], sent_to_provider=False)} if body.two_pass else None)
        turn['provider_request'] = payload.get('provider')

        def emit(event):
            queue.put_nowait(event)

        def edits(events):
            for event in events:
                turn["events"].append(event)
                emit(event)

        async def produce():
            nonlocal route
            if session['mode'] == 'demo':
                source = demo_stream(selected, catalog, rounds_required, body.two_pass and not direct) if protocol == 'MC/2' else legacy_demo_stream(selected)
            else:
                source = (transport or openrouter_stream)(cfg, payload)
            emit(dict(type="start", turn_id=turn["id"], mode=session["mode"], model=session["model"],
                      protocol=protocol, framework_hash=session["framework_hash"],
                      history_turns=sum(t['status'] == 'complete' for t in prior_turns),
                      history_requests=len(prior_turns), generation=turn['generation'], two_pass=body.two_pass,
                      routing=turn['routing'], cache_policy=policy))
            try:
                done = False
                async for obj in source:
                    capture_metadata(turn['provider_metadata'], obj)
                    if obj.get("done"):
                        done = True
                        break
                    if obj.get("usage"):
                        turn["usage"] = obj["usage"]
                        emit(dict(type="usage", usage=obj["usage"]))
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    if choice.get("error"):
                        raise ProviderError("Provider failed during generation.")
                    turn["finish_reason"] = choice.get("finish_reason") or turn["finish_reason"]
                    if isinstance(choice.get('native_finish_reason'), str):
                        turn['native_finish_reason'] = choice['native_finish_reason'][:200]
                    delta = choice.get("delta") or {}
                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        # Limit raw retention before appending to avoid unbounded provider data.
                        if len(turn["raw"]) + len(content) > parser.MAX_OUTPUT:
                            raise ProtocolError("Response exceeded the character limit.")
                        turn["raw"] += content
                        edits(parser.feed(content))
                        emit(dict(type="raw", text=content))
                edits(parser.finish())
                if not done:
                    raise ProviderError("The provider stream ended unexpectedly before its completion marker.")
                if turn['finish_reason'] != 'stop':
                    raise ProviderError(finish_reason_message(turn['finish_reason'], 'Exploration'))
                if route:
                    route = await confirm_route(store, sid, route, turn['provider_metadata'], cfg['key'])
                    turn['routing'] = dict(route)
                    emit(dict(type='routing', phase=1, routing=turn['routing'], provider_metadata=turn['provider_metadata']))
                if parser.errors:
                    if getattr(parser, 'format_mismatch', False):
                        raise ProtocolError('Wrong reply format: the model returned JSON instead of the numbered word stream. Assistant output stays local; your request is retained for follow-ups.')
                    raise ProtocolError(f"Incomplete MC response: {parser.errors} unrecognized units. Valid text stays visible locally; your request is retained for follow-ups.")
                if not parser.channels.get("answer", "").strip():
                    raise ProtocolError("Incomplete MC response: no Answer content. Assistant output stays local; your request is retained for follow-ups.")
                empty_selected = [c for c in selected if not parser.channels.get(c, '').strip()]
                if requires_selected_content(session['framework']) and empty_selected:
                    raise ProtocolError('Selected channels produced no text: ' + ', '.join(empty_selected) +
                                        '. Skip/finish commands alone do not fulfil a selection. Assistant output stays local; your request is retained for follow-ups.')
                if rounds_required and parser.rounds.violations:
                    edits(parser.repair_missing_close())
                    if not parser.closing_repair:
                        raise ProtocolError(f"Interleaving failed: {parser.rounds.violations} round violations. Output is preserved for inspection, but excluded from model history.")
                mc_canonical = parser.canonical()
                if body.two_pass:
                    final = turn['consolidation']
                    if not direct and (not parser.channels.get('self_prompt', '').strip() or parser.statuses.get('self_prompt') != 'done'):
                        raise ProtocolError('Self Prompt must contain text and finish with 39_!done before the second call can start.')
                    turn['exploration_status'] = 'complete'
                    if route and not route.get('provider_slug'):
                        raise ProviderError('The first provider could not be identified for pinning. No second call was made. The exploration is saved locally; try again later.')
                    if direct:
                        final['rendered_channels'] = rendered_channels(mc_canonical, catalog)
                        final['request'] = final['prompt'] = consolidation_request(
                            body.prompt, body.notes, selected=selected, canonical=mc_canonical, catalog=catalog)
                        final_messages = build_channel_final_messages(session, prior_turns, encoded_user,
                            final['rendered_channels'], final['request'], cfg['cache_mode'], policy['block'])
                    else:
                        final['prompt'] = parser.channels['self_prompt']
                        final_messages = build_final_messages(prior_turns, body.prompt, mc_canonical,
                                                             final['prompt'], cfg['cache_mode'], policy['block'])
                    final_payload = dict(payload, messages=final_messages)
                    if route:
                        final_payload = apply_route(final_payload, route)
                        final['routing'] = dict(route)
                    final['provider_request'] = final_payload.get('provider')
                    await source.aclose()
                    final['status'] = 'streaming'
                    final['generation']['sent_to_provider'] = session['mode'] == 'live'
                    emit(dict(type='final_start', prompt=final['prompt'], generation=final['generation'],
                              exploration_usage=turn['usage'], mode=final['mode'], routing=final['routing']))
                    source = demo_final_stream(direct) if session['mode'] == 'demo' else (transport or openrouter_stream)(cfg, final_payload)
                    final_done = False
                    async for obj in source:
                        capture_metadata(final['provider_metadata'], obj)
                        if obj.get('done'):
                            final_done = True
                            break
                        if obj.get('usage'):
                            final['usage'] = obj['usage']
                            emit(dict(type='final_usage', usage=final['usage'],
                                      total_usage=combined_usage(turn['usage'], final['usage'])))
                        choices = obj.get('choices') or []
                        if not choices:
                            continue
                        choice = choices[0]
                        if choice.get('error'):
                            raise ProviderError('Provider failed during final-answer generation.')
                        final['finish_reason'] = choice.get('finish_reason') or final['finish_reason']
                        if isinstance(choice.get('native_finish_reason'), str):
                            final['native_finish_reason'] = choice['native_finish_reason'][:200]
                        text = (choice.get('delta') or {}).get('content')
                        if isinstance(text, str) and text:
                            if len(final['raw']) + len(text) > parser.MAX_OUTPUT:
                                raise ProtocolError('Final answer exceeded the character limit.')
                            final['raw'] += text
                            emit(dict(type='final_delta', text=text))
                    if not final_done:
                        raise ProviderError('The final-answer stream ended before its completion marker. Partial text stays local; your request is retained.')
                    if final['finish_reason'] != 'stop':
                        raise ProviderError(finish_reason_message(final['finish_reason'], 'Final answer'))
                    if not final['raw'].strip():
                        raise ProviderError('The final answer was empty. Your request is retained; no automatic retry was made.')
                    if route:
                        route = await confirm_route(store, sid, route, final['provider_metadata'], cfg['key'])
                        final['routing'] = dict(route)
                        emit(dict(type='routing', phase=2, routing=final['routing'], provider_metadata=final['provider_metadata']))
                    final['status'] = 'complete'
                    memory = json.loads(mc_canonical)
                    memory['final_answer'] = final['raw']
                    mc_canonical = json.dumps(memory, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                turn['exploration_status'] = 'complete'
                turn["status"] = "complete"
                turn["canonical"] = mc_canonical
            except asyncio.CancelledError:
                turn["status"] = "stopped"
                emit(dict(type="notice", message="Stopped. Partial output stays local; your request is retained for follow-ups."))
            except (ProviderError, ProtocolError) as exc:
                turn["status"] = "failed"
                turn["error"] = str(exc)
                emit(dict(type="error", message=str(exc)))
            except httpx.TimeoutException:
                turn["status"] = "failed"
                turn["error"] = "OpenRouter timed out. Partial output is kept locally; retry when ready."
                emit(dict(type="error", message=turn["error"]))
            except Exception:
                turn["status"] = "failed"
                turn["error"] = "The response could not be completed. Check the connection and provider settings."
                emit(dict(type="error", message=turn["error"]))
            finally:
                try:
                    await source.aclose()
                except Exception:
                    pass
                turn["channels"] = parser.channels
                if turn['exploration_status'] == 'streaming':
                    turn['exploration_status'] = turn['status']
                if body.two_pass:
                    final = turn['consolidation']
                    if final['status'] == 'pending':
                        final['status'] = 'skipped'
                    elif final['status'] == 'streaming':
                        final['status'] = turn['status']
                        final['error'] = turn.get('error')
                    turn['total_usage'] = combined_usage(turn['usage'], final['usage'])
                else:
                    turn['total_usage'] = combined_usage(turn['usage'])
                turn["statuses"] = parser.statuses
                turn['repairs'] = getattr(parser, 'repairs', 0)
                if requires_selected_content(session['framework']):
                    missing = [c for c in selected if not parser.channels.get(c, '').strip()]
                    turn['participation'] = dict(required=True, empty_channels=missing,
                                                 status='failed' if missing else 'passed')
                if rounds_required:
                    turn['interleaving'] = parser.interleaving_summary()
                    if (not done or turn['finish_reason'] != 'stop' or parser.errors) and not parser.rounds.violations:
                        turn['interleaving']['status'] = 'incomplete'
                try:
                    store.save_turn(sid, turn)
                    emit(dict(type="complete", status=turn["status"], turn_id=turn["id"],
                              channels=turn["channels"], usage=turn["usage"],
                              interleaving=turn['interleaving'], repairs=turn['repairs'], participation=turn['participation'],
                              exploration_status=turn['exploration_status'], consolidation=turn['consolidation'],
                              total_usage=turn['total_usage'], routing=turn['routing'], provider_metadata=turn['provider_metadata']))
                except Exception:
                    emit(dict(type="error", message="The local conversation could not be saved."))
                finally:
                    active.pop(sid, None)
                    emit(None)

        task = asyncio.create_task(produce())
        active[sid] = {"task": task}

        async def response_events():
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=10)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    if event is None:
                        break
                    yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        return StreamingResponse(response_events(), media_type="text/event-stream",
                                 headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")

    @app.get("/")
    async def index():
        return FileResponse(ROOT / "frontend" / "index.html")

    return app


app = create_app()
