"""Third-party service integrations -- none are implemented yet.

Distinct from `services/webhook_service.py`, which stays where it is: that
module is core job-lifecycle orchestration (delivering a job's own
completion callback) already in active use by `services/job_service.py`,
not a "future integration" with an external product. Each stub below is a
placeholder for a genuinely new third-party integration that does not
exist in the codebase today.
"""

from __future__ import annotations
