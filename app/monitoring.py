"""Read-only monitoring computed from business collections and Neon."""

from datetime import UTC, datetime

import psycopg
from fastapi import APIRouter
from psycopg import sql
from psycopg.rows import dict_row
from pymongo.errors import PyMongoError

from app.api import Auth
from app.database import collection, mongo, postgres
from app.models import TABLES
from app.runtime import CLUSTERS, live
from app.security import require_admin
from app.worker import scheduler

router = APIRouter(tags=["Monitoreo"])


def mongo_snapshot():
    result = []
    for cluster in CLUSTERS:
        try:
            mongo(cluster).command("ping")
            versions = {
                kind: {
                    str(d["_id"]): d["version"]
                    for d in collection(cluster, name).find({}, {"_id": 1, "version": 1})
                }
                for kind, name in TABLES.items()
            }
            result.append(
                {
                    "id": cluster,
                    "status": "online",
                    "documents": sum(len(v) for v in versions.values()),
                    "versions": versions,
                }
            )
        except (PyMongoError, KeyError) as exc:
            result.append(
                {
                    "id": cluster,
                    "status": "error",
                    "documents": None,
                    "message": type(exc).__name__,
                    "versions": {},
                }
            )
    return result


def postgres_snapshot():
    with postgres(read_only=True, row_factory=dict_row) as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            versions = {}
            counts = {}
            for kind, name in TABLES.items():
                rows = conn.execute(
                    sql.SQL("SELECT {} AS id,source_cluster,source_version FROM cloud_db.{}").format(
                        sql.Identifier(kind + "_id"), sql.Identifier(name)
                    )
                ).fetchall()
                versions[kind] = {(r["source_cluster"], str(r["id"])): r["source_version"] for r in rows}
                counts[name] = len(rows)
            counts["order_items"] = conn.execute("SELECT count(*) AS n FROM cloud_db.order_items").fetchone()[
                "n"
            ]
            errors = conn.execute(
                "SELECT count(*) AS n FROM cloud_db.system_logs WHERE component='worker' AND level IN ('error','critical')"
            ).fetchone()["n"]
            last = conn.execute(
                "SELECT max(finished_at) AS value FROM cloud_db.sync_runs WHERE status='succeeded'"
            ).fetchone()["value"]
    return {
        "total_records": sum(counts.values()),
        "synchronized_tables": sum(n > 0 for n in counts.values()),
        "tables": counts,
        "load_errors": errors,
        "last_sync": last,
        "versions": versions,
    }


@router.get("/clusters")
def clusters(actor: Auth):
    return [{k: v for k, v in c.items() if k != "versions"} for c in mongo_snapshot()]


@router.get("/usuarios")
def users(actor: Auth):
    snapshot = live.snapshot()
    return {
        "active_count": snapshot["active_count"],
        "assignments": snapshot["assignments"],
        "cluster_loads": snapshot["cluster_loads"],
        "bots": [
            {"id": f"bot-{i}", **snapshot["bots"].get(f"bot-{i}", {"state": "stopped"})} for i in range(1, 10)
        ],
    }


@router.get("/estadisticas")
@router.get("/metrics")
def metrics(actor: Auth):
    sources = mongo_snapshot()
    errors = []
    try:
        pg = postgres_snapshot()
    except psycopg.Error as exc:
        pg = None
        errors.append(f"Neon: {type(exc).__name__}")
    known = all(c["status"] == "online" for c in sources)
    total = sum(c["documents"] for c in sources) if known else None
    synced = 0
    for source in sources:
        if source["status"] != "online":
            errors.append(f"{source['id']}: {source['message']}")
        for kind, docs in source["versions"].items():
            if pg:
                synced += sum(
                    pg["versions"][kind].get((source["id"], entity_id)) == version
                    for entity_id, version in docs.items()
                )
    if not known or pg is None:
        synced = None
    state = live.snapshot()
    return {
        "observed_at": datetime.now(UTC),
        "mongo": {
            "total_documents": total,
            "synchronized_documents": synced,
            "pending_documents": total - synced if total is not None and synced is not None else None,
            "active_clusters": sum(c["status"] == "online" for c in sources),
            "connected_users": state["active_count"],
        },
        "postgres": {k: v for k, v in pg.items() if k != "versions"} if pg else None,
        "clusters": [{k: v for k, v in c.items() if k != "versions"} for c in sources],
        "errors": errors,
        "etl": state["etl"],
    }


@router.get("/sincronizacion")
def synchronization(actor: Auth):
    with postgres(read_only=True, row_factory=dict_row) as conn:
        runs = conn.execute("SELECT * FROM cloud_db.sync_runs ORDER BY started_at DESC LIMIT 5").fetchall()
    return {**live.snapshot()["etl"], "runs": runs, "interval_seconds": 180}


@router.post("/sincronizacion", status_code=202)
def synchronize(actor: Auth):
    require_admin(actor)
    return scheduler.trigger("manual")


@router.get("/health")
def health(actor: Auth):
    status = {}
    for cluster in CLUSTERS:
        try:
            mongo(cluster).command("ping")
            status[cluster] = "online"
        except PyMongoError:
            status[cluster] = "error"
    try:
        with postgres(read_only=True) as conn:
            conn.execute("SELECT 1 FROM cloud_db.sync_runs LIMIT 0")
        status["neon"] = "online"
    except psycopg.Error:
        status["neon"] = "error"
    return {
        "status": "ready" if all(s == "online" for s in status.values()) else "degraded",
        "services": status,
    }
