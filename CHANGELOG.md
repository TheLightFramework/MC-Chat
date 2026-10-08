# Changelog

## 0.1.0 — Initial public release

An experimental local application for interleaved multi-channel model output,
using MC Framework 2.6 and OpenRouter.

- Python/FastAPI backend and browser frontend with streaming channel cards.
- 38 selectable channels, per-request preferences and channel-specific notes.
- Numbered one-word units with round validation, skips, closes and rejoining.
- Word-level backspace and replacement commands with an unchanged raw audit.
- Automatic two-pass answers: reconstruct channels, then consolidate into Markdown.
- Explicit carry-through of selected channels into final substance and style.
- Provider affinity and cache-aware history with provider-reported usage metrics.
- Reasoning controls, cancellation, replay and local JSON exports.
- Compact diagnostics and narrowly logged parser repairs.
- A scripted local demo with no paid model calls.
- 158 automated tests passing at release preparation, using simulated providers.

### Known limits

Models can violate the wire format even with high reasoning enabled. Passing
validation establishes protocol compliance, not correctness of the content.
Backspace is supported but models may never choose to emit one. Two-pass mode
uses two calls, and cache hits are provider-dependent. History grows without
automatic truncation. This is a local, single-user app, not an authenticated
internet-facing service. The multi-channel protocol does not reveal private
reasoning or establish parallel internal model execution.
