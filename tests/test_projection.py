from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest

from app.projection import deliver


def run(pg):
    run_id = uuid4()
    pg.execute(
        "INSERT INTO cloud_db.sync_runs(run_id,trigger_type,status) VALUES (%s,'manual','running')", (run_id,)
    )
    return run_id


def doc(**values):
    now = datetime.now(UTC)
    return {
        "_id": str(uuid4()),
        "owner_cluster": "mongo_1",
        "version": 1,
        "created_at": now,
        "updated_at": now,
        "deleted_at": None,
        **values,
    }


def category():
    code = uuid4().hex.upper()
    return doc(code=code, name=code, normalized_name=code.casefold())


def apply(pg, run_id, data, kind="category"):
    with pg.transaction():
        return deliver(pg, run_id, kind, data["owner_cluster"], data)


def test_repeat_and_tampered_version(pg):
    run_id, data = run(pg), category()
    assert apply(pg, run_id, data) == "applied"
    assert apply(pg, run_id, data) == "repeated"
    changed = deepcopy(data)
    changed.update(name="Changed", normalized_name="changed")
    with pytest.raises(ValueError, match="version"):
        apply(pg, run_id, changed)
    assert (
        pg.execute(
            "SELECT count(*) FROM cloud_db.categories WHERE category_id=%s", (data["_id"],)
        ).fetchone()[0]
        == 1
    )


def test_old_snapshot_cannot_resurrect_deleted_entity(pg):
    run_id, data = run(pg), category()
    newer = {**data, "version": 3, "deleted_at": datetime.now(UTC)}
    assert apply(pg, run_id, newer) == "applied"
    assert apply(pg, run_id, data) == "obsolete"
    assert pg.execute(
        "SELECT source_version,deleted_at IS NOT NULL FROM cloud_db.categories WHERE category_id=%s",
        (data["_id"],),
    ).fetchone() == (3, True)


def test_fk_failure_rolls_back_and_can_retry(pg):
    run_id, cat = run(pg), category()
    product = doc(
        sku=uuid4().hex.upper(), name="P", category_id=cat["_id"], unit_price_minor=100, currency="MXN"
    )
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        apply(pg, run_id, product, "product")
    assert (
        pg.execute(
            "SELECT count(*) FROM cloud_db.sync_receipts WHERE entity_id=%s", (product["_id"],)
        ).fetchone()[0]
        == 0
    )
    apply(pg, run_id, cat)
    assert apply(pg, run_id, product, "product") == "applied"


def test_order_normalization_prices_and_repeated_items(pg):
    run_id, cat = run(pg), category()
    email = f"{uuid4().hex}@example.org"
    customer = doc(name="C", email=email, normalized_email=email)
    product = doc(
        sku=uuid4().hex.upper(), name="P", category_id=cat["_id"], unit_price_minor=100, currency="MXN"
    )
    order = doc(
        order_reference=uuid4().hex.upper(),
        customer_id=customer["_id"],
        status="confirmed",
        currency="MXN",
        items=[{"product_id": product["_id"], "quantity": 2, "unit_price_minor": 100}],
    )
    for data, kind in [(cat, "category"), (customer, "customer"), (product, "product"), (order, "order")]:
        apply(pg, run_id, data, kind)
    apply(pg, run_id, {**product, "version": 2, "unit_price_minor": 200}, "product")
    apply(pg, run_id, {**order, "version": 2, "status": "completed"}, "order")
    assert apply(pg, run_id, {**order, "version": 2, "status": "completed"}, "order") == "repeated"
    assert pg.execute(
        "SELECT count(*),sum(quantity*unit_price_minor) FROM cloud_db.order_items WHERE order_id=%s",
        (order["_id"],),
    ).fetchone() == (1, 200)


def test_owner_collision_is_rejected(pg):
    run_id, data = run(pg), category()
    apply(pg, run_id, data)
    with pytest.raises(ValueError, match="another source"):
        apply(pg, run_id, {**data, "owner_cluster": "mongo_3"})
