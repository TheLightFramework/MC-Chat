# MC Chat

A local multi-channel chat laboratory: Python/FastAPI, OpenRouter streaming, and
a browser frontend with live numbered-word corrections (MC/2) and automatic
consolidation into Markdown. No Node build step or runtime CDN calls.

**Experimental open-source release · MIT license · MC Framework 2.6**

Choose perspectives such as Answer, Creativity, Imagery, Correctness, or Green IT.
Watch their words arrive in an interleaved stream, including explicit corrections,
then read a final Markdown answer guided by those perspectives. The app runs
locally and sends live model requests through OpenRouter. A scripted demo needs
no API key.

```text
Your request + selected channels
             ↓
Pass 1: numbered words, interleaved across channels
             ↓
Python parser: reconstruct channel text and apply backspaces
             ↓
Pass 2: channel findings + original request → final Markdown answer
```

The channels expose generated content, not a model's private reasoning. This is
an experiment in output structure, correction and consolidation; it is not a
benchmark claim of improved intelligence or factual accuracy. Protocol adherence
varies by model and settings. See [MCFramework.md](MCFramework.md) for the contract.

## Start on Windows

1. Install Python 3.10 or newer.
2. Copy the blank configuration template, then open `.env` and fill in your values:

   ```powershell
   Copy-Item .env.example .env
   ```

   Skip the copy if you already have a configured `.env`.

   ```dotenv
   OPENROUTER_API_KEY=your-key-here
   OPENROUTER_MODEL=provider/model-slug
   ```

   Use the exact slug shown by OpenRouter. No model is silently selected for you.
3. Run `./start.ps1` in PowerShell from this folder. It creates `.venv`, installs
   the runtime dependencies, and runs the server.
4. Open <http://127.0.0.1:8765>.

If your PowerShell policy prevents scripts, use these commands instead:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

On macOS/Linux: `python3 -m venv .venv`, `.venv/bin/python -m pip install -r requirements.txt`,
copy `.env.example` to `.env` if it does not already exist, then `.venv/bin/python run.py`.
Launch from this folder. The **Explore the live
demo** button works without a key. It is explicitly scripted, stays local, and
uses the same parser and frontend stream as real responses.

## Use

- Pick channels or a preset before sending. Answer is always included. The model
  is free to open any of the 38 ordinary channels, even when not selected.
- Use the small × on a channel chip above the message box to remove that
  preference for the next message. The picker stays in sync. Answer is required
  and has no remove button; unselecting other channels does not forbid the model
  from opening them. All selected chips remain available, scrolling when needed.
- Add an optional focus under a selected channel. For Green IT, for example:
  “Check peak RAM for 100,000 rows and avoid repeated allocations.”
- Watch channels unfold side by side. Amber flashes mark backspaces/clears;
  the event log retains removed text and the raw MC stream. Copy a channel, replay
  a response, or export the whole chat as JSON. Diagnostics stay in one collapsed
  row per response; repeated warnings are counted together, with sample rejected
  units. Expanded details scroll within a fixed-height panel. Full available events
  remain in the export, including on older chats.
- **Stop** cancels the upstream request. Partial output stays local and is excluded
  from subsequent model context. Provider billing may include work before the
  cancellation reaches it. Failed/truncated/invalid assistant output is also excluded.
  Your requests are retained even when a response fails or is stopped.
- A new chat snapshots `MCFramework.md` and pins the model slug. Changes to the
  framework or model take effect in new chats; key and generation settings are
  read on each request. System environment variables take precedence over `.env`.
- To add a channel, add a row to the catalog table in `MCFramework.md` using a
  unique positive number and lowercase/underscore ID, then create a new chat.
  Channel 1 must remain Answer; never renumber existing channels. No Python or JS edit is
  needed. Avoid a literal pipe inside a table cell.
- The model is prompted to follow preferences; neither its choice of channel nor
  its compliance with the protocol is guaranteed. Rejected events are reported.

## Two-pass answers (framework 2.6)

