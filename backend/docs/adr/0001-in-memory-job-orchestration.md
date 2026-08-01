# ADR 0001: In-memory, single-process job orchestration

## Status
Accepted

## Context
Discovery and analysis jobs need to track live progress, cancel cleanly,
and serialize access per platform (two scrapers racing on the same login
cookies get checkpointed). The pre-rebuild system already did this with an
in-process `JobManager` singleton — job state, subscriber queues, and
per-platform `asyncio.Lock`s all live in that one process's memory — and
explicitly requires a single API worker as a result.

A textbook "enterprise" rebuild would normally push this to a durable,
horizontally-scalable task queue (Celery/RQ/Arq + Redis), so job state
survives a restart and multiple API workers can share load.

## Decision
Keep the single-process, in-memory model. Job state lives in `JobManager`;
the actual scraping still runs in a separate child process per job (see
`engine/jobs.py`), but the job's bookkeeping (status, event log, locks)
does not survive a process restart, and only one API worker may run at a
time.

## Consequences
- Simpler: no new infra dependency, no serialization format for job state,
  no distributed-lock design for the per-platform mutual exclusion.
- A restart loses in-flight job history (not the scraped data itself —
  that's durable in Mongo — only the job's own progress log and status).
- Cannot horizontally scale the API beyond one worker for job throughput.
- `engine/discovery.py`/`engine/analysis.py` depend only on `JobManager`'s
  public interface, not its in-memory implementation, so swapping in a
  real queue later is an `engine/jobs.py` rewrite, not a cross-cutting
  change.

## Revisit when
Job throughput or availability requirements exceed what one worker can
serve, or a restart losing in-flight job history becomes a real operational
problem.
