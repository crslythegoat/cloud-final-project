from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from app import bots


class Stop:
    def __init__(self):
        self.stopped = False
        self.waits = []

    def is_set(self):
        return self.stopped

    def wait(self, seconds):
        self.waits.append(seconds)
        return self.stopped


def test_retry_retains_idempotency_key_and_only_confirms_success():
    seen, emitted, stop = [], [], Stop()

    def handle(request):
        seen.append(request.headers["Idempotency-Key"])
        return httpx.Response(
            503 if len(seen) == 1 else 201, json={"id": "saved", "owner_cluster": "mongo_2"}
        )

    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(handle)) as client:
        result = bots.request(
            client,
            "POST",
            "/categories",
            stop,
            {"code": "A", "name": "A"},
            notify=lambda **data: emitted.append(data),
        )
    assert result["id"] == "saved" and seen[0] == seen[1]
    assert [event["phase"] for event in emitted] == ["sent", "retrying", "sent", "confirmed"]
    assert emitted[-1]["owner_cluster"] == "mongo_2"


def test_stopped_bot_does_not_start_a_request():
    stop = Stop()
    stop.stopped = True
    with httpx.Client(
        base_url="http://test", transport=httpx.MockTransport(lambda _: pytest.fail("must not send"))
    ) as client:
        with pytest.raises(InterruptedError):
            bots.request(client, "GET", "/orders/id", stop)


def test_one_bot_cycle_has_ten_operations_and_uses_versions(monkeypatch):
    calls, reports, stop = [], [], Stop()
    real_client = httpx.Client

    def handle(request):
        calls.append(request)
        resource = request.url.path.split("/")[1]
        return httpx.Response(
            201 if request.method == "POST" else 200,
            json={"id": resource, "version": 2 if request.method == "PUT" else 1, "owner_cluster": "mongo_1"},
        )

    monkeypatch.setattr(
        bots.httpx, "Client", lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handle))
    )
    monkeypatch.setattr(
        bots, "get_settings", lambda: SimpleNamespace(bot_tokens={"bot-1": SecretStr("test")})
    )
    assert bots.run_bot(1, "http://test", 1, 0.5, stop, lambda bot, data: reports.append(data))
    assert [r.method for r in calls] == ["POST"] * 4 + ["GET", "PUT"] + ["DELETE"] * 4
    assert len(stop.waits) == 10 and set(stop.waits) == {0.5}
    product_delete = next(r for r in calls if r.method == "DELETE" and "/products/" in r.url.path)
    assert product_delete.headers["If-Match"] == "2"
    assert reports[-1]["state"] == "completed"
