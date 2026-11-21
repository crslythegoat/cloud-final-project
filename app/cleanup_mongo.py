"""Review-only by default. Stop old API/worker processes before applying cleanup."""

import argparse
from urllib.parse import urlsplit

from app.core.config import get_settings
from app.database import close_clients, mongo
from app.runtime import CLUSTERS, COLLECTIONS

LEGACY = {
    "sync_outbox",
    "audit_events",
    "applied_commands",
    "entity_directory",
    "operation_requests",
    "mutation_locks",
    "client_sessions",
}


def inspect(databases):
    plans = []
    for cluster, db in databases:
        names = set(db.list_collection_names())
        aliases = [n for n in names if n.lower() in COLLECTIONS and n not in COLLECTIONS]
        if aliases:
            raise ValueError(f"{cluster}: revisar mayúsculas antes de continuar: {aliases}")
        unknown = names - set(COLLECTIONS) - LEGACY
        if unknown:
            print(f"{cluster}: colecciones desconocidas, no se eliminarán: {sorted(unknown)}")
        for name in sorted(names & LEGACY):
            count = db[name].count_documents({})
            print(f"{cluster}.{name}: {count} documentos; candidato de limpieza")
            plans.append((cluster, db, name))
    return plans


def run(apply=False, confirm_database=None, selected=()):
    settings = get_settings()
    if settings.mongo_database in {"admin", "local", "config"}:
        raise ValueError("No se permite limpiar bases de sistema")
    hosts = [urlsplit(getattr(settings, f"{c}_uri").get_secret_value()).hostname for c in CLUSTERS]
    if None in hosts or len(set(hosts)) != 3:
        raise ValueError("Se requieren tres hosts distintos")
    if set(selected) - LEGACY:
        raise ValueError("Solo se pueden seleccionar las siete colecciones antiguas conocidas")
    # Inspect all targets before any destructive action.
    plans = inspect([(cluster, mongo(cluster)) for cluster in CLUSTERS])
    if not apply:
        print("Solo inspección. No se ha eliminado nada.")
        return
    if confirm_database != settings.mongo_database or not selected:
        raise ValueError("Indica --confirm-database y --collections con los nombres revisados")
    print("Se eliminará la telemetría antigua seleccionada; las cuatro colecciones de negocio se conservan.")
    for cluster, db, name in plans:
        if name in selected:
            db.drop_collection(name)
            print(f"Eliminada: {cluster}.{name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-database")
    parser.add_argument("--collections", nargs="+", default=[])
    args = parser.parse_args()
    try:
        run(args.apply, args.confirm_database, args.collections)
    finally:
        close_clients()
