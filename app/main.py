from contextlib import asynccontextmanager
from pathlib import Path

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pymongo.errors import PyMongoError
from starlette.concurrency import run_in_threadpool

from app.api import router as business_router
from app.core.config import get_settings
from app.database import close_clients
from app.events import router as events_router
from app.monitoring import router as monitoring_router
from app.runtime import COLLECTIONS, live
from app.simulation import manager
from app.simulation import router as bot_router
from app.worker import scheduler


@asynccontextmanager
async def lifespan(app):
    settings = get_settings()
    if settings.environment == "production" and len(settings.api_token.get_secret_value()) < 32:
        raise RuntimeError("Production requires an operator token of at least 32 characters")
    manager.closing = False
    if settings.environment != "test":
        scheduler.start()
    yield
    await run_in_threadpool(manager.shutdown)
    await run_in_threadpool(scheduler.shutdown)
    close_clients()


app = FastAPI(title="Cloud DB", version="1.0.0", lifespan=lifespan)
for router in (business_router, monitoring_router, bot_router, events_router):
    app.include_router(router)


@app.middleware("http")
async def validation_events(request: Request, call_next):
    response = await call_next(request)
    resource = request.url.path.strip("/").split("/")[0]
    actor = getattr(request.state, "actor", None)
    if (
        actor
        and response.status_code >= 400
        and resource in COLLECTIONS
        and not getattr(request.state, "telemetry_handled", False)
    ):
        live.emit(
            bot_id=actor.id if not actor.admin else None,
            operation={"POST": "INSERT", "PUT": "UPDATE", "DELETE": "DELETE", "GET": "READ"}.get(
                request.method, "READ"
            ),
            collection=resource,
            cluster_id=actor.cluster,
            status="error",
            stage="api",
            message=f"Solicitud rechazada por la API (HTTP {response.status_code}); revisa datos y encabezados",
        )
    return response


@app.exception_handler(PyMongoError)
@app.exception_handler(psycopg.Error)
async def database_failure(request: Request, exc):
    return JSONResponse(
        status_code=503,
        content={
            "detail": "Base de datos no disponible; conserva la misma clave al reintentar",
            "error_code": type(exc).__name__,
        },
    )


DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
app.mount("/assets", StaticFiles(directory=DIST / "assets", check_dir=False), name="assets")


@app.get("/", include_in_schema=False)
def index():
    if (DIST / "index.html").is_file():
        return FileResponse(DIST / "index.html")
    return {
        "service": "Cloud DB",
        "frontend": "Pendiente de compilar React con npm run build en frontend/",
        "docs": "/docs",
    }
