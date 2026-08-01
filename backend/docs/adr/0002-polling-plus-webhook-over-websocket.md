# ADR 0002: Polling + webhook callback, not WebSocket

## Status
Accepted

## Context
The pre-rebuild system pushed job progress over a resumable WebSocket
(numbered events, replay via `?after_seq=N`) to a live React UI. This
engine has no UI at all; the sole consumer is the SaaS backend that
integrates it, submitting a discovery/analysis job and wanting the result
back.

## Decision
Drop the WebSocket. Expose the same numbered event log through polling
(`GET /jobs/{id}`, `GET /jobs/{id}/events?after_seq=N`), and add an
optional `callback_url` on job creation: when the job reaches a terminal
state, the engine POSTs its final summary there (with retries/backoff).

## Consequences
- The calling SaaS backend doesn't need a WebSocket client or a held-open
  connection; a plain HTTP POST endpoint on its side is enough to receive
  the result.
- Polling remains available and is the source of truth either way —
  `callback_url` is a convenience push, not a replacement for `GET /jobs/{id}`.
- The resumable-replay semantics the WebSocket provided are preserved
  exactly: events are still numbered on the `Job`, so a caller that missed
  some by polling late just asks for everything after the last seq it saw.
- If a future consumer genuinely needs sub-second live progress (not this
  system's actual requirement), that's a case for adding Server-Sent Events
  or a WebSocket back — the numbered-event design underneath doesn't change
  either way.
