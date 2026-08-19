from __future__ import annotations

from fastapi import APIRouter

from backend.controllers import client_controller
from backend.dto.client_dto import ClientIn, ReorderIn

router = APIRouter(tags=["clients"])


@router.post("/clients")
async def upsert_client(body: ClientIn) -> dict:
    return await client_controller.upsert_client(body)


@router.get("/clients")
async def list_clients() -> dict:
    return await client_controller.list_clients()


@router.put("/clients/reorder")
async def reorder_clients(body: ReorderIn) -> dict:
    """The Scheduler tab's drag-to-reorder: `client_ids` is the full new
    order, front to back. This is what the round-robin engine's rotation
    (and the Scheduler tab's own listing) actually processes clients in --
    see client_repository.list_all's sort."""
    return {"items": await client_controller.reorder_clients(body.client_ids)}


@router.get("/clients/{client_id}")
async def get_client(client_id: str) -> dict:
    return await client_controller.get_client(client_id)


@router.delete("/clients/{client_id}")
async def delete_client(client_id: str) -> dict:
    return await client_controller.delete_client(client_id)
