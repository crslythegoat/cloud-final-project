"""In-memory Mongo doubles check business invariants and uncertain-write retries."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pymongo.errors import AutoReconnect

from app import service
from app.runtime import CLUSTERS, COLLECTIONS, live
from app.security import Actor


class Collection:
    def __init__(self):
        self.docs = {}
        self.uncertain = False

    def find_one(self, query):
        def match(doc):
            for key, value in query.items():
                actual = doc.get(key)
                if isinstance(value, dict) and "$ne" in value:
                    if actual == value["$ne"]:
                        return False
                elif actual != value:
                    return False
            return True

        return next((deepcopy(d) for d in self.docs.values() if match(d)), None)

    def insert_one(self, doc):
        self.docs[doc["_id"]] = deepcopy(doc)
        if self.uncertain:
            self.uncertain = False
            raise AutoReconnect("response lost after commit")

    def replace_one(self, query, doc):
        if self.find_one(query):
            self.docs[doc["_id"]] = deepcopy(doc)
            return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)


@pytest.fixture
def sources(monkeypatch):
    data = {(cluster, name): Collection() for cluster in CLUSTERS for name in COLLECTIONS}

    def get(cluster, name):
        assert name in COLLECTIONS
        return data[cluster, name]

    monkeypatch.setattr(service, "collection", get)
    service._replays.clear()
    monkeypatch.setattr(live, "bots", {})
    return data


def create_category(key=None, actor=None):
    code = uuid4().hex.upper()
    return service.mutate(
        "category",
        "created",
        {"code": code, "name": code},
        actor or Actor("operator", "mongo_1", True),
        key or uuid4(),
    )


def test_load_changes_new_target_existing_owner_is_preserved(sources):
    live.bots = {f"bot-{i}": {"state": "running"} for i in range(1, 7)}
    first = create_category(actor=Actor("bot-6", live.destination("bot-6")))
    assert first["owner_cluster"] == "mongo_2"
    # Removing lower IDs repacks active bots, but does not migrate old documents.
    live.bots["bot-1"]["state"] = "stopped"
    live.bots["bot-2"]["state"] = "completed"
    live.bots["bot-3"]["state"] = "error"
    actor = Actor("bot-6", live.destination("bot-6"))
    second = create_category(actor=actor)
    assert second["owner_cluster"] == "mongo_1"
    assert service.read("category", first["id"])["owner_cluster"] == "mongo_2"
    updated = service.mutate(
        "category",
        "updated",
        {"code": first["code"], "name": "Changed"},
        actor,
        uuid4(),
        first["id"],
        1,
    )
    assert updated["owner_cluster"] == "mongo_2"
    deleted = service.mutate("category", "deleted", {}, actor, uuid4(), first["id"], 2)
    assert deleted["owner_cluster"] == "mongo_2" and deleted["deleted_at"]
    assert first["id"] not in sources["mongo_1", "categories"].docs


def test_ambiguous_create_retry_after_memory_reset_does_not_duplicate(sources):
    body = {"code": "RETRY", "name": "Retry"}
    actor, key = Actor("bot-1", "mongo_1"), uuid4()
    sources["mongo_1", "categories"].uncertain = True
    with pytest.raises(AutoReconnect):
        service.mutate("category", "created", body, actor, key)
    service._replays.clear()
    actor = Actor("bot-1", "mongo_3")
    result = service.mutate("category", "created", body, actor, key)
    assert result["version"] == 1 and result["owner_cluster"] == "mongo_1"
    assert sum(len(c.docs) for c in sources.values()) == 1
    with pytest.raises(HTTPException):
        service.mutate("category", "created", {"code": "OTHER", "name": "Other"}, actor, key)


def test_unique_keys_across_clusters(sources):
    first = create_category()
    with pytest.raises(HTTPException) as exc:
        service.mutate(
            "category",
            "created",
            {"code": first["code"], "name": "Other"},
            Actor("bot-4", "mongo_2"),
            uuid4(),
        )
    assert exc.value.status_code == 409


def test_delete_version_and_replay(sources):
    first = create_category()
    actor, key = Actor("operator", "mongo_1", True), uuid4()
    with pytest.raises(HTTPException):
        service.mutate("category", "deleted", {}, actor, key, first["id"], 99)
    deleted = service.mutate("category", "deleted", {}, actor, key, first["id"], 1)
    service._replays.clear()
    assert service.mutate("category", "deleted", {}, actor, key, first["id"], 1) == deleted
    assert deleted["deleted_at"] and deleted["version"] == 2
    assert "_request" not in deleted
    with pytest.raises(HTTPException):
        service.read("category", first["id"])


def test_missing_reference_does_not_write(sources):
    with pytest.raises(HTTPException):
        service.mutate(
            "product",
            "created",
            {
                "sku": "P",
                "name": "P",
                "category_id": str(uuid4()),
                "unit_price_minor": 100,
                "currency": "MXN",
            },
            Actor("operator", "mongo_1", True),
            uuid4(),
        )
    assert not any(c.docs for c in sources.values())


def test_collection_guard_rejects_before_connecting(monkeypatch):
    import app.database

    monkeypatch.setattr(app.database, "mongo", lambda _: pytest.fail("must reject before opening database"))
    with pytest.raises(ValueError):
        app.database.collection("mongo_1", "unplanned")
