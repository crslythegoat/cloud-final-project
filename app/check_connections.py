import psycopg
from pymongo import MongoClient

from app.core.config import get_settings


def check_mongo(name: str, uri: str) -> bool:
    try:
        with MongoClient(
            uri,
            appname="cloud-db-connection-check",
            serverSelectionTimeoutMS=10000,
            connectTimeoutMS=10000,
            socketTimeoutMS=10000,
        ) as client:
            client.admin.command("ping")

        print(f"{name}: OK")
        return True
    except Exception as exc:
        print(f"{name}: FAILED ({type(exc).__name__})")
        return False


def check_postgres(uri: str) -> bool:
    try:
        with psycopg.connect(
            uri,
            connect_timeout=15,
            autocommit=True,
            application_name="cloud-db-connection-check",
            options="-c statement_timeout=10000",
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                if cursor.fetchone() != (1,):
                    raise RuntimeError("Unexpected diagnostic result")

        print("neon: OK")
        return True
    except Exception as exc:
        print(f"neon: FAILED ({type(exc).__name__})")
        return False


def main() -> int:
    settings = get_settings()

    mongo_connections = (
        ("mongo_1", settings.mongo_1_uri),
        ("mongo_2", settings.mongo_2_uri),
        ("mongo_3", settings.mongo_3_uri),
    )

    results = [check_mongo(name, uri.get_secret_value()) for name, uri in mongo_connections]

    results.append(check_postgres(settings.postgres_direct_uri.get_secret_value()))

    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
