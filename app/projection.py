"""Versioned snapshots to normalized Neon tables; no Mongo outbox or receipts."""

import hashlib
from uuid import NAMESPACE_URL, uuid5

from bson.json_util import CANONICAL_JSON_OPTIONS, dumps
from psycopg import sql

from app.models import MODELS, TABLES, business_keys

BUSINESS_COLUMNS = {
    "category": ["code", "name", "normalized_name"],
    "customer": ["name", "email", "normalized_email"],
    "product": ["sku", "name", "description", "category_id", "unit_price_minor", "currency"],
    "order": ["order_reference", "customer_id", "status", "currency"],
}
COMMON = ["source_cluster", "source_version", "created_at", "updated_at", "deleted_at"]


def deliver(conn, run_id, kind, cluster, doc):
    if doc["owner_cluster"] != cluster or doc["version"] < 1:
        raise ValueError("Source cluster/version mismatch")
    MODELS[kind].model_validate({k: doc[k] for k in MODELS[kind].model_fields if k in doc})
    if any(doc.get(k) != v for k, v in business_keys(kind, doc).items()):
        raise ValueError("Invalid normalized key")
    fields = [
        "_id",
        "owner_cluster",
        "version",
        "created_at",
        "updated_at",
        "deleted_at",
        *BUSINESS_COLUMNS[kind],
    ]
    if kind == "order":
        fields.append("items")
    payload = {k: doc.get(k) for k in fields}
    digest = hashlib.sha256(
        dumps(payload, json_options=CANONICAL_JSON_OPTIONS, sort_keys=True).encode()
    ).digest()
    event_id = uuid5(NAMESPACE_URL, f"cloud-db:snapshot:{cluster}:{kind}:{doc['_id']}:{doc['version']}")
    saved = conn.execute(
        "SELECT event_digest FROM cloud_db.sync_receipts WHERE event_id=%s", (event_id,)
    ).fetchone()
    if saved:
        if bytes(saved[0]) != digest:
            raise ValueError("Document changed without a version increment")
        return "repeated"
    table, pk = TABLES[kind], kind + "_id"
    current = conn.execute(
        sql.SQL("SELECT source_cluster,source_version FROM cloud_db.{} WHERE {}=%s FOR UPDATE").format(
            sql.Identifier(table), sql.Identifier(pk)
        ),
        (doc["_id"],),
    ).fetchone()
    outcome = "applied"
    if current:
        if current[0] != cluster:
            raise ValueError("Entity identifier already belongs to another source")
        if current[1] >= doc["version"]:
            outcome = "equivalent" if current[1] == doc["version"] else "obsolete"
    if outcome == "applied":
        columns = [pk, *BUSINESS_COLUMNS[kind], *COMMON]
        row = {
            pk: doc["_id"],
            **{c: doc.get(c) for c in BUSINESS_COLUMNS[kind]},
            "source_cluster": cluster,
            "source_version": doc["version"],
            **{c: doc[c] for c in ("created_at", "updated_at", "deleted_at")},
        }
        conn.execute(
            sql.SQL("INSERT INTO cloud_db.{} ({}) VALUES ({}) ON CONFLICT ({}) DO UPDATE SET {}").format(
                sql.Identifier(table),
                sql.SQL(",").join(map(sql.Identifier, columns)),
                sql.SQL(",").join(sql.Placeholder() for _ in columns),
                sql.Identifier(pk),
                sql.SQL(",").join(
                    sql.SQL("{}=EXCLUDED.{}").format(sql.Identifier(c), sql.Identifier(c))
                    for c in columns
                    if c != pk
                ),
            ),
            [row[c] for c in columns],
        )
        if kind == "order":
            conn.execute("DELETE FROM cloud_db.order_items WHERE order_id=%s", (doc["_id"],))
            for item in doc["items"]:
                conn.execute(
                    "INSERT INTO cloud_db.order_items (order_id,product_id,quantity,unit_price_minor) VALUES (%s,%s,%s,%s)",
                    (doc["_id"], item["product_id"], item["quantity"], item["unit_price_minor"]),
                )
    conn.execute(
        """INSERT INTO cloud_db.sync_receipts
        (event_id,run_id,source_cluster,event_category,entity_type,entity_id,entity_version,event_digest,outcome)
        VALUES (%s,%s,%s,'business',%s,%s,%s,%s,%s)""",
        (event_id, run_id, cluster, kind, doc["_id"], doc["version"], digest, outcome),
    )
    return outcome
