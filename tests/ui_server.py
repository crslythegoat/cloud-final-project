"""Isolated UI acceptance server: in-memory Mongo, disposable local PostgreSQL.
Run only from the documented test command, never as the production entry point.
"""

import os
from copy import deepcopy
from types import SimpleNamespace

assert os.environ.get("CLOUD_DB_ENVIRONMENT") == "test"
assert os.environ.get("CLOUD_DB_POSTGRES_DIRECT_URI") == "postgresql://postgres@127.0.0.1:55439/cloud_db_ui"

from app import database  # noqa: E402
from app.runtime import CLUSTERS, COLLECTIONS, write_lock  # noqa: E402


class Cursor(list):
    def sort(self, key, direction=1):
        return Cursor(sorted(self, key=lambda d: d[key], reverse=direction < 0))

    def limit(self, n):
        return Cursor(self[:n])


def matches(doc, query):
    for key, value in query.items():
        if key == "items.product_id":
            if not any(i["product_id"] == value for i in doc.get("items", [])):
                return False
        elif isinstance(value, dict) and "$ne" in value:
            if doc.get(key) == value["$ne"]:
                return False
        elif doc.get(key) != value:
            return False
    return True


class Collection:
    def __init__(self):
        self.docs = {}

    def find(self, query, projection=None):
        with write_lock:
            return Cursor(deepcopy([d for d in self.docs.values() if matches(d, query)]))

    def find_one(self, query):
        return next(iter(self.find(query)), None)

    def insert_one(self, doc):
        self.docs[doc["_id"]] = deepcopy(doc)

    def replace_one(self, query, doc):
        if self.find_one(query):
            self.docs[doc["_id"]] = deepcopy(doc)
            return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)


class DB:
    def __init__(self):
        self.collections = {name: Collection() for name in COLLECTIONS}

    def __getitem__(self, name):
        assert name in COLLECTIONS
        return self.collections[name]

    def command(self, name):
        assert name == "ping"
        return {"ok": 1}


sources = {cluster: DB() for cluster in CLUSTERS}
database.mongo = lambda cluster="mongo_1": sources[cluster]
from app.main import app  # noqa: E402,F401
