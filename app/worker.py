"""Snapshot ETL. Scheduler lives in FastAPI; this module provides the single cycle."""

from collections import Counter
from datetime import UTC, datetime
from threading import Event, Lock, Thread
from time import monotonic
from uuid import uuid4

import psycopg
from fastapi import HTTPException
from pydantic import ValidationError
from pymongo.errors import PyMongoError

from app.database import collection, postgres
from app.models import TABLES
from app.projection import deliver
from app.runtime import CLUSTERS, live, write_lock

LOCK = "cloud_db.snapshot_worker.v1"
ORDER = ("category", "customer", "product", "order")


def technical_log(conn, run_id, kind, cluster, error):
    conn.execute(
        """INSERT INTO cloud_db.system_logs
        (log_id,occurred_at,event_category,level,component,event_type,outcome,run_id,source_cluster,message,error_code)
        VALUES (%s,clock_timestamp(),'technical','error','worker','snapshot.delivery','failed',%s,%s,%s,%s)""",
        (uuid4(), run_id, cluster, f"No se pudo sincronizar {TABLES[kind]}", type(error).__name__),
    )


def cycle(trigger="scheduled"):
    with postgres() as conn:
        if not conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s,0))", (LOCK,)).fetchone()[0]:
            return {"status": "skipped_overlap"}
        run_id = uuid4()
        counts = Counter()
        try:
            conn.execute(
                "UPDATE cloud_db.sync_runs SET status='interrupted',finished_at=clock_timestamp() WHERE status='running'"
            )
            conn.execute(
                "INSERT INTO cloud_db.sync_runs (run_id,trigger_type,status) VALUES (%s,%s,'running')",
                (run_id, trigger),
            )
            # A consistent application snapshot prevents a parent created after its scan from being missed.
            snapshot = []
            with write_lock:
                for kind in ORDER:
                    for cluster in CLUSTERS:
                        try:
                            snapshot.extend(
                                (kind, cluster, doc) for doc in collection(cluster, TABLES[kind]).find({})
                            )
                        except PyMongoError as exc:
                            counts["deferred"] += 1
                            technical_log(conn, run_id, kind, cluster, exc)
                            live.emit(
                                operation="ETL",
                                collection=TABLES[kind],
                                cluster_id=cluster,
                                status="error",
                                stage="etl",
                                message=f"No se pudo leer el origen ({type(exc).__name__})",
                            )
            for kind, cluster, doc in snapshot:
                live.emit(
                    operation="ETL",
                    collection=TABLES[kind],
                    cluster_id=cluster,
                    status="sent",
                    stage="etl",
                    message="Comprobando versión en Neon",
                    entity_id=doc["_id"],
                    run_id=str(run_id),
                )
                try:
                    with conn.transaction():
                        outcome = deliver(conn, run_id, kind, cluster, doc)
                    counts[outcome] += 1
                    live.emit(
                        operation="ETL",
                        collection=TABLES[kind],
                        cluster_id=cluster,
                        status="success",
                        stage="neon",
                        message="Documento cargado en Neon"
                        if outcome == "applied"
                        else "Versión ya procesada; sin duplicar",
                        outcome=outcome,
                        entity_id=doc["_id"],
                        run_id=str(run_id),
                    )
                except (psycopg.Error, ValueError, KeyError, ValidationError) as exc:
                    counts["deferred"] += 1
                    if conn.closed:
                        raise
                    technical_log(conn, run_id, kind, cluster, exc)
                    live.emit(
                        operation="ETL",
                        collection=TABLES[kind],
                        cluster_id=cluster,
                        status="error",
                        stage="etl",
                        message=f"Carga rechazada ({type(exc).__name__}); se reintentará en el próximo ciclo",
                        run_id=str(run_id),
                    )
            status = "partial" if counts["deferred"] else "succeeded"
            conn.execute(
                """UPDATE cloud_db.sync_runs SET status=%s,finished_at=clock_timestamp(),events_applied=%s,
                events_obsolete=%s,events_repeated=%s,events_deferred=%s WHERE run_id=%s""",
                (
                    status,
                    counts["applied"],
                    counts["obsolete"],
                    counts["repeated"] + counts["equivalent"],
                    counts["deferred"],
                    run_id,
                ),
            )
            return {"status": status, "run_id": str(run_id), "counts": dict(counts)}
        except Exception:
            if not conn.closed:
                conn.execute(
                    "UPDATE cloud_db.sync_runs SET status='failed',finished_at=clock_timestamp() WHERE run_id=%s",
                    (run_id,),
                )
            raise
        finally:
            if not conn.closed:
                conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (LOCK,))


class Scheduler:
    def __init__(self):
        self.lock = Lock()
        self.stop = Event()
        self.clock = None
        self.job = None
        self.next_run = 0

    def start(self):
        self.stop.clear()
        self.next_run = monotonic() + 180
        self.clock = Thread(target=self._watch, daemon=True, name="etl-scheduler")
        self.clock.start()

    def _watch(self):
        while not self.stop.wait(max(0.2, self.next_run - monotonic())):
            self.next_run = monotonic() + 180
            try:
                self.trigger("scheduled")
            except HTTPException:
                pass  # A manual cycle is already running.

    def trigger(self, source="manual"):
        with self.lock:
            if self.stop.is_set():
                raise HTTPException(503, "ETL detenido")
            if self.job and self.job.is_alive():
                raise HTTPException(409, "Ya hay una sincronización en ejecución")
            with live.lock:
                live.etl.update(status="running", message="Sincronización en ejecución")
            live.emit(
                operation="ETL", status="sent", stage="etl", message="Sincronización iniciada", lifecycle=True
            )
            self.job = Thread(target=self._run, args=(source,), daemon=True, name="etl-cycle")
            self.job.start()
        return {"status": "running"}

    def _run(self, source):
        try:
            result = cycle(source)
            status = "succeeded" if result["status"] == "succeeded" else "error"
            message = (
                "Sincronización completada"
                if status == "succeeded"
                else "Sincronización incompleta o bloqueada por otra ejecución"
            )
        except Exception as exc:
            status, message = "error", f"ETL no disponible ({type(exc).__name__})"
            result = {}
        with live.lock:
            live.etl.update(status=status, message=message, result=result)
            if status == "succeeded":
                live.etl["last_success"] = datetime.now(UTC).isoformat()
        live.emit(
            operation="ETL",
            status="success" if status == "succeeded" else "error",
            stage="etl",
            message=message,
            lifecycle=True,
            result=result,
        )

    def shutdown(self):
        self.stop.set()
        if self.clock:
            self.clock.join(timeout=2)
        if self.job:
            self.job.join()  # Complete any committed batch before closing database clients.


scheduler = Scheduler()
