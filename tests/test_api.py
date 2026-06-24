"""Tests for FastAPI backend endpoints."""

import pytest
from httpx import ASGITransport, AsyncClient

from api.main import app, _active_missions, _ws_clients


@pytest.fixture(autouse=True)
def clean_state():
    """Reset in-memory state between tests."""
    _active_missions.clear()
    _ws_clients.clear()
    yield
    _active_missions.clear()
    _ws_clients.clear()


@pytest.mark.asyncio
async def test_health():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_list_missions_empty():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/missions")
        assert resp.status_code == 200
        assert resp.json() == []


@pytest.mark.asyncio
async def test_create_mission():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/missions", json={
            "runfile_path": "runfiles/test_mission.yaml",
            "sample_only": False,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "mission_id" in data
        assert data["status"] == "pending"


@pytest.mark.asyncio
async def test_create_mission_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/missions", json={
            "runfile_path": "runfiles/nonexistent.yaml",
        })
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_mission_path_traversal_blocked():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/missions", json={
            "runfile_path": "../../etc/passwd",
        })
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_mission():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_resp = await client.post("/missions", json={
            "runfile_path": "runfiles/test_mission.yaml",
        })
        mission_id = create_resp.json()["mission_id"]

        resp = await client.get(f"/missions/{mission_id}")
        assert resp.status_code == 200
        assert resp.json()["mission_id"] == mission_id


@pytest.mark.asyncio
async def test_get_mission_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/missions/nonexistent")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_approve_mission():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_resp = await client.post("/missions", json={
            "runfile_path": "runfiles/test_mission.yaml",
        })
        mission_id = create_resp.json()["mission_id"]

        resp = await client.post(f"/missions/{mission_id}/approve", json={
            "action": "approve",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_abort_mission():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_resp = await client.post("/missions", json={
            "runfile_path": "runfiles/test_mission.yaml",
        })
        mission_id = create_resp.json()["mission_id"]

        resp = await client.post(f"/missions/{mission_id}/approve", json={
            "action": "abort",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "aborted"


@pytest.mark.asyncio
async def test_approve_mission_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/missions/nonexistent/approve", json={
            "action": "approve",
        })
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_skills_empty():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/skills")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_get_traces():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_resp = await client.post("/missions", json={
            "runfile_path": "runfiles/test_mission.yaml",
        })
        mission_id = create_resp.json()["mission_id"]

        resp = await client.get(f"/missions/{mission_id}/traces")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
