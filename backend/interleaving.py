"""Audit generation order; never shuffle output to manufacture interleaving."""


class RoundMonitor:
    def __init__(self, channels, terminal_channel=None):
        self.active = set(channels)
        self.terminal_channel = terminal_channel
        self.terminal_started = False
        self.closed = set()
        self.seen = set()
        self.completed_rounds = 0
        self.violations = 0
        self.slots = 0
        self.finished = False

    def issue(self, message):
        self.violations += 1
        # Keep every violation in the count, but bound repetitive UI notices.
        if self.violations > 8:
            return []
        return [dict(type='interleaving', message=message)]

    def consume(self, channel, done=False):
        # A redundant close is a no-op, not a new round participant. Text or
        # another active command still allows a finished channel to rejoin.
        if done and channel in self.closed:
            return []
        events = []
        if channel == self.terminal_channel:
            if self.active - {channel}:
                events.extend(self.issue('Self Prompt must come after every other channel has finished with !done.'))
            self.terminal_started = True
        elif self.terminal_started:
            events.extend(self.issue('Only Self Prompt may emit units after the final synthesis begins.'))
        if done:
            self.closed.add(channel)
        else:
            self.closed.discard(channel)
        self.slots += 1
        self.active.add(channel)  # Spontaneous channels join the current round.
        if channel in self.seen:
            missing = ', '.join(sorted(self.active - self.seen))
            events.extend(self.issue(f'Interleaving violation: {channel} repeated before {missing} had a turn.'))
            self.seen.clear()  # Resynchronize at the observed repetition, without reordering.
        self.seen.add(channel)
        if done:
            self.active.remove(channel)
        if self.active.issubset(self.seen):
            self.completed_rounds += 1
            self.seen.clear()
        return events

    def finish(self):
        if self.finished:
            return []
        self.finished = True
        if self.slots and self.seen and self.active - self.seen:
            return self.issue('Incomplete final round; missing: ' + ', '.join(sorted(self.active - self.seen)) + '.')
        return []

    def summary(self):
        return dict(required=True, status='failed' if self.violations else ('passed' if self.slots else 'empty'),
                    completed_rounds=self.completed_rounds, violations=self.violations, slots=self.slots)
