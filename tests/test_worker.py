"""ETL orchestration keeps all sources and dependency order after bot routing changes."""

from contextlib import nullcontext
from types import SimpleNamespace

from app import worker
from app.runtime import CLUSTERS, LiveState


def test_etl_scans_all_clusters_with_no_bots_and_preserves_source(monkeypatch):
    state = LiveState()
    scans, deliveries, committed = [], [], set()

    def source(cluster, name):
        scans.append((cluster, name))
        return SimpleNamespace(find=lambda query: [{"_id": f"{cluster}:{name}", "owner_cluster": cluster}])

    def deliver(conn, run_id, kind, cluster, doc):
        assert doc["owner_cluster"] == cluster
        deliveries.append((kind, cluster))
        if doc["_id"] in committed:
            return "repeated"
        committed.add(doc["_id"])
        return "applied"

    conn = SimpleNamespace(
        closed=False,
        execute=lambda *args: SimpleNamespace(fetchone=lambda: (True,)),
        transaction=nullcontext,
    )
    monkeypatch.setattr(worker, "postgres", lambda: nullcontext(conn))
    monkeypatch.setattr(worker, "collection", source)
    monkeypatch.setattr(worker, "deliver", deliver)
    monkeypatch.setattr(worker, "live", state)

    result = worker.cycle("manual")
    expected = [(kind, cluster) for kind in worker.ORDER for cluster in CLUSTERS]
    assert deliveries == expected
    assert len(scans) == 12 and result["counts"]["applied"] == 12
    assert result["status"] == "succeeded"
    assert [(e["cluster_id"], e["collection"]) for e in state.events if e["status"] == "sent"] == scans
    assert {e["cluster_id"] for e in state.events} == set(CLUSTERS)

    result = worker.cycle("manual")
    assert result["counts"] == {"repeated": 12}
    assert result["status"] == "succeeded" and len(committed) == 12
