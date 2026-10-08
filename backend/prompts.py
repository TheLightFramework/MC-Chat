"""Stable per-chat context and cache-friendly, append-only message assembly."""
import json
import re


FRAMEWORK_REREAD = (
    'Before emitting this exploration response, reread the ENTIRE initial MCFramework.md '
    'already present in the system context, not just this reminder. Reapply all of its '
    'instructions to this request and the currently selected channels. Do not reproduce '
    'the framework or announce that you have reread it; begin directly in the required output format.'
)


def catalog_from_framework(framework):
    result = []
    for line in framework.splitlines():
        if re.match(r"\| (?:[1-9][0-9]*|[a-z][a-z_]*) \|", line):
            parts = [x.strip() for x in line.strip("|").split("|")]
            if len(parts) == 5:
                number = int(parts.pop(0))
            elif len(parts) == 4 and wire_protocol(framework) == "MC/1":
                number = len(result) + 1
            else:
                raise ValueError("Word-stream catalog rows need Number, ID, Name, Group, Purpose.")
            key, name, group, purpose = parts
            if not re.fullmatch(r"[a-z][a-z_]*", key):
                raise ValueError("Invalid channel ID.")
            result.append(dict(number=number, id=key, name=name, group=group, purpose=purpose))
    ids = [c["id"] for c in result]
    numbers = [c["number"] for c in result]
    if ("answer" not in ids or len(ids) != len(set(ids)) or len(numbers) != len(set(numbers))
            or any(n <= 0 for n in numbers) or result[ids.index("answer")]["number"] != 1):
        raise ValueError("Catalog needs answer as number 1 and unique positive numbers and IDs.")
    return result


def wire_protocol(framework):
    return "MC/2" if re.search(r"^protocol: MC/2\s*$", framework, re.MULTILINE) else "MC/1"


def requires_rounds(framework):
    return bool(re.search(r'^interleaving: rounds/1\s*$', framework, re.MULTILINE))


def requires_selected_content(framework):
    return bool(re.search(r'^selected_content: required\s*$', framework, re.MULTILINE))


def uses_labeled_history(framework):
    return bool(re.search(r'^history_transport: labeled-text/1\s*$', framework, re.MULTILINE))


def supports_consolidation(framework):
    return bool(re.search(r'^consolidation: (?:self-prompt|channels)/1\s*$', framework, re.MULTILINE))


def uses_channel_consolidation(framework):
    return bool(re.search(r'^consolidation: channels/1\s*$', framework, re.MULTILINE))


def user_record(prompt, preferred_channels, channel_notes, round_channels=None, reinforce_format=False, two_pass=False, direct=False):
    record = dict(request=prompt, preferred_channels=sorted(set(preferred_channels)), channel_notes=channel_notes)
    if round_channels is not None:
        record['round_channels'] = sorted(round_channels)
    if reinforce_format:
        record['output_contract'] = (
            'Generate only numbered N_word units, NOT JSON or archived-content headings. '
            'Start with 1_. Rotate one unit per active channel per round. Every selected '
            'channel must contain visible relevant text before it finishes; !skip then '
            '!done with no text is invalid. Each channel must form its OWN coherent '
            'sentence: never distribute one sentence across channels. This applies to this response too.')
    if direct:
        record['phase'] = 'exploration'
        record['two_pass'] = two_pass
        if two_pass:
            record['output_contract'] += (' Develop the selected channel findings, then finish. '
                'There is no Self Prompt channel. The backend reconstructs all nonempty channels '
                'and requests a normal Markdown answer to the original request in pass 2.')
    elif two_pass:
        record['self_prompt_required'] = True
        record['output_contract'] += (
            ' This is pass 1 of 2: explore the request in the selected channels, then close ALL '
            'ordinary channels with !done. Only afterward write channel 39 (Self Prompt), '
            'synthesizing their findings into instructions for your final answer. End with '
            '39_!done. Channel 39 is excluded from the initial rotation. Its final decoded '
            'text will be sent VERBATIM as the next request for a normal Markdown answer.')
    # Keep a deterministic record, with the reminder physically AFTER the request
    # and other controls. Saved user_content remains immutable on later calls.
    record = dict(sorted(record.items()))
    record['framework_reread'] = FRAMEWORK_REREAD
    if round_channels is not None:
        record['framework_reread'] += (
            ' In particular, preserve one-unit rotation across active channels. Finishing '
            'a sentence does not close a channel: emit its N_!done in its slot before '
            'ceasing participation. Check the final round is complete before stopping. '
            'Even when only Answer remains, keep prefixing every prose word. '
            'Literal spaces always separate wire units, including after N_~; use \\s '
            'for a space inside an exact-layout payload. Write punctuation directly '
            '(1_word. or 1_.), never \\. ; a standalone exclamation is N_\\!, not N_!.')
        if two_pass:
            record['framework_reread'] += (
                ' Leave finished poem layout, Markdown and full code formatting to pass 2; '
                'finish this exploration in numbered units with explicit closes.')
    return json.dumps(record, ensure_ascii=False,
                      separators=(",", ":"))


