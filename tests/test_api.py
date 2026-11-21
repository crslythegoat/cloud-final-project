import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import get_settings
from app.main import app
from app.models import Order, Product
from app.runtime import LiveState, cluster_for_position, live


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CLOUD_DB_API_TOKEN", "test-operator-token")
    monkeypatch.setenv("CLOUD_DB_BOT_TOKENS", '{"bot-1":"test-bot-token"}')
    monkeypatch.setenv("CLOUD_DB_ENVIRONMENT", "test")
    get_settings.cache_clear()
    options = {}
    if os.environ.get("CLOUD_DB_TEST_SOCKETLESS") == "1":
        from socketless_loop import SocketlessLoop

        options["loop_factory"] = SocketlessLoop
    with TestClient(app, backend_options=options) as client:
        yield client
    get_settings.cache_clear()


AUTH = {"Authorization": "Bearer test-operator-token"}


@pytest.mark.parametrize("count", [3, 6, 9])
def test_authenticated_creates_follow_distribution_and_sse(client, monkeypatch, count):
    import json

    from test_service import Collection

    from app import service
    from app.runtime import CLUSTERS, COLLECTIONS

    monkeypatch.setenv("CLOUD_DB_BOT_TOKENS", json.dumps({f"bot-{i}": f"token-{i}" for i in range(1, 10)}))
    get_settings.cache_clear()
    monkeypatch.setattr(
        live, "bots", {f"bot-{i}": {"state": "running", "confirmed_requests": 0} for i in range(1, count + 1)}
    )
    sources = {(cluster, name): Collection() for cluster in CLUSTERS for name in COLLECTIONS}
    monkeypatch.setattr(service, "collection", lambda cluster, name: sources[cluster, name])
    monkeypatch.setattr(service, "_replays", service.OrderedDict())
    for i in range(1, count + 1):
        expected = CLUSTERS[(i - 1) // 3]
        response = client.post(
            "/categories",
            json={"code": f"BOT-{i}", "name": f"Category {i}"},
            headers={"Authorization": f"Bearer token-{i}", "Idempotency-Key": str(uuid4())},
        )
        assert response.status_code == 201
        assert response.json()["owner_cluster"] == expected
        assert response.json()["id"] in sources[expected, "categories"].docs
        sent, success = list(live.events)[-2:]
        assert sent["cluster_id"] == success["cluster_id"] == expected
        assert success["bot_id"] == f"bot-{i}"
    snapshot = client.get("/bots", headers=AUTH).json()
    users = client.get("/usuarios", headers=AUTH).json()
    assert snapshot["cluster_loads"] == users["cluster_loads"]
    assert list(snapshot["cluster_loads"].values()) == [3, 3 if count >= 6 else 0, 3 if count == 9 else 0]


@pytest.mark.parametrize(
    "number,expected",
    [
        (1, "mongo_1"),
        (3, "mongo_1"),
        (4, "mongo_2"),
        (6, "mongo_2"),
        (7, "mongo_3"),
        (9, "mongo_3"),
    ],
)
def test_group_position_routing(number, expected):
    assert cluster_for_position(number) == expected


@pytest.mark.parametrize("invalid", [-1, 0, 10, True, 1.5, "3"])
def test_invalid_position(invalid):
    with pytest.raises(ValueError):
        cluster_for_position(invalid)


def test_auth_and_routes(client):
    assert client.get("/categories").status_code == 401
    assert client.get("/events").status_code == 401
    assert client.get("/events", headers={"Authorization": "Bearer test-bot-token"}).status_code == 403
    paths = client.get("/openapi.json").json()["paths"]
    assert all(
        p in paths
        for p in [
            "/estadisticas",
            "/sincronizacion",
            "/health",
            "/clusters",
            "/usuarios",
            "/metrics",
            "/events",
        ]
    )
    assert "/operations/recover" not in paths
    assert "/logs" not in paths


def test_validation_before_database_write(client):
    headers = {**AUTH, "Idempotency-Key": str(uuid4())}
    assert (
        client.post(
            "/products",
            json={"sku": "BAD", "name": "Bad", "category_id": str(uuid4()), "unit_price_minor": -1},
            headers=headers,
        ).status_code
        == 422
    )
    assert client.post("/categories", json={"code": "A", "name": "Name"}, headers=AUTH).status_code == 422
    assert live.events[-1]["status"] == "error"


def test_mutation_contract_and_events(client, monkeypatch):
    import app.api

    captured = {}

    def mutate(*args):
        captured["args"] = args
        return {"id": "saved", "version": 2, "owner_cluster": "mongo_3"}

    monkeypatch.setattr(app.api, "mutate", mutate)
    key, entity_id = str(uuid4()), str(uuid4())
    response = client.put(
        "/customers/" + entity_id,
        json={"name": "Name", "email": "a@example.org"},
        headers={**AUTH, "Idempotency-Key": key, "If-Match": "1"},
    )
    assert response.status_code == 200
    assert captured["args"][0:2] == ("customer", "updated")
    assert captured["args"][-2:] == (entity_id, 1)
    sent, success = list(live.events)[-2:]
    assert sent["status"] == "sent"
    assert success["status"] == "success" and success["cluster_id"] == "mongo_3"
    assert {
        "bot_id",
        "operation",
        "collection",
        "cluster_id",
        "status",
        "message",
        "timestamp",
    } <= success.keys()


def test_rejected_write_never_emits_success(client, monkeypatch):
    from fastapi import HTTPException

    def fail(*args):
        raise HTTPException(409, "Conflicto de versión")

    monkeypatch.setattr("app.api.mutate", fail)
    response = client.delete(
        "/categories/" + str(uuid4()), headers={**AUTH, "Idempotency-Key": str(uuid4()), "If-Match": "1"}
    )
    assert response.status_code == 409
    assert [e["status"] for e in list(live.events)[-2:]] == ["sent", "error"]
    assert "versión" in live.events[-1]["message"]


@pytest.mark.parametrize(
    "body",
    [
        {"count": -1},
        {"count": 10},
        {"count": True},
        {"count": 1.5},
        {"count": 2, "rounds": 21},
        {"count": 2, "interval_seconds": 0.1},
    ],
)
def test_bot_limits(client, body):
    assert client.put("/bots", json=body, headers=AUTH).status_code == 422


def test_controls_forward_options(client, monkeypatch):
    import app.simulation

    seen = []
    monkeypatch.setattr(
        app.simulation.manager, "resize", lambda body: seen.append(body.model_dump()) or {"ok": True}
    )
    assert (
        client.put("/bots", json={"count": 6, "rounds": 3, "interval_seconds": 0.5}, headers=AUTH).status_code
        == 200
    )
    assert seen[-1] == {"count": 6, "rounds": 3, "interval_seconds": 0.5}
    assert client.post("/bots/stop", headers=AUTH).status_code == 202
    assert seen[-1]["count"] == 0
    assert client.post("/bots/10/start", json={}, headers=AUTH).status_code == 422
    monkeypatch.setattr("app.monitoring.scheduler.trigger", lambda source: {"source": source})
    assert client.post("/sincronizacion", headers=AUTH).json() == {"source": "manual"}


def test_order_and_money_validation():
    item = {"product_id": str(uuid4()), "quantity": 1, "unit_price_minor": 100}
    with pytest.raises(ValidationError):
        Order(order_reference="A", customer_id=uuid4(), items=[item, item])
    for invalid in [True, 1.5, -1, 2**63]:
        with pytest.raises(ValidationError):
            Product(sku="A", name="Test", category_id=uuid4(), unit_price_minor=invalid)


def test_bounded_telemetry_cursor_and_restart():
    state = LiveState()
    cursor = state.snapshot()["cursor"]
    for _ in range(501):
        state.emit(operation="READ", status="success", message="ok")
    assert len(state.events) == 500 and len(state.snapshot()["recent"]) == 20
    assert state.since(cursor) is None
    recent = state.since(state.events[-2]["id"])
    assert len(recent) == 1
    recent[0]["message"] = "changed"
    assert state.events[-1]["message"] == "ok"
    assert LiveState().since(state.events[-1]["id"]) is None
