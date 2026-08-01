"""Request shape for POST /analysis."""

from __future__ import annotations

from pydantic import BaseModel


class AnalysisIn(BaseModel):
    client_id: str
    callback_url: str = ""