**Two-pass answer** is on by default in the frontend for new or upgraded chats.
Choose ordinary exploration channels as before. Their reconstructed content is
the brief for pass 2: no generated Self Prompt is needed. Uncheck Two-pass answer
for the original single-call experiment.
Every new exploration request ends with a `framework_reread` instruction asking
the model to reread the entire initial framework already in context before
responding, with a reminder about rotation and explicit channel closing. The
reminder also keeps prose word prefixes mandatory when only Answer remains,
clarifies escaped spaces and standalone punctuation, and defers finished poem
layout, Markdown and code formatting to pass 2. The
framework is not duplicated, older request records are not rewritten, and the
consolidation request still asks for normal Markdown. This is renewed instruction
emphasis, not a verifiable rereading operation. Strict validation remains active.
Older chats retain their pinned framework: choose **Update framework** to create
an upgraded copy, or start a new conversation. Original chats remain intact.

1. **Exploration:** the model interleaves numbered words in your chosen channels,
   with the existing correction and validation rules. Channels explore checks,
   examples and alternatives. The blanket instruction to keep contributions short
   has been removed; requested depth and useful content determine length.
2. **Reconstruction:** the backend finds every nonempty channel and serializes its
   corrected text in a stable, labeled form. Raw wire text remains in the audit.
   In two-pass mode, exact-layout payloads may contain multiple words when only
   one channel is active. With multiple active channels the one-word check remains.
3. **Consolidation:** valid exploration triggers one more OpenRouter call with the
   SAME framework and prior history. It appends reconstructed channels and a fixed
   consolidation request containing the original user prompt, channel notes,
   selected channel IDs and the definitions of all filled channels. Selected
   channels strongly guide both substance and presentation: Creativity and Imagery,
   for example, should contribute their actual ideas and metaphors to the answer,
   rather than disappear into a generic summary. Contributions are blended into
   the deliverable, subject to the original request and factual accuracy. Channel
   text is not duplicated in the instruction. This strengthens prompting; it does
   not mechanically guarantee a model's stylistic adherence.
   The framework explicitly switches between numbered exploration and ordinary
   Markdown according to the latest request's phase. No generated prompt is needed.

The new per-request reminders and consolidation guidance also apply to future
requests in existing direct-consolidation (2.5) chats after restarting the server.
Start a new chat or use **Update framework** for the full 2.6 system text. Earlier
saved requests, reconstructed channels and final answers stay unchanged, preserving
their history prefix. The legacy Self Prompt flow is unchanged.

Both calls use the same model, temperature, thinking setting and maximum-token
budget. The budget is per call, not a target length or shared cap. Two-pass mode
can use more time and tokens. There are no automatic retries. Stop cancels the
active call. Invalid or stopped exploration never triggers consolidation. If the
second pass fails, partial output stays local and the assistant turn is excluded
from future model history; the user request remains.

