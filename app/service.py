"""CRUD over four collections. All writers must use one API process."""

import hashlib
import json
from collections import OrderedDict
from copy import deepcopy
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from app.database import collection
from app.models import TABLES, business_keys
from app.runtime import CLUSTERS, write_lock

_replays = OrderedDict()


def public(doc):
    return {"id": doc["_id"], **{k: v for k, v in doc.items() if not k.startswith("_")}}


def locate(kind, entity_id, *, active=True):
    found = []
    for cluster in CLUSTERS:
        doc = collection(cluster, TABLES[kind]).find_one({"_id": entity_id})
        if doc:
            found.append((cluster, doc))
    if len(found) > 1:
        raise HTTPException(409, "Identificador duplicado en varios clústeres; requiere revisión")
    if not found or (active and found[0][1].get("deleted_at")):
        raise HTTPException(404, "Documento activo no encontrado")
    return found[0]


def validate_references(kind, data):
    if kind == "product":
        locate("category", data["category_id"])
    if kind == "order":
        locate("customer", data["customer_id"])
        for item in data["items"]:
            locate("product", item["product_id"])


def check_keys(kind, data, entity_id):
    for cluster in CLUSTERS:
        for field, value in business_keys(kind, data).items():
            if collection(cluster, TABLES[kind]).find_one({field: value, "_id": {"$ne": entity_id}}):
                raise HTTPException(409, f"El valor de {field} ya existe")


def check_dependents(kind, entity_id):
    references = {
        "category": [("products", "category_id")],
        "customer": [("orders", "customer_id")],
        "product": [("orders", "items.product_id")],
        "order": [],
    }
    for cluster in CLUSTERS:
        for name, field in references[kind]:
            if collection(cluster, name).find_one({field: entity_id, "deleted_at": None}):
                raise HTTPException(409, "Primero elimina los documentos dependientes")


def remember(key, digest, result):
    _replays[key] = (digest, deepcopy(result))
    _replays.move_to_end(key)
    while len(_replays) > 1000:
        _replays.popitem(last=False)
    return result


def mutate(kind, action, data, actor, key, entity_id=None, expected_version=None):
    request_key = f"{actor.id}:{key}"
    digest = hashlib.sha256(
        json.dumps([kind, action, data, entity_id, expected_version], sort_keys=True).encode()
    ).hexdigest()
    with write_lock:
        if request_key in _replays:
            saved_hash, saved_result = _replays[request_key]
            if digest != saved_hash:
                raise HTTPException(409, "La clave de idempotencia corresponde a otra solicitud")
            return deepcopy(saved_result)
        doc_id = entity_id or str(uuid5(NAMESPACE_URL, f"cloud-db:{request_key}"))
        try:
            owner, old = locate(kind, doc_id, active=False)
        except HTTPException as exc:
            if action != "created" or exc.status_code != 404:
                raise
            owner, old = actor.cluster, None
        if old and old.get("_request", {}).get("key") == request_key:
            if old["_request"]["digest"] != digest:
                raise HTTPException(409, "La clave de idempotencia corresponde a otra solicitud")
            return remember(request_key, digest, public(old))
        if action == "created" and old:
            raise HTTPException(409, "La creación ya existe y fue modificada; consulta su estado")
        if old and (old.get("deleted_at") or old["version"] != expected_version):
            raise HTTPException(409, "Conflicto de versión; vuelve a consultar el documento")
        now = datetime.now(UTC)
        doc = (
            deepcopy(old)
            if old
            else {"_id": doc_id, "owner_cluster": owner, "created_at": now, "deleted_at": None, "version": 0}
        )
        if action == "deleted":
            check_dependents(kind, doc_id)
            doc["deleted_at"] = now
        else:
            if kind == "order" and old:
                transitions = {
                    "draft": {"draft", "confirmed", "cancelled"},
                    "confirmed": {"confirmed", "completed", "cancelled"},
                    "completed": {"completed"},
                    "cancelled": {"cancelled"},
                }
                if data["status"] not in transitions[old["status"]]:
                    raise HTTPException(409, "Transición de pedido inválida")
                if old["status"] != "draft" and any(
                    data[k] != old[k] for k in ("customer_id", "items", "currency", "order_reference")
                ):
                    raise HTTPException(409, "Un pedido confirmado conserva su contenido")
            validate_references(kind, data)
            check_keys(kind, data, doc_id)
            doc.update(data)
            doc.update(business_keys(kind, data))
        doc.update(
            version=doc["version"] + 1, updated_at=now, _request={"key": request_key, "digest": digest}
        )
        target = collection(owner, TABLES[kind])
        try:
            if old:
                result = target.replace_one({"_id": doc_id, "version": old["version"]}, doc)
                if result.matched_count != 1:
                    raise HTTPException(409, "El documento cambió durante la solicitud")
            else:
                target.insert_one(doc)
        except DuplicateKeyError:
            raise HTTPException(409, "Identificador o clave de negocio duplicada") from None
        return remember(request_key, digest, public(doc))


def read(kind, entity_id):
    return public(locate(kind, entity_id)[1])


def listing(kind, limit, offset):
    rows = []
    for cluster in CLUSTERS:
        rows.extend(
            collection(cluster, TABLES[kind]).find({"deleted_at": None}).sort("_id", 1).limit(offset + limit)
        )
    rows.sort(key=lambda d: d["_id"])
    return [public(d) for d in rows[offset : offset + limit]]
