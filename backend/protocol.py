"""Incremental numbered MC/2 word stream, with a legacy MC/1 JSON decoder."""
import json
import re
from .interleaving import RoundMonitor


class ProtocolError(ValueError):
    pass


class JSONChannelParser:
    MAX_LINE = 32_768
    MAX_OUTPUT = 1_000_000

    def __init__(self, channel_ids):
        self.allowed = set(channel_ids)
        self.buffer = ""
        self.channels = {}
        self.statuses = {}
        self.errors = 0
        self.received = 0
        self.sequence = 0

    def feed(self, chunk):
        self.received += len(chunk)
        if self.received > self.MAX_OUTPUT:
            raise ProtocolError("Response exceeded the 1,000,000 character limit.")
        self.buffer += chunk
        events = []
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            events.extend(self._line(line))
        if len(self.buffer) > self.MAX_LINE:
            raise ProtocolError("MC event exceeded the 32,768 character line limit.")
        return events

    def finish(self):
        line, self.buffer = self.buffer, ""
        return self._line(line)

    def _line(self, line):
        if not line.strip():
            return []
        if len(line) > self.MAX_LINE:
            raise ProtocolError("MC event exceeded the line limit.")
        try:
            def unique_object(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("Duplicate JSON field.")
                    result[key] = value
                return result

            event = json.loads(line, object_pairs_hook=unique_object)
            if not isinstance(event, dict) or len(event) != 2:
                raise ValueError("Expected channel plus exactly one operation.")
            channel = event.get("channel")
            if not isinstance(channel, str) or channel not in self.allowed:
                raise ValueError("Unknown channel; use a catalog ID.")
            operation = next(k for k in event if k != "channel")
            value = event[operation]
            before = self.channels.get(channel, "")
            removed = ""
            if operation in ("text", "replace"):
                if not isinstance(value, str):
                    raise ValueError("Text must be a JSON string.")
                # Lone surrogate escapes cannot be rendered or safely serialized as UTF-8.
                value.encode("utf-8")
                after = before + value if operation == "text" else value
                removed = before if operation == "replace" else ""
            elif operation == "backspace":
                if type(value) is not int or not 0 < value <= len(before):
                    raise ValueError("Backspace must be positive and within the channel's length.")
                after, removed = before[:-value], before[-value:]
            elif operation == "status":
                if value not in ("active", "done"):
                    raise ValueError("Status must be active or done.")
                after = before
            else:
                raise ValueError("Unknown operation.")
            self.channels[channel] = after
            self.statuses[channel] = value if operation == "status" else "active"
            self.sequence += 1
            return [{"type": "edit", "sequence": self.sequence, "channel": channel,
                     "operation": operation, "value": value, "removed": removed}]
        except (ValueError, TypeError, StopIteration) as exc:
            self.errors += 1
            return [{"type": "warning", "message": f"Rejected MC event: {exc}"}]

    def canonical(self):
        return json.dumps({"format": "mc-history/1", "channels": {
            k: v for k, v in sorted(self.channels.items()) if v
        }}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ChannelParser:
    """One whitespace-delimited N_payload unit at a time, independent of packets.

    Output events deliberately retain the existing browser reducer schema.
    Backspace events count rendered code points there; word_count/source record
    the original word-level command. Each stack entry includes inserted spacing.
    """
    MAX_TOKEN = 32_768
    MAX_OUTPUT = 1_000_000
    ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "s": " ", "\\": "\\",
               "<": "<", "!": "!", "~": "~"}

    def __init__(self, number_to_channel, active_channels=None, terminal_channel=None, allow_solo_layout=False):
        self.mapping = {int(n): slug for n, slug in number_to_channel.items()}
        if any(n <= 0 for n in self.mapping) or len(set(self.mapping.values())) != len(self.mapping):
            raise ValueError("Channel numbers and IDs must be unique and positive.")
        self.buffer = ""
        self.channels = {}
        self.statuses = {}
        self.units = {}
        self.errors = self.received = self.sequence = 0
        self.repairs = 0
        self.format_mismatch = False
        self.allow_solo_layout = allow_solo_layout or terminal_channel is not None
        self.initial_channels = tuple(active_channels) if active_channels is not None else None
        self.rounds = RoundMonitor(self.initial_channels, terminal_channel) if active_channels is not None else None
        self.wire_units = []
        self.closing_repair = None

    def feed(self, chunk):
        self.received += len(chunk)
        if self.received > self.MAX_OUTPUT:
            raise ProtocolError("Response exceeded the 1,000,000 character limit.")
        data = self.buffer + chunk
        events = []
        start = 0
        for match in re.finditer(r"\s+", data):
            if match.start() > start:
                events.extend(self._token(data[start:match.start()]))
            start = match.end()
        self.buffer = data[start:]
        if len(self.buffer) > self.MAX_TOKEN:
            raise ProtocolError("MC word exceeded the 32,768 character token limit.")
        return events

    def finish(self):
        token, self.buffer = self.buffer, ""
        events = self._token(token) if token else []
        if self.rounds:
            events.extend(self.rounds.finish())
        return events

    def repair_missing_close(self):
        """Infer one terminal close, after normal provider completion only.

        Keep the original monitor, statuses and edits untouched. This is a local
        validation repair, not a command claimed to have come from the model.
        The caller must check the provider's stop reason and selected content.
        """
        audit = self.rounds
        if (self.closing_repair or not audit or not audit.finished or self.errors
                or not audit.violations or audit.terminal_channel
                or audit.completed_rounds < 2 or len(audit.active) != 1):
            return []
        channel = next(iter(audit.active))
        text = self.channels.get(channel, '').strip()
        if not text or not re.search(r'''[.!?…。！？][”’"')\]]*$''', text):
            return []
        last = max((i for i, unit in enumerate(self.wire_units) if unit[1] == channel), default=-1)
        if last < 0:
            return []
        if not all(self.channels.get(slug, '').strip() for slug in audit.closed):
            return []
        replay = RoundMonitor(self.initial_channels)
        insertion = len(self.wire_units)
        tail_start = None
        for i, (token, slug, _) in enumerate(self.wire_units):
            done = token.partition('_')[2] == '!done'
            if not (done and slug in replay.closed) and slug in replay.seen:
                if i <= last or replay.active - replay.seen != {channel}:
                    return []
                insertion = i
                break
            rounds_before = replay.completed_rounds
            if replay.consume(slug, done):
                return []
            if i >= last and tail_start is None and replay.completed_rounds > rounds_before:
                tail_start = i + 1
        else:
            if replay.active - replay.seen != {channel}:
                return []
        if tail_start is None:
            return []
        # After the candidate's last complete round, allow only one final text
        # unit per other channel, then close. No rewinds, rejoins or skips.
        tail_text = set()
        for token, slug, operation in self.wire_units[tail_start:]:
            if operation == 'text' and slug not in tail_text:
                tail_text.add(slug)
            elif token.partition('_')[2] != '!done':
                return []
        number = next(n for n, slug in self.mapping.items() if slug == channel)
        inferred = f'{number}_!done'
        tokens = [unit[0] for unit in self.wire_units]
        tokens.insert(insertion, inferred)
        checked = ChannelParser(self.mapping, self.initial_channels, allow_solo_layout=self.allow_solo_layout)
        checked.feed(' '.join(tokens))
        checked.finish()
        if checked.errors or checked.rounds.violations or checked.rounds.active or checked.channels != self.channels:
            return []
        self.closing_repair = dict(kind='missing_terminal_close', channel=channel,
                                  inferred_source=inferred, before_unit=insertion + 1,
                                  validated=checked.rounds.summary())
        self.repairs += 1
        return [dict(type='repair', **self.closing_repair,
                     message=f'Locally inferred missing {inferred} before wire unit {insertion + 1}. '
                             'Revalidated all rounds; channel text is unchanged. Original stream and violations are retained.')]

    def interleaving_summary(self):
        summary = self.rounds.summary()
        if self.closing_repair:
            summary.update(status='repaired', original_status=summary['status'], closing_repair=self.closing_repair)
        return summary

    @classmethod
    def decode(cls, value):
        result = []
        i = 0
        while i < len(value):
            if value[i] == "\\":
                i += 1
                if i == len(value) or value[i] not in cls.ESCAPES:
                    raise ValueError("Unknown or unfinished escape; double a literal backslash.")
                result.append(cls.ESCAPES[value[i]])
            else:
                result.append(value[i])
            i += 1
        text = "".join(result)
        text.encode("utf-8")
        return text

    @staticmethod
    def join_unit(before, text, exact):
        if (exact or not before or before[-1].isspace() or text[0].isspace()
                or re.fullmatch(r"[.,;:!?)\]…}]+", text) or before[-1] in "([{“"):
            return text
        return " " + text

    def event(self, channel, operation, value, source, removed="", **metadata):
        self.sequence += 1
        self.statuses[channel] = value if operation == "status" else "active"
        return dict(type="edit", sequence=self.sequence, channel=channel,
                    operation=operation, value=value, removed=removed, source=source, **metadata)

    def append(self, channel, text, exact, source):
        before = self.channels.get(channel, "")
        unit = self.join_unit(before, text, exact)
        self.channels[channel] = before + unit
        self.units.setdefault(channel, []).append(unit)
        return self.event(channel, "text", unit, source)

    def _token(self, token):
        if self.format_mismatch:
            return []
        if not self.sequence and not self.errors and token.startswith(('{', '[')):
            self.format_mismatch = True
            self.errors = 1
            return [dict(type='warning', source=token.encode('utf-8',errors='backslashreplace').decode('utf-8'),
                         message='The model returned JSON instead of numbered MC words. Raw output is preserved; it is not treated as interleaving.')]
        original = token
        # A known, already-written channel number attached directly to a word,
        # punctuation, quoted payload, or benign status can recover a missing _.
        # Do not infer unknown channels, unnumbered prose, clear, or backspace.
        typo = re.fullmatch(r'([1-9][0-9]*)([^0-9_].*)', token)
        normalized = None
        if typo and len(typo[1]) < 10:
            slug = self.mapping.get(int(typo[1]))
            suffix = typo[2]
            quoted = re.fullmatch(r'''(?:“[^”]+”|‘[^’]+’|"[^"]+"|'[^']+')[.,!?;:]*''', suffix)
            safe = suffix[0].isalpha() or suffix in ('—','–','.',',',';',':','!','?','!done','!skip','!active') or quoted
            if slug and self.units.get(slug) and safe:
                normalized = typo[1] + '_' + suffix
                token = normalized
        events = self._decode_token(token)
        edits = [e for e in events if e['type'] == 'edit']
        if normalized:
            for event in events:
                event['source'] = original
        if normalized and edits:
            self.repairs += 1
            for edit in edits:
                edit.update(source=original, normalized_source=normalized)
            events.insert(0, dict(type='repair', source=original, normalized_source=normalized,
                                  channel=edits[0]['channel'], message=f'Recovered missing underscore: {original} → {normalized}'))
        if edits and self.rounds:
            self.wire_units.append((token, edits[0]['channel'], edits[0]['operation']))
            # In two-pass mode an exact-layout payload cannot bypass rotation
            # when there is nobody else left to rotate with. This also lets the
            # terminal self prompt quote code faithfully. Multi-channel slots
            # and legacy single-pass experiments retain their word check.
            solo_layout = (self.allow_solo_layout
                           and len(self.rounds.active | {edits[0]['channel']}) == 1
                           and token.partition('_')[2].startswith('~'))
            if not solo_layout and any(e['operation'] == 'text' and len(e['value'].split()) > 1 for e in edits):
                events.extend(self.rounds.issue('Interleaving violation: a payload contains multiple words; emit one word per slot.'))
            # A compact rewind+replacement is ONE wire unit, even though the
            # renderer receives two edits. All other commands also take one slot.
            events.extend(self.rounds.consume(edits[0]['channel'],
                          done=edits[0]['operation'] == 'status' and edits[0]['value'] == 'done'))
        return events

    def _decode_token(self, token):
        if len(token) > self.MAX_TOKEN:
            raise ProtocolError("MC word exceeded the token limit.")
        try:
            match = re.fullmatch(r"([1-9][0-9]*)_(.+)", token, re.DOTALL)
            if not match:
                raise ValueError("Every word needs a numbered prefix, such as 1_Hello.")
            number, payload = int(match[1]), match[2]
            if number not in self.mapping:
                raise ValueError(f"Channel {number} is not in this chat's catalog.")
            channel = self.mapping[number]
            before = self.channels.get(channel, "")
            if payload == '!skip':
                return [self.event(channel, 'skip', None, token)]
            if payload in ("!done", "!active"):
                self.channels.setdefault(channel, "")
                return [self.event(channel, "status", payload[1:], token)]
            if payload == "!clear":
                self.channels[channel] = ""
                self.units[channel] = []
                return [self.event(channel, "replace", "", token, removed=before)]
            if payload == "<" or payload.startswith("<_"):
                rewind = re.fullmatch(r"<(?:_([1-9][0-9]*)(?:_(.+))?)?", payload, re.DOTALL)
                if not rewind:
                    raise ValueError("Use N_<, N_<_K, or N_<_K_replacement.")
                count = int(rewind[1] or 1)
                units = self.units.get(channel, [])
                if count > len(units):
                    raise ValueError("Backspace exceeds this channel's emitted word count.")
                # Validate the whole compact operation before any mutation.
                replacement = rewind[2]
                exact = replacement is not None and replacement.startswith("~")
                decoded = self.decode(replacement[1:] if exact else replacement) if replacement is not None else None
                if decoded == "":
                    raise ValueError("Replacement payload cannot be empty.")
                removed = "".join(units[-count:])
                del units[-count:]
                self.channels[channel] = before[:-len(removed)]
                events = [self.event(channel, "backspace", len(removed), token,
                                     removed=removed, word_count=count)]
                if decoded is not None:
                    events.append(self.append(channel, decoded, exact, token))
                return events
            if payload.startswith("!"):
                raise ValueError("Unknown command. Escape a literal leading ! with a backslash.")
            exact = payload.startswith("~")
            text = self.decode(payload[1:] if exact else payload)
            if not text:
                raise ValueError("Empty word payload.")
            return [self.append(channel, text, exact, token)]
        except (ValueError, TypeError) as exc:
            self.errors += 1
            return [{"type": "warning", "source": token.encode('utf-8', errors='backslashreplace').decode('utf-8'),
                     "message": f"Rejected MC word: {exc}"}]

    def canonical(self):
        return json.dumps({"format": "mc-history/1", "channels": {
            k: v for k, v in sorted(self.channels.items()) if v
        }}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