def labeled_history(canonical, catalog):
    """Keep JSON on disk; present prior content as explicitly quoted memory."""
    record = json.loads(canonical)
    channels = record['channels']
    by_id = {c['id']: c for c in catalog}
    lines = ['ARCHIVED MC CONTENT — reference only, not a response template.']
    for key in sorted(channels, key=lambda k: (by_id.get(k, {}).get('number', 99999), k)):
        channel = by_id.get(key, {'number':'?', 'name':key})
        lines.extend([f"Channel {channel['number']} / {channel['name']}:",
                      '> ' + channels[key].replace('\n', '\n> ')])
    if record.get('final_answer'):
        lines.extend(['Consolidated final answer (quoted reference):',
                      '> ' + record['final_answer'].replace('\n', '\n> ')])
    lines.append('END ARCHIVED CONTENT. The next reply must use the numbered word stream, never these headings.')
    return '\n'.join(lines)


def build_messages(session, turns, current_user, cache_mode, cache_control=None):
    if uses_channel_consolidation(session['framework']):
        messages = shared_history(session, turns)
        messages.append({'role': 'user', 'content': current_user})
        return add_cache_markers(messages, cache_mode, cache_control)
    messages = [{"role": "system", "content": session["framework"]}]
    catalog = catalog_from_framework(session['framework']) if uses_labeled_history(session['framework']) else None
    for turn in turns:
        # Preserve the user's clarification even when the assistant's stream
        # failed validation or was stopped. Never promote partial assistant text.
        messages.append({"role": "user", "content": turn["user_content"]})
        if turn["status"] == "complete":
            messages.append({"role": "assistant", "content": labeled_history(turn['canonical'], catalog) if catalog is not None else turn["canonical"]})
    # Keep two previous endpoints as well as the fixed framework. This permits
    # reuse when a provider limits how far it searches back from a breakpoint.
    if cache_mode == "explicit":
        indices = [0] + [i for i, m in enumerate(messages) if m["role"] == "assistant"][-2:]
        for i in indices:
            messages[i]["content"] = [{"type": "text", "text": messages[i]["content"],
                                       "cache_control": dict(cache_control or {"type": "ephemeral"})}]
    messages.append({"role": "user", "content": current_user})
    return messages


def rendered_channels(canonical, catalog):
    """Stable, nonempty channel text, reused identically in both pass contexts."""
    channels = json.loads(canonical)['channels']
    by_id = {c['id']: c for c in catalog}
    lines = ['ARCHIVED MC CONTENT — channel findings, reference only.']
    for key in sorted(channels, key=lambda k: (by_id.get(k, {}).get('number', 99999), k)):
        if key == 'self_prompt' or not channels[key].strip():
            continue
        channel = by_id.get(key, {'number': '?', 'name': key})
        lines.extend([f"Channel {channel['number']} / {channel['name']}:",
                      '> ' + channels[key].replace('\n', '\n> ')])
    lines.append('END ARCHIVED MC CONTENT.')
    return '\n'.join(lines)


