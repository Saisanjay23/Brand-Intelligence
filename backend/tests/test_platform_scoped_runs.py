"""The Sweep and Re-run Analysis buttons' platform selector -- "All
Platforms" (blank/omitted) keeps the previous behaviour of sweeping/
analysing every ready platform in one job; naming one platform scopes that
single run to just it, leaving every other platform untouched.

Analysis already branched on `job.platform` before this feature (the
approve-triggered auto-queue always scoped to one platform); the real
change is on the discovery side, which previously ignored `job.platform`
entirely and swept every ready platform regardless of what was asked.
"""

from __future__ import annotations

import pytest

from backend.shared.errors import ValidationError


# Controller-level validation

def test_discovery_rejects_an_unknown_platform():
    from backend.controllers.discovery_controller import _validated_platform

    with pytest.raises(ValidationError):
        _validated_platform("myspace")


def test_discovery_accepts_every_real_discovery_capable_platform():
    from backend.controllers.discovery_controller import _validated_platform
    from backend.platforms import registry

    for platform_id, plat in registry.PLATFORMS.items():
        if plat.can_discover:
            assert _validated_platform(platform_id) == platform_id


def test_analysis_rejects_an_unknown_platform():
    from backend.controllers.analysis_controller import _validated_platform

    with pytest.raises(ValidationError):
        _validated_platform("myspace")


def test_analysis_accepts_every_registered_platform():
    from backend.controllers.analysis_controller import _validated_platform
    from backend.platforms import registry

    for platform_id in registry.PLATFORMS:
        assert _validated_platform(platform_id) == platform_id


@pytest.mark.asyncio
async def test_discovery_controller_passes_the_validated_platform_to_job_creation(monkeypatch):
    from backend.controllers import discovery_controller as ctl
    from backend.services.job_service import DISCOVERY

    captured = {}

    async def fake_get(client_id):
        return {"client_id": client_id}

    def fake_create(kind, client_id, params, *, platform=None, callback_url=""):
        captured.update(kind=kind, client_id=client_id, params=params, platform=platform)
        class _J:
            id = "j1"
            status = "queued"
        return _J()

    monkeypatch.setattr(ctl.client_service, "get", fake_get)
    monkeypatch.setattr(ctl.job_manager, "create", fake_create)

    from backend.dto.discovery_dto import DiscoveryIn
    body = DiscoveryIn(client_id="c1", keywords=["acme"], platform="facebook")
    await ctl.start_discovery(body)

    assert captured["kind"] == DISCOVERY
    assert captured["platform"] == "facebook"


@pytest.mark.asyncio
async def test_discovery_controller_leaves_platform_none_when_all_platforms_chosen(monkeypatch):
    from backend.controllers import discovery_controller as ctl

    captured = {}

    async def fake_get(client_id):
        return {"client_id": client_id}

    def fake_create(kind, client_id, params, *, platform=None, callback_url=""):
        captured["platform"] = platform
        class _J:
            id = "j1"
            status = "queued"
        return _J()

    monkeypatch.setattr(ctl.client_service, "get", fake_get)
    monkeypatch.setattr(ctl.job_manager, "create", fake_create)

    from backend.dto.discovery_dto import DiscoveryIn
    body = DiscoveryIn(client_id="c1", keywords=["acme"])  # platform omitted
    await ctl.start_discovery(body)
    assert captured["platform"] is None


@pytest.mark.asyncio
async def test_discovery_controller_rejects_unknown_platform_before_creating_a_job(monkeypatch):
    from backend.controllers import discovery_controller as ctl

    async def fake_get(client_id):
        return {"client_id": client_id}

    created = []
    monkeypatch.setattr(ctl.client_service, "get", fake_get)
    monkeypatch.setattr(ctl.job_manager, "create", lambda *a, **k: created.append(1))

    from backend.dto.discovery_dto import DiscoveryIn
    body = DiscoveryIn(client_id="c1", keywords=["acme"], platform="myspace")
    with pytest.raises(ValidationError):
        await ctl.start_discovery(body)
    assert not created, "an invalid platform must never reach job_manager.create"


