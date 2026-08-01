"""`POST /clients` is a plain upsert -- most callers never hit this
directly since `POST /discovery` upserts the client as a side effect, but
it's here for registering/renaming a client (and setting `cron`) without
also kicking off a sweep.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from backend.db import clients as clients_db
from backend.engine import scheduler

router = APIRouter(tags=["clients"])


class ClientIn(BaseModel):
    client_id: str
    name: str
    keywords: list[str] = []
    cron: Optional[str] = None


@router.post("/clients")
async def upsert_client(body: ClientIn) -> dict:
    out = await clients_db.upsert(body.client_id, body.name, body.keywords, body.cron)
    scheduler.sync()
    return out


@router.get("/clients/{client_id}")
async def get_client(client_id: str) -> dict:
    return await clients_db.get(client_id)