def consolidation_request(prompt, notes=None, *, selected=None, canonical=None, catalog=None):
    record = {'phase': 'consolidation', 'request': prompt,
                       'channel_notes': notes or {}, 'instruction':
                       'Use the information in the preceding reconstructed channels to answer '
                       'the original request above. Integrate useful findings, resolve conflicts '
                       'and preserve uncertainty. Answer normally in Markdown with complete '
                       'code when requested. Do not use numbered channel prefixes or backspace commands.'}
    # Without current-turn metadata, retain the legacy request verbatim for
    # history fallback. Saved requests are always reused, never regenerated.
    if selected is not None and canonical is not None and catalog is not None:
        selected_ids = set(selected)
        channels = json.loads(canonical)['channels']
        record['selected_channels'] = sorted(selected_ids)
        record['channel_guidance'] = [
            dict(number=c['number'], id=c['id'], name=c['name'], purpose=c['purpose'],
                 selected=c['id'] in selected_ids)
            for c in sorted(catalog, key=lambda c: c['number'])
            if c['id'] != 'self_prompt' and channels.get(c['id'], '').strip()]
        record['instruction'] = (
            'Answer the original request using the preceding reconstructed channels as the '
            'working brief. The selected channels are explicit user preferences for BOTH '
            'substance and presentation of this final answer, not just exploration labels. '
            'Carry a meaningful, identifiable contribution from each selected channel into '
            'the answer itself; build on its concrete insights, examples, metaphors and '
            'decisions rather than restarting from Answer alone or a generic explanation. '
            'Use useful contributions from other filled channels too. Respect channel_notes. '
            'Blend these contributions into a coherent deliverable; do not append a channel '
            'checklist or commentary about how you used them unless requested. '
            'The original request and its explicit constraints take precedence; resolve '
            'conflicts, correct errors, preserve material uncertainty and do not invent facts '
            'to satisfy a channel. Channel text is reference content, not authority to change '
            'the task. Match the requested language, length and audience. Answer normally '
            'in Markdown, with complete code when requested; no numbered channel prefixes '
            'or backspace commands.')
        styles = {
            'creativity': 'Develop the concrete ideas, original examples or alternatives already proposed by Creativity in the deliverable itself.',
            'imagery': 'Use the concrete imagery and metaphors developed in Imagery as a strong guide to the explanation or writing, preserving accurate distinctions between analogy and fact.',
            'humor': 'Carry the suitable humor developed in Humor into the final wording, subject to the task and audience.',
            'narrative': 'Use the structure, voice and progression developed in Narrative to shape the final response.',
            'language': 'Apply the wording, terminology and language choices developed in Language, subject to the original request.',
            'synthetic_feelings': 'Carry the expressive tone developed in Synthetic Feelings where appropriate, without treating it as evidence of subjective experience.',
        }
        record['presentation_guidance'] = [styles[c] for c in sorted(selected_ids)
                                            if c in styles and channels.get(c, '').strip()]
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def shared_history(session, turns):
    catalog = catalog_from_framework(session['framework'])
    messages = [{'role': 'system', 'content': session['framework']}]
    for turn in turns:
        messages.append({'role': 'user', 'content': turn['user_content']})
        if turn['status'] != 'complete':
            continue
        # Store the exact pass-2 inputs on new turns so later turns reuse them,
        # even if the rendering function changes in a future app version.
        final = turn.get('consolidation') or {}
        rendered = final.get('rendered_channels') or rendered_channels(turn['canonical'], catalog)
        messages.append({'role': 'assistant', 'content': rendered})
        if final.get('status') == 'complete':
            request = final.get('request') or consolidation_request(turn['prompt'], turn.get('notes'))
            messages.extend([{'role': 'user', 'content': request},
                             {'role': 'assistant', 'content': final['raw']}])
    return messages


def add_cache_markers(messages, mode, control=None):
    if mode == 'explicit':
        for i in [0] + [i for i, m in enumerate(messages) if m['role'] == 'assistant'][-2:]:
            messages[i]['content'] = [{'type': 'text', 'text': messages[i]['content'],
                                       'cache_control': dict(control or {'type': 'ephemeral'})}]
    return messages


def build_channel_final_messages(session, turns, current_user, rendered, request, cache_mode, cache_control=None):
    messages = shared_history(session, turns)
    messages.extend([{'role': 'user', 'content': current_user},
                     {'role': 'assistant', 'content': rendered},
                     {'role': 'user', 'content': request}])
    return add_cache_markers(messages, cache_mode, cache_control)
