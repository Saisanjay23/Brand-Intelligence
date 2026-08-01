"""Placeholder: NOT the job-callback webhook delivery already in use --
that's `services/webhook_service.py`, core job-lifecycle orchestration
consumed directly by `services/job_service.py`, and it stays there because
it isn't a "future" integration, it's a shipping feature. This stub is
reserved for a genuinely new, separate use of outbound webhooks (e.g. a
generic third-party event bus) should one come up.
"""

from __future__ import annotations
