from __future__ import annotations

from fastapi import APIRouter

from backend.controllers import client_controller
from backend.dto.client_dto import ClientIn

router = APIRouter(tags=["clients"])


@router.post("/clients")
async def upsert_client(body: ClientIn) -> dict:
    return await client_controller.upsert_client(body)


@router.get("/clients/{client_id}")
async def get_client(client_id: str) -> dict:
    return await client_controller.get_client(client_id)
