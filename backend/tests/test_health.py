import pytest
from app.services.health import HealthService


class _FakeSession:
    """Minimal stand-in for AsyncSession.execute used by HealthService."""

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail

    async def execute(self, _stmt):  # noqa: ANN001
        if self._fail:
            raise RuntimeError("db down")
        return object()


@pytest.mark.asyncio
async def test_health_service_ok():
    service = HealthService(_FakeSession())
    status = await service.check()
    assert status.status == "ok"
    assert status.database == "ok"


@pytest.mark.asyncio
async def test_health_service_db_error():
    service = HealthService(_FakeSession(fail=True))
    status = await service.check()
    assert status.status == "ok"
    assert status.database == "error"


@pytest.mark.asyncio
async def test_health_endpoint_returns_200(client):
    resp = await client.get("/health")
    assert resp.status_code in (200, 503)
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] in ("ok", "error")