@pytest.mark.asyncio
async def test_analysis_controller_passes_platform_and_force_together(monkeypatch):
    from backend.controllers import analysis_controller as ctl

    captured = {}

    async def fake_get(client_id):
        return {"client_id": client_id}

    def fake_create(kind, client_id, params, *, platform=None, callback_url=""):
        captured.update(params=params, platform=platform)
        class _J:
            id = "j1"
            status = "queued"
        return _J()

    monkeypatch.setattr(ctl.client_service, "get", fake_get)
    monkeypatch.setattr(ctl.job_manager, "create", fake_create)

    from backend.dto.analysis_dto import AnalysisIn
    body = AnalysisIn(client_id="c1", platform="instagram", force=True)
    await ctl.start_analysis(body)

    assert captured["platform"] == "instagram"
    assert captured["params"]["force"] is True


# Discovery_service: readiness scoping

@pytest.mark.asyncio
async def test_platform_readiness_with_no_scope_checks_every_platform(monkeypatch):
    from backend.services import discovery_service as svc
    import backend.platforms.registry as real_registry

    async def fake_state(plat):
        return "ready" if plat.id == "facebook" else "missing"

    # _platform_readiness imports `registry` LOCALLY (inside the function,
    # to dodge a real circular import -- see its own docstring), so the
    # module-level attribute is what has to be patched, not anything bound
    # on discovery_service itself.
    monkeypatch.setattr("backend.platforms.registry.session_state", fake_state)

    ready, skipped = await svc._platform_readiness()
    assert "facebook" in ready
    assert set(skipped) == set(real_registry.PLATFORMS) - {"facebook"}


@pytest.mark.asyncio
async def test_platform_readiness_scoped_to_one_platform_ignores_the_rest(monkeypatch):
    """The core of this feature: naming one platform must not even LOOK at
    the others' session state, let alone sweep them."""
    from backend.services import discovery_service as svc

    checked = []

    async def fake_state(plat):
        checked.append(plat.id)
        return "ready"

    monkeypatch.setattr("backend.platforms.registry.session_state", fake_state)

    ready, skipped = await svc._platform_readiness(only="instagram")
    assert ready == ["instagram"]
    assert skipped == {}
    assert checked == ["instagram"], f"checked platforms other than instagram: {checked}"


@pytest.mark.asyncio
async def test_platform_readiness_scoped_to_a_not_ready_platform_reports_why(monkeypatch):
    from backend.services import discovery_service as svc

    async def fake_state(plat):
        return "incomplete"

    monkeypatch.setattr("backend.platforms.registry.session_state", fake_state)

    ready, skipped = await svc._platform_readiness(only="twitter")
    assert ready == []
    assert skipped == {"twitter": "incomplete"}


@pytest.mark.asyncio
async def test_platform_readiness_scoped_to_a_bogus_platform_is_empty_not_an_error():
    """Defense in depth: the controller should already have rejected this,
    but the service layer must degrade safely rather than raising a
    KeyError if it's ever reached with a bad value some other way."""
    from backend.services import discovery_service as svc

    ready, skipped = await svc._platform_readiness(only="myspace")
    assert ready == []
    assert skipped == {}


@pytest.mark.asyncio
async def test_run_discovery_scoped_run_raises_a_specific_message_when_not_ready(monkeypatch):
    from backend.services import discovery_service as svc
    from backend.services.job_service import Job

    class _Mgr:
        async def emit(self, *a, **k):
            pass

    async def fake_state(plat):
        return "expired"

    monkeypatch.setattr("backend.platforms.registry.session_state", fake_state)
    monkeypatch.setattr("backend.services.job_service.JobManager", lambda: _Mgr())

    job = Job(id="j1", kind="discovery", client_id="c1", platform="facebook",
              params={"keywords": ["acme"]})
    with pytest.raises(RuntimeError, match=r"facebook.*expired"):
        await svc.run_discovery(job)


@pytest.mark.asyncio
async def test_run_discovery_unscoped_run_keeps_the_generic_message(monkeypatch):
    from backend.services import discovery_service as svc
    from backend.services.job_service import Job

    class _Mgr:
        async def emit(self, *a, **k):
            pass

    async def fake_state(plat):
        return "missing"

    monkeypatch.setattr("backend.platforms.registry.session_state", fake_state)
    monkeypatch.setattr("backend.services.job_service.JobManager", lambda: _Mgr())

    job = Job(id="j1", kind="discovery", client_id="c1", platform=None,
              params={"keywords": ["acme"]})
    with pytest.raises(RuntimeError, match="no platform has a ready session"):
        await svc.run_discovery(job)
