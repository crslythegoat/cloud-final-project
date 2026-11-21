from dataclasses import dataclass
from hmac import compare_digest
from typing import Annotated

from fastapi import Header, HTTPException, Request

from app.core.config import get_settings
from app.runtime import live


@dataclass(frozen=True)
class Actor:
    id: str
    cluster: str
    admin: bool = False


def authenticate(request: Request, authorization: Annotated[str | None, Header()] = None) -> Actor:
    settings = get_settings()
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required", headers={"WWW-Authenticate": "Bearer"})
    token = authorization[7:]
    admin = settings.api_token.get_secret_value()
    if admin and compare_digest(token, admin):
        request.state.actor = Actor("operator", "mongo_1", True)
        return request.state.actor
    for name, secret in settings.bot_tokens.items():
        if secret.get_secret_value() and compare_digest(token, secret.get_secret_value()):
            request.state.actor = Actor(name, live.destination(name))
            return request.state.actor
    raise HTTPException(401, "Invalid token", headers={"WWW-Authenticate": "Bearer"})


def require_admin(actor):
    if not actor.admin:
        raise HTTPException(403, "Operator role required")
