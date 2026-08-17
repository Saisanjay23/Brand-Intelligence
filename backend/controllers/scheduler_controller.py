from __future__ import annotations

from backend.services import round_robin_service


async def get_status() -> dict:
    engine = round_robin_service.status()
    clients = await round_robin_service.client_statuses()
    return {**engine, "clients": clients}


async def start_engine() -> dict:
    round_robin_service.start()
    return round_robin_service.status()


async def stop_engine() -> dict:
    round_robin_service.stop()
    return round_robin_service.status()


def set_autostart(enabled: bool) -> bool:
    return round_robin_service.set_autostart(enabled)
