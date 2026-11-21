"""The four REST resources and real operation events."""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pymongo.errors import PyMongoError

from app.models import MODELS, TABLES
from app.runtime import live
from app.security import Actor, authenticate
from app.service import listing, mutate, read

router = APIRouter()
Auth = Annotated[Actor, Depends(authenticate)]
Key = Annotated[UUID, Header(alias="Idempotency-Key")]
Version = Annotated[int, Header(alias="If-Match", ge=1)]


def tracked(request, actor, kind, operation, fn):
    request.state.telemetry_handled = True
    request_id = str(uuid4())
    base = {
        "bot_id": actor.id if not actor.admin else None,
        "operation": operation,
        "collection": TABLES[kind],
        "cluster_id": actor.cluster,
        "request_id": request_id,
    }
    live.emit(**base, status="sent", message="Solicitud recibida por la API", stage="api")
    try:
        result = fn()
    except HTTPException as exc:
        live.emit(**base, status="error", message=str(exc.detail), stage="api", http_status=exc.status_code)
        raise
    except PyMongoError as exc:
        live.emit(
            **base,
            status="error",
            message=f"MongoDB no confirmó la operación ({type(exc).__name__})",
            stage="mongo",
        )
        raise
    if isinstance(result, dict):
        base["cluster_id"] = result.get("owner_cluster", base["cluster_id"])
    else:
        base["cluster_id"] = None  # A listing reads the three clusters, not just the current write target.
    live.emit(
        **base,
        status="deleted" if operation == "DELETE" else "success",
        stage="mongo",
        message="Borrado lógico confirmado" if operation == "DELETE" else "Operación confirmada por MongoDB",
    )
    if not actor.admin:
        with live.lock:
            if actor.id in live.bots:
                live.bots[actor.id]["confirmed_requests"] += 1
    return result


def routes(kind, model):
    name = TABLES[kind]

    def create(body: model, request: Request, actor: Auth, idempotency_key: Key):
        return tracked(
            request,
            actor,
            kind,
            "INSERT",
            lambda: mutate(kind, "created", body.model_dump(mode="json"), actor, idempotency_key),
        )

    def update(
        entity_id: UUID, body: model, request: Request, actor: Auth, idempotency_key: Key, if_match: Version
    ):
        return tracked(
            request,
            actor,
            kind,
            "UPDATE",
            lambda: mutate(
                kind,
                "updated",
                body.model_dump(mode="json"),
                actor,
                idempotency_key,
                str(entity_id),
                if_match,
            ),
        )

    def delete(entity_id: UUID, request: Request, actor: Auth, idempotency_key: Key, if_match: Version):
        return tracked(
            request,
            actor,
            kind,
            "DELETE",
            lambda: mutate(kind, "deleted", {}, actor, idempotency_key, str(entity_id), if_match),
        )

    def get_one(entity_id: UUID, request: Request, actor: Auth):
        return tracked(request, actor, kind, "READ", lambda: read(kind, str(entity_id)))

    def get_list(
        request: Request,
        actor: Auth,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=10000),
    ):
        return tracked(request, actor, kind, "READ", lambda: listing(kind, limit, offset))

    router.add_api_route(f"/{name}", create, methods=["POST"], status_code=201, tags=[name])
    router.add_api_route(f"/{name}", get_list, methods=["GET"], tags=[name])
    router.add_api_route(f"/{name}/{{entity_id}}", get_one, methods=["GET"], tags=[name])
    router.add_api_route(f"/{name}/{{entity_id}}", update, methods=["PUT"], tags=[name])
    router.add_api_route(f"/{name}/{{entity_id}}", delete, methods=["DELETE"], tags=[name])


for _kind, _model in MODELS.items():
    routes(_kind, _model)