When consolidation starts, exploration folds into **Pass 1 · Channel exploration**.
Open it to inspect channels, corrections and raw text. The final answer
streams below with headings, lists, links, tables and fenced code. Copy code and
Copy Markdown preserve source text. The consolidation request and Markdown source
remain inspectable. Code is displayed, never executed. Local bundles of
[Marked](https://marked.js.org/) and [DOMPurify](https://github.com/cure53/DOMPurify)
parse and sanitize Markdown; scripts, forms, images, event handlers and unsafe
links are not rendered as active content. Licenses are in `frontend/vendor/`.

Exports retain `raw`, `events`, `channels`, `usage` and `generation` for pass 1.
New fields are `two_pass`, `exploration_status`, and `consolidation` (status, exact
request, reconstructed channels, Markdown `raw`, usage, finish reason and settings).
`total_usage` sums reported usage, labels how many passes reported, and leaves
fields unknown when one reported pass omits them. Demos make neither API call.
Completed history preserves channel findings and the final answer, so follow-ups
can refer to the text the user read. Failed assistant output stays local.

History grows in this order:

```text
MCFramework.md (same system text for both passes)
UserRequest1 (including selections and notes)
MultiChannel1-rendered
ConsolidationRequest1 (fixed instruction + original request)
SiblingAnswer1 (normal Markdown)
UserRequest2
...
```

The small consolidation instruction stays in history too: removing it would
change the prefix after pass 2. New turns save exact rendered channel text and
the exact consolidation request so later software changes do not rewrite them.
Raw MC output is replaced locally before it is sent as input for the next call;
this does not modify any provider cache entry in place. Generated text becomes
eligible for input caching when it is resent. Cache hits remain provider-reported.

Saved framework-2.4 chats still use Self Prompt and their original two-context
format. Upgrade or start a new chat for direct channel consolidation and the
shared prefix. Original chat records remain untouched. Provider affinity applies
to both versions. Channel findings can still be wrong: consolidation provides an
opportunity to integrate and review them, not a guarantee of correctness.

## Provider affinity and cache lifetime

The first request in a routing window lets OpenRouter select a provider. The app
records the response's provider and generation ID, resolves an official routing
slug, and persists the lock in SQLite. A uniquely identifiable endpoint is pinned
by its full slug; otherwise the provider's base slug is used, with OpenRouter's
sticky session retained for endpoint affinity. Both passes use the SAME routing ID.

Subsequent requests send `provider.only` and `allow_fallbacks: false`. A pinned
provider outage produces an error instead of silently switching or automatically
retrying. If the first provider cannot be identified, pass 2 is not sent unpinned.
Provider details, requested restrictions and routing IDs are included in exports;
the UI shows provider names, slugs, lock expiry, and reported cache-token usage.

`MC_PROVIDER_PIN_SECONDS=1800` sets a 30-minute inactivity window. Successful
provider completions renew it; failed calls do not. After expiry the next request
gets a fresh routing ID with no provider restriction, allowing OpenRouter to choose
again. Locks survive app restarts and are isolated per chat. A lock is a routing
policy, NOT proof that a provider still retains the prefix or supports caching.

In explicit cache mode, Claude receives the documented maximum `cache_control`
TTL of `1h`, and its routing window is at least one hour. OpenAI GPT-5.6 and newer
receive their documented `prompt_cache_options` TTL of `30m`. Other providers use
their managed retention: unsupported TTL fields are not guessed. See
[OpenRouter caching](https://openrouter.ai/docs/guides/best-practices/prompt-caching)
and [provider routing](https://openrouter.ai/docs/guides/routing/provider-selection).
Claude's longer retention increases the cache-write rate; it is not a free extension.

DeepInfra offers explicit retention on selected models, but its current published
support list does not include DeepSeek V4.1 Flash, and forwarding those controls
through OpenRouter is not documented. This app therefore cannot promise a chosen
DeepSeek cache TTL. See [DeepInfra retention](https://docs.deepinfra.com/chat/prompt-cache-retention).
Endpoint catalog flags can lag actual behavior: reported cache tokens take
precedence. A successful cache read is recorded on the routing window and shown
in the UI; it is not used to invent an unknown retention deadline.

## Protocol: numbered words and < backspace

The standalone `MCFramework.md` defines the complete MC/2 protocol and fixed
numbered catalog. Every generated word has a prefix, including consecutive words
on the same channel. Channel 1 is Answer, channel 2 is Truth. Selecting different
channels never changes their numbers. The current picker shows 38 ordinary channels;
older framework-2.4 chats also contain 39 · Self Prompt.

```text
1_The 2_Check: 1_result 2_!skip 1_is 2_!skip 1_5. 2_2+2=4. 1_<_1_4. 2_!done 1_!done
```

This reconstructs "The result is 4." in Answer and "Check: 2+2=4." in
Truth. The raw stream and deleted `5.` remain in the event log and exported JSON.

| Syntax | Operation |
|---|---|
| `N_word` | Append one word, with automatic spacing |
| `N_<` | Remove the last emitted unit from channel N |
| `N_<_K` | Remove the last K units |
| `N_<_K_replacement` | Remove K units and append one replacement payload |
| `N_~payload` | Append exactly, without automatic spacing |
| `N_!clear` | Clear this response's channel, retaining an audit record |
| `N_!done`, `N_!active` | Finish participation or rejoin the active rotation |
| `N_!skip` | Yield this channel's slot without adding text |

### Required interleaving (framework 2.1)

Every selected channel is initially active. Each active channel must contribute
one word/unit before any channel repeats. With Answer, Synthetic Feelings, and
Humor, a typical order is `1_word 7_word 33_word 1_word 7_word 33_word …`.
The validator checks one unit per channel per round, not a fixed numeric order.
The current request includes `round_channels` with the selected channel numbers.

Use `!skip` for a temporary empty slot and `!done` when a channel has finished;
there is no need to pad shorter channels. A spontaneous channel joins the current
round on its first unit. A finished channel may rejoin. Every command occupies
one slot, including a compact backspace/replacement, except that a repeated
`!done` on an already closed channel is an idempotent no-op. It remains in the
audit but neither occupies a slot nor reopens the channel. Explicit layout units also
occupy slots. Packing multiple escaped words into one payload is flagged.

The raw stream is never reordered to manufacture a successful experiment.
Round violations leave the output available for inspection, mark the turn failed,
and exclude it from subsequent model history. The UI and exports report completed
rounds, violations, and parser repairs separately. This validates what the model
actually generated; the app cannot force every provider to obey the prompt.

Framework 2.2 also requires **visible text in every explicitly selected channel**.
`!skip` followed by `!done` with no text no longer fulfils a selection. Humor should
offer fitting humor; if that would be inappropriate, it should briefly explain why
in its own channel. The backend checks nonempty content, not whether a joke is good.
Participation and round ordering are separate checks in the UI/export, so passing
rotation cannot hide an empty selected channel. Optional, model-opened channels do
not acquire the same required-content obligation.

### Narrow prefix recovery

A known channel that has already emitted text can recover a missing underscore
before an attached word, simple punctuation, balanced quoted payload, or benign
status command: `33“amazing”`, `33energy.`, `33—`, and `33!done` receive the missing
underscore locally. Clear/backspace commands are never inferred. A `repair`
audit entry records the original and normalized tokens, while `raw` stays intact.
It does not invalidate an otherwise valid response. Repair does not make a
non-interleaved response valid, and it is not counted as a model's own backspace.
Unknown channel numbers, unnumbered prose, and malformed commands remain errors.

A unit is one payload: usually a word, but escaped whitespace or standalone
punctuation also counts. Backspace removes each unit's inserted separator too.
It never splits a Unicode payload into code points. Invalid counts are rejected
without mutation. Underscores inside a payload remain literal, so
`2_[C139_Deception]` keeps the tag and `1_<_2_word1_rewritten` inserts the literal
`word1_rewritten`. For multiple replacement words use `1_<_2 1_first 1_second`.

Payload escapes: `\n`, `\r`, `\t`, `\s` (space), `\\` (literal backslash),
`\<`, `\!`, and `\~` (literal leading command characters). Physical whitespace
between wire units is only a separator. To create paragraphs use `1_~\n\n`.
Code can preserve exact indentation:

```text
13_~def 13_~\shello(): 13_~\n\s\s\s\sreturn 13_~\s"hi" 13_~\n
```

The parser buffers only the incomplete word at a network boundary. Each complete
unit updates the frontend immediately; the final unit is flushed at stream end.
The backend converts edits into its existing browser event schema: a backspace
carries the number of rendered code points to remove, plus `word_count` and
`source` for the original command. The model itself generates words, not JSON.

### Existing chats

MC/1 JSON chats retain their frozen framework and decoder so their original raw
streams, canonical history, and replay remain readable. Open one and choose
**Use word stream** to create an MC/2 copy with the same model and all saved turns.
Existing MC/2 chats with an older frozen framework offer **Update framework** to
copy their history into the latest rounds, participation, and history-format rules. Their old turns keep
their recorded format and are not retroactively relabeled as interleaving passes.
The original is unchanged; copied turns keep their protocol and source turn ID.
All user requests enter model context; only complete assistant responses do.
This runtime fix also applies to existing chats. After an old failed turn, its
retained request changes the subsequent cache prefix once; later requests reuse
that stable context. Saved turns are not rewritten. New replies use numbered words.
The framework change starts a new cache prefix; subsequent turns reuse the new
stable prefix. New conversations use MC/2 automatically.

Canonical history, exports, and internal browser transport remain structured JSON;
the generated raw output uses `N_word`. From framework 2.2 onward, history sent to
the model is labeled, quoted channel text rather than the local `mc-history/1`
JSON serialization. This avoids presenting that JSON shape as an assistant reply
to imitate. A concise `output_contract` in each new user record reinforces numbered
output and participation; previous user records are not rewritten. A JSON-shaped
reply produces one explicit format warning rather than a warning for every word,
and remains excluded from context. Raw output is preserved without reconstruction
into a falsely interleaved stream. Public checks and expressive
channels are not a private reasoning transcript or proof of independent parallel
execution. Provider-specific hidden reasoning fields are not displayed or stored.

## Thinking controls

The composer has a **Thinking** checkbox (initially off) and an **Effort** dropdown.
These are per-request provider settings, separate from the public MC channels.
Turning thinking off leaves Truth, Uncertainty, and other channel choices intact.

- Off sends `reasoning: {"enabled": false, "effort": "none", "exclude": true}`.
- On with Model default sends `reasoning: {"enabled": true, "exclude": true}`.
- On with an effort also sets `reasoning.effort` to that choice.

The public OpenRouter model catalog supplies supported efforts; for example a
model may offer Low, High, and Max. A model declaring mandatory reasoning has its
checkbox checked and locked. If metadata is unavailable, the UI offers the generic
efforts and states that support is unverified. The provider can reject or map a
setting. No automatic paid retries are made. Catalog metadata is cached for ten
minutes and queried without your API key.

The sidebar and each turn show actual reported reasoning tokens (missing counts
stay “Not reported”). If Off was requested but positive reasoning tokens are
reported, the turn shows a notice. `exclude: true` only suppresses returned hidden
reasoning; it is not what disables thinking. Zero reported tokens is the relevant
observable measure here, not proof about every internal computation.

Preferences persist in this browser and each submitted turn records `thinking`
(the controls) and `reasoning` (the exact requested API configuration) in exports.
New turns also record `generation`: the pinned model, requested temperature,
maximum tokens, reasoning parameters, and whether these were sent to a provider.
These are request settings, not a claim that the provider honored each parameter.
Temperature and token budget also appear below each new request. Older exports
have no recorded temperature; it is never inferred from the current `.env`.
Opening a chat restores its latest recorded settings; older turns say their
settings were not recorded. Controls are locked while a request runs. Local demos
record the choices for inspection but do not exercise provider reasoning.

The frozen framework and canonical history are not rewritten when effort changes.
Providers may nevertheless partition their cache by generation configuration.
Whether disabling thinking improves MC adherence is an experimental question;
compare rejected tokens and response quality as well as latency and token usage.

Reference: [OpenRouter reasoning controls](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens).

## Context and cache behavior

Each API call sends:

1. The exact frozen `MCFramework.md` as the system message.
2. Every saved user request (including its selections), and each valid assistant
   response's final corrected channels as labeled, quoted archived text. The canonical JSON
   stays local; older frozen framework profiles retain their original transport.
3. The new request and this turn's channel preferences.

History is append-only and stored in SQLite. Changing current channel selections
does not alter earlier messages. Raw events and removed text remain in the local
audit record, not in the model's next input. Exports include that raw record.

In `MC_CACHE_MODE=explicit`, the system block and up to the two most recent
completed assistant blocks receive `cache_control: {"type":"ephemeral"}`. There
are at most three markers. `session_id` stays constant to support OpenRouter's
provider affinity. `automatic` omits markers and leaves caching to the provider;
it does not disable provider-side automatic caching.

**Sending the same prefix makes it eligible for reuse; it does not guarantee a
cache hit.** Support, minimum token counts, expiry, routing, and pricing depend
on the provider/model. The framework is intentionally compact and can fall below
a model's minimum cache size. There is no padding or paid prewarming request.
Reconstructed channels first become cacheable input in pass 2 of the same turn.
The final Markdown answer joins the input on the next user turn, and subsequent
calls can reuse that extended prefix. In single-pass chats, reconstructed channels
first enter input on the next user turn. Generated output is not assumed to have
automatically cached a differently reconstructed representation.

The UI displays actual `usage.prompt_tokens_details.cached_tokens`,
`cache_write_tokens`, token counts, and cost when reported; missing fields remain
“Not reported”. Local persistence is distinct from the provider's temporary cache.
There is no automatic context truncation: start a new chat if you reach the model's
context limit. Very long chats and many active channels increase token costs.

References checked during implementation:
- [OpenRouter prompt caching](https://openrouter.ai/docs/guides/best-practices/prompt-caching)
- [OpenRouter streaming](https://openrouter.ai/docs/api_reference/streaming)
- [Chat completions API](https://openrouter.ai/docs/api/api-reference/chat/create-a-chat-completion)

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `OPENROUTER_API_KEY` | empty | Backend-only API key |
| `OPENROUTER_MODEL` | empty | Exact OpenRouter model slug, pinned per chat |
| `MC_CACHE_MODE` | `explicit` | `explicit` markers or provider-managed `automatic` |
| `MC_PROVIDER_PIN_SECONDS` | `1800` | Idle routing lock in seconds, 60–86400; extended to cover requested TTL where supported |
| `MC_MAX_TOKENS` | `8192` | Output budget, 128–65536; must also fit the selected model |
| `MC_TEMPERATURE` | `0.7` | Sampling temperature, 0–2 |
| `MC_TIMEOUT_SECONDS` | `120` | Upstream read timeout, 10–600 seconds |

If the provider rejects explicit cache blocks, try `automatic`. Non-MC output
produces visible protocol errors rather than being silently interpreted as valid
channels. No automatic retries incur additional API charges.

## Files and local data

- `backend/protocol.py`: incremental parser, validation, channel reducer.
- `backend/prompts.py`: catalog reader, immutable history and request assembly.
- `backend/provider.py`: HTTP streaming and SSE decoding.
- `backend/app.py`: API, cancellation, demo, browser assets.
- `backend/storage.py`: SQLite persistence.
- `backend/consolidation.py`: final-answer context, usage totals and scripted demo.
- `backend/routing.py`: persistent provider affinity, metadata resolution and supported TTL requests.
- `frontend/`: dependency-free HTML/CSS/JavaScript.
- `data/mc_chat.sqlite3`: local conversations including original corrected text.

The key never goes to the browser. `.env` and `data/` are gitignored. Chat data
is stored unencrypted locally; live prompts and canonical history go to OpenRouter
and its selected provider. The server binds to loopback, checks host/origin, and
renders channel text as plain text and final Markdown through an HTML sanitizer. This is a single-user
local application, not an authenticated public service. Run **one server worker**
so stream ownership and cancellation remain coherent. Stop the server before
backing up or removing its database.

## Provider completion diagnostics

Stop reasons are distinguished in both passes. `length` reports a token-budget
stop; `content_filter` reports a provider filter stop, without recommending more
tokens or claiming to know its trigger. Tool-call and provider-error stops have
their own messages. The normalized `finish_reason` and, when supplied, the
provider's `native_finish_reason` are saved in exports. A non-normal stop does not
renew the routing lock, trigger an automatic retry, or enter assistant history.
Existing saved error messages are retained as originally recorded.

## Verification

### Missing terminal close recovery

After a normal, complete provider response, the backend can infer **one missing
`N_!done`** in a short closing tail. The channel must have nonempty text ending
with sentence punctuation, every other channel must have text and explicitly
close, and at least two complete rounds must have been observed. After the
candidate channel's last complete round, each other channel may emit at most one text unit
before closing. The entire stream is then revalidated with the inferred close;
it must have zero syntax/rotation errors and exactly the same channel text.

Successful recovery allows consolidation and history to proceed. The UI says
**Interleaving repaired**; exports retain the original raw stream, edit events,
statuses and violation counts, alongside the inferred command, insertion position
(one-based wire unit, or the unit after EOF) and separate validation result.
This is a local parser repair, not a model-authored correction or backspace.
It does not recover truncated/filtered/interrupted responses, empty selected
channels, blockwise output, or multiple missing closes. Legacy Self Prompt mode
remains strict. Existing saved turns are not retroactively changed. No framework
upgrade is needed; restart the server to use the repair on new requests.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
node --check frontend/app.js
node --check frontend/markdown.js
```

The tests use a simulated OpenRouter transport, not paid calls. They cover every
packet split in numbered Unicode streams, word corrections and escapes, legacy
JSON decoding, chat upgrades, invalid events, provider SSE usage
and keepalives, canonical history reuse, persistence, error/truncation handling,
concurrent request rejection, cancellation, and local origin checks. Live provider
format adherence and cache hits must be measured with your configured model.

## Contributing and license

See [CONTRIBUTING.md](CONTRIBUTING.md) for local tests and useful bug reports,
[CHANGELOG.md](CHANGELOG.md) for release notes, and [LICENSE](LICENSE) for the MIT
license. Bundled frontend libraries retain their own license notices in
`frontend/vendor/`. OpenRouter and model providers are external services with
their own terms and pricing; an API key is not included.

The public source excludes `.env`, local chat databases, exported conversations,
development previews and virtual environments. Do not attach private chat exports
or credentials to public issues. Reproduce problems with a minimal synthetic prompt.
