# ADR 0005: No auth/rate-limit layer — internal SaaS integration only

## Status
Accepted

## Context
This engine is not a standalone public API. It's one feature of an
existing SaaS product: that SaaS's own backend calls this engine directly
over a trusted internal path (same network / same deployment), and it
already owns authenticating its own end users, rate-limiting them, and
presenting its own error shape back to them before any of that reaches
here. [ADR 0003](0003-api-key-auth.md) added a second, redundant
authentication layer on the assumption this was reachable by an arbitrary
caller — it wasn't, once the actual integration shape became clear.

## Decision
Drop API-key auth, rate limiting, and the structured `{"error": {code,
message, correlation_id}}` envelope entirely. `main.py` registers no auth
middleware. Engine/db code raises one of a small set of plain exceptions
(`shared/errors.py`: `NotFoundError`, `ValidationError`, `ConflictError`,
`UpstreamPlatformError`), and the one exception handler in `main.py` maps
each to FastAPI's own default shape — `{"detail": "message"}` with a
normal HTTP status code. No custom envelope, no correlation id threaded
through the response body.

A lightweight `api/middleware/request_logging.py` middleware still logs
method/path/status/duration as structured JSON and increments Prometheus
counters — that's this process's own operability, independent of who's
authenticating the caller, so it stays.

## Consequences
- Nothing in this codebase is a security boundary against an untrusted
  caller. Deploying this so it's reachable from anywhere other than the
  SaaS backend's own trusted network is a deployment mistake, not
  something the code will catch.
- Simpler error handling everywhere: raise a plain exception, get a plain
  HTTP response, no envelope to construct or parse on either side.
- `shared/security/api_key.py` and its `api_keys` Mongo collection are
  gone — there's no key issuance flow to operate.
- `/health/*` and `/metrics` needed no special-casing anymore since
  nothing else is gated either.

## Revisit when
This engine is ever exposed to a caller this SaaS backend doesn't already
authenticate — a public API, a different internal service that isn't the
one trusted caller, or a multi-tenant deployment where the caller itself
needs to be verified. At that point, reintroduce a boundary at the actual
new trust edge (an API key, mTLS, or OAuth2 client-credentials, per what
that caller can support) rather than assuming today's trusted-path
decision still holds.
