"""Atlas migration 001. Run as python -m app.mongo_collections [--apply]."""

import argparse
from contextlib import ExitStack
from urllib.parse import urlsplit

from pymongo import MongoClient
from pymongo.errors import CollectionInvalid, OperationFailure, PyMongoError

from app.core.config import get_settings

UUID = {"bsonType": "string", "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"}
TEXT = {"bsonType": "string", "pattern": r"\S"}
CODE = {"bsonType": "string", "pattern": r"^[A-Z0-9][A-Z0-9._-]*$"}
PRICE = {"bsonType": ["int", "long"], "minimum": 0}
CURRENCY = {"enum": ["MXN"]}
ITEM = {
    "bsonType": "object",
    "required": ["product_id", "quantity", "unit_price_minor"],
    "properties": {
        "product_id": UUID,
        "quantity": {"bsonType": ["int", "long"], "minimum": 1, "maximum": 2147483647},
        "unit_price_minor": PRICE,
    },
    "additionalProperties": False,
}
FIELDS = {
    "categories": {"code": CODE, "name": TEXT, "normalized_name": TEXT},
    "products": {
        "sku": CODE,
        "name": TEXT,
        "category_id": UUID,
        "unit_price_minor": PRICE,
        "currency": CURRENCY,
    },
    "customers": {"name": TEXT, "email": TEXT, "normalized_email": TEXT},
    "orders": {
        "order_reference": TEXT,
        "customer_id": UUID,
        "status": {"enum": ["draft", "confirmed", "completed", "cancelled"]},
        "currency": CURRENCY,
        "items": {"bsonType": "array", "items": ITEM},
    },
}
# field, enforce uniqueness inside this collection
INDEXES = {
    "categories": [("code", True), ("normalized_name", True)],
    "products": [("sku", True), ("category_id", False)],
    "customers": [("normalized_email", True)],
    "orders": [("order_reference", True), ("customer_id", False)],
}


def validator(name, owner):
    fields = {
        "_id": UUID,
        "owner_cluster": {"enum": [owner]},
        "version": {"bsonType": ["int", "long"], "minimum": 1},
        "created_at": {"bsonType": "date"},
        "updated_at": {"bsonType": "date"},
        "deleted_at": {"bsonType": ["null", "date"]},
        **FIELDS[name],
    }
    required = list(fields)
    if name == "products":
        fields["description"] = {"bsonType": ["null", "string"]}
    schema = {"$jsonSchema": {"bsonType": "object", "required": required, "properties": fields}}
    if name != "orders":
        return schema
    # Guard array expressions if an invalid document has missing or non-array items.
    rules = {
        "$cond": [
            {"$isArray": "$items"},
            {
                "$and": [
                    {"$eq": [{"$size": "$items"}, {"$size": {"$setUnion": ["$items.product_id", []]}}]},
                    {"$or": [{"$eq": ["$status", "draft"]}, {"$gt": [{"$size": "$items"}, 0]}]},
                ]
            },
            False,
        ]
    }
    return {"$and": [schema, {"$expr": rules}]}


def inspect_collection(db, owner, name):
    expected = validator(name, owner)
    info = next(db.list_collections(filter={"name": name}), None)
    if info is not None:
        options = info.get("options", {})
        if (
            info["type"] != "collection"
            or options.get("validator") != expected
            or options.get("validationLevel", "strict") != "strict"
            or options.get("validationAction", "error") != "error"
            or "collation" in options
        ):
            raise ValueError(f"{owner}.{name}: existing collection has different options")
    indexes = list(db[name].list_indexes()) if info else []
    missing = []
    for field, unique in INDEXES[name]:
        index_name = ("uq_" if unique else "ix_") + field
        keys = [(field, 1)]
        found = False
        for index in indexes:
            if index["name"] == index_name or list(index["key"].items()) == keys:
                if (
                    list(index["key"].items()) != keys
                    or bool(index.get("unique")) != unique
                    or index.get("sparse")
                    or "partialFilterExpression" in index
                    or "collation" in index
                    or "expireAfterSeconds" in index
                ):
                    raise ValueError(f"{owner}.{name}: incompatible index for {field}")
                found = True
        if not found:
            missing.append((field, unique, index_name))
    return info is None, missing


def run(settings, apply):
    targets = [(f"mongo_{i}", getattr(settings, f"mongo_{i}_uri").get_secret_value()) for i in (1, 2, 3)]
    hosts = [urlsplit(uri).hostname for _, uri in targets]
    if None in hosts or len(set(hosts)) != 3:
        raise ValueError("Expected three different Atlas hosts in the configuration")
    if settings.mongo_database in {"admin", "config", "local"}:
        raise ValueError("Choose an application database")
    with ExitStack() as stack:
        work = []
        for owner, uri in targets:
            print(f"Inspecting {owner} / {settings.mongo_database}", flush=True)
            client = stack.enter_context(
                MongoClient(
                    uri, appname="cloud-db-migration", serverSelectionTimeoutMS=12000, socketTimeoutMS=30000
                )
            )
            db = client[settings.mongo_database]
            aliases = [n for n in db.list_collection_names() if n.lower() in FIELDS and n not in FIELDS]
            if aliases:
                raise ValueError(f"{owner}: review collection name casing before setup: {aliases}")
            for name in FIELDS:
                create, indexes = inspect_collection(db, owner, name)
                work.append((db, owner, name, create, indexes))
                if not apply:
                    print(f"  {name}: create={create}, missing_indexes={len(indexes)}")
        if not apply:
            return
        for db, owner, name, create, indexes in work:
            print(f"Applying {owner}.{name}", flush=True)
            if create:
                db.create_collection(
                    name, validator=validator(name, owner), validationLevel="strict", validationAction="error"
                )
            for field, unique, index_name in indexes:
                db[name].create_index([(field, 1)], unique=unique, name=index_name)
            if inspect_collection(db, owner, name) != (False, []):
                raise ValueError(f"{owner}.{name}: final verification failed")
        print("Verified: 12 business collections across 3 clusters.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="create missing collections and indexes")
    args = parser.parse_args()
    try:
        run(get_settings(), args.apply)
    except OperationFailure as exc:
        raise SystemExit(f"Atlas operation failed: {type(exc).__name__}, code={exc.code}") from None
    except (PyMongoError, CollectionInvalid) as exc:
        raise SystemExit(f"Atlas connection or operation failed: {type(exc).__name__}") from None
