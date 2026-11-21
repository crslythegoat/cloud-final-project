"""Bounded concurrent HTTP clients. No direct database access; unique synthetic data."""

import random
from itertools import count
from threading import Event
from uuid import uuid4

import httpx

from app.core.config import get_settings


def request(client, method, path, stop, body=None, version=None, notify=None):
    headers = {"Idempotency-Key": str(uuid4())}
    request_id = headers["Idempotency-Key"]
    operation = {"POST": "INSERT", "GET": "READ", "PUT": "UPDATE", "DELETE": "DELETE"}[method]
    last_code = "NetworkError"

    def emit(phase, **extra):
        if notify:
            notify(
                phase=phase,
                request_id=request_id,
                method=method,
                resource=path.split("/")[1],
                operation=operation,
                **extra,
            )

    if version is not None:
        headers["If-Match"] = str(version)
    for attempt in range(10):
        if stop.is_set():
            raise InterruptedError("Simulation stopped")
        emit("sent", state="running", attempt=attempt + 1)
        try:
            response = client.request(method, path, json=body, headers=headers)
            last_code = f"HTTP_{response.status_code}"
            if response.status_code not in (409, 429, 502, 503, 504):
                if response.is_error:
                    emit("failed", state="error", status_code=response.status_code, error_code=last_code)
                response.raise_for_status()
                result = response.json()
                emit(
                    "confirmed",
                    state="running",
                    status_code=response.status_code,
                    entity_id=result.get("id"),
                    operation_id=result.get("operation_id"),
                    owner_cluster=result.get("owner_cluster"),
                )
                return result
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_code = type(exc).__name__
        if attempt < 9:
            emit("retrying", state="retrying", error_code=last_code, attempt=attempt + 1)
            stop.wait(min(0.3 * 2**attempt, 3) + random.uniform(0, 0.3))
    emit("failed", state="error", error_code=last_code, attempt=10)
    raise RuntimeError("Request retries exhausted; retry key retained only for this bounded request")


def run_bot(number, base_url, rounds, interval, stop=None, report=None):
    stop = stop or Event()
    bot_id = f"bot-{number}"
    token = get_settings().bot_tokens.get(bot_id)
    if not token:
        raise ValueError(f"Missing token for {bot_id}")

    def notify(**data):
        if report:
            report(bot_id, data)

    def send(client, method, path, stop, body=None, version=None):
        result = request(client, method, path, stop, body, version, notify=notify)
        stop.wait(interval)
        return result

    with httpx.Client(
        base_url=base_url,
        headers={"Authorization": f"Bearer {token.get_secret_value()}"},
        timeout=30,
        trust_env=False,
    ) as client:
        for turn in count() if rounds is None else range(rounds):
            if stop.is_set():
                break
            code = f"B{number}-{uuid4().hex[:12].upper()}"
            try:
                notify(state="running", round=turn + 1, operation="INSERT")
                category = send(client, "POST", "/categories", stop, {"code": code, "name": code})
                customer = send(
                    client,
                    "POST",
                    "/customers",
                    stop,
                    {"name": f"Demo {code}", "email": f"{code.lower()}@example.org"},
                )
                product_body = {
                    "sku": code,
                    "name": f"Product {code}",
                    "category_id": category["id"],
                    "unit_price_minor": 25000,
                }
                product = send(client, "POST", "/products", stop, product_body)
                order = send(
                    client,
                    "POST",
                    "/orders",
                    stop,
                    {
                        "order_reference": code,
                        "customer_id": customer["id"],
                        "status": "confirmed",
                        "items": [{"product_id": product["id"], "quantity": 2, "unit_price_minor": 25000}],
                    },
                )
                notify(state="running", operation="READ")
                send(client, "GET", f"/orders/{order['id']}", stop)
                notify(state="running", operation="UPDATE")
                product = send(
                    client,
                    "PUT",
                    f"/products/{product['id']}",
                    stop,
                    {**product_body, "unit_price_minor": 30000},
                    product["version"],
                )
                notify(state="running", operation="DELETE")
                for table, entity in [
                    ("orders", order),
                    ("products", product),
                    ("customers", customer),
                    ("categories", category),
                ]:
                    send(client, "DELETE", f"/{table}/{entity['id']}", stop, version=entity["version"])
                notify(state="running", round=turn + 1, completed_rounds=turn + 1, operation="cycle")
            except InterruptedError:
                notify(state="stopped", operation="cycle")
                break
            except (httpx.HTTPError, RuntimeError) as exc:
                notify(state="error", exception_code=type(exc).__name__)
                return False
    notify(state="stopped" if stop.is_set() else "completed", operation="cycle")
    return True
