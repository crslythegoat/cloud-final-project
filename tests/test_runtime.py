import asyncio
from types import SimpleNamespace

import pytest

from app.events import stream_events
from app.runtime import LiveState


@pytest.mark.parametrize("count", range(10))
def test_distribution_for_every_active_count(count):
    state = LiveState()
    state.bots = {f"bot-{i}": {"state": "running"} for i in range(1, count + 1)}
    snapshot = state.snapshot()
    assert list(snapshot["cluster_loads"].values()) == [
        min(count, 3),
        min(max(count - 3, 0), 3),
        max(count - 6, 0),
    ]
    assert sum(snapshot["cluster_loads"].values()) == count
    for name, cluster in snapshot["assignments"].items():
        assert state.destination(name) == cluster == snapshot["bots"][name]["cluster_id"]
    assert state.destination() == "mongo_1"


def test_nonconsecutive_ids_and_stopping_bot_keep_capacity_until_finished():
    state = LiveState()
    state.bots = {f"bot-{i}": {"state": "running"} for i in (2, 5, 8, 9)}
    assert state.assignments() == {
        "bot-2": "mongo_1",
        "bot-5": "mongo_1",
        "bot-8": "mongo_1",
        "bot-9": "mongo_2",
    }
    state.bots["bot-2"]["state"] = "stopping"
    assert state.destination("bot-9") == "mongo_2"
    state.bots["bot-2"]["state"] = "stopped"
    assert state.destination("bot-9") == "mongo_1"
    assert state.snapshot()["bots"]["bot-2"]["cluster_id"] is None


def test_stream_snapshot_live_events_and_disconnect(monkeypatch):
    from socketless_loop import SocketlessLoop

    import app.events

    state = LiveState()
    monkeypatch.setattr(app.events, "live", state)
    disconnected = False

    async def is_disconnected():
        return disconnected

    async def no_wait(_):
        return

    monkeypatch.setattr(app.events.asyncio, "sleep", no_wait)

    async def exercise():
        nonlocal disconnected
        stream = stream_events(SimpleNamespace(is_disconnected=is_disconnected))
        snapshot = await anext(stream)
        assert "event: snapshot" in snapshot
        event = state.emit(
            bot_id="bot-2",
            operation="DELETE",
            collection="orders",
            cluster_id="mongo_2",
            status="deleted",
            message="Borrado confirmado",
        )
        frame = await anext(stream)
        assert f"id: {event['id']}" in frame and "event: operation" in frame
        assert '"status": "deleted"' in frame
        disconnected = True
        try:
            await anext(stream)
        except StopAsyncIteration:
            pass
        else:
            raise AssertionError("Stream did not close on disconnect")

    with asyncio.Runner(loop_factory=SocketlessLoop) as runner:
        runner.run(exercise())


def test_bot_manager_limit_stop_and_options(monkeypatch):
    import pytest
    from fastapi import HTTPException

    from app import simulation
    from app.runtime import live
    from app.simulation import BotCount, BotManager

    threads = []

    class FakeThread:
        def __init__(self, target, args, **kwargs):
            self.args = args
            self.alive = False
            threads.append(self)

        def start(self):
            self.alive = True

        def is_alive(self):
            return self.alive

        def join(self, **kwargs):
            self.alive = False

    monkeypatch.setattr(simulation, "Thread", FakeThread)
    monkeypatch.setattr(
        simulation,
        "get_settings",
        lambda: SimpleNamespace(bot_tokens={f"bot-{i}": "unused" for i in range(1, 10)}),
    )
    monkeypatch.setattr(live, "bots", {})
    manager = BotManager()
    result = manager.resize(BotCount(count=9, interval_seconds=0.5, rounds=3))
    assert result["active_count"] == 9
    assert result["cluster_loads"] == {"mongo_1": 3, "mongo_2": 3, "mongo_3": 3}
    assert len(threads) == 9
    assert all(t.args[1].rounds == 3 and t.args[1].interval_seconds == 0.5 for t in threads)
    with pytest.raises(HTTPException):
        manager.start(1, BotCount(count=1))
    manager.resize(BotCount(count=0))
    assert all(stop.is_set() for _, stop in manager.jobs.values())
    assert all(b["state"] == "stopping" for b in live.bots.values())


def test_cleanup_never_selects_business_or_unknown_collections():
    from app.cleanup_mongo import inspect

    class DB:
        def list_collection_names(self):
            return ["categories", "products", "customers", "orders", "sync_outbox", "external_data"]

        def __getitem__(self, name):
            assert name == "sync_outbox"
            return SimpleNamespace(count_documents=lambda _: 7)

    plan = inspect([("mongo_1", DB())])
    assert [p[2] for p in plan] == ["sync_outbox"]


def test_cleanup_refuses_case_aliases():
    import pytest

    from app.cleanup_mongo import inspect

    with pytest.raises(ValueError, match="mayúsculas"):
        inspect([("mongo_1", SimpleNamespace(list_collection_names=lambda: ["Categories", "categories"]))])
