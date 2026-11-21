-- Cloud DB | Migration 001 | PostgreSQL
-- Run the entire file once on the development branch, using the schema owner.
-- If the schema already exists, stop and inspect it before applying changes.
BEGIN;

CREATE SCHEMA cloud_db;
SET LOCAL search_path = cloud_db, pg_catalog;

-- Global entity IDs and source timestamps are supplied by the ETL.
-- A logical deletion retains the row, its relationships and its business keys.
CREATE TABLE categories (
    category_id uuid PRIMARY KEY,
    code text NOT NULL UNIQUE CHECK (code <> '' AND code = upper(btrim(code))),
    name text NOT NULL CHECK (btrim(name) <> ''),
    normalized_name text NOT NULL UNIQUE CHECK (btrim(normalized_name) <> ''),
    source_cluster text NOT NULL CHECK (source_cluster IN ('mongo_1','mongo_2','mongo_3')),
    source_version bigint NOT NULL CHECK (source_version >= 1),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    deleted_at timestamptz
);

CREATE TABLE customers (
    customer_id uuid PRIMARY KEY,
    name text NOT NULL CHECK (btrim(name) <> ''),
    email text NOT NULL CHECK (btrim(email) <> ''),
    normalized_email text NOT NULL UNIQUE CHECK (btrim(normalized_email) <> ''),
    source_cluster text NOT NULL CHECK (source_cluster IN ('mongo_1','mongo_2','mongo_3')),
    source_version bigint NOT NULL CHECK (source_version >= 1),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    deleted_at timestamptz
);

CREATE TABLE products (
    product_id uuid PRIMARY KEY,
    sku text NOT NULL UNIQUE CHECK (sku <> '' AND sku = upper(btrim(sku))),
    name text NOT NULL CHECK (btrim(name) <> ''),
    description text,
    category_id uuid NOT NULL REFERENCES categories(category_id),
    unit_price_minor bigint NOT NULL CHECK (unit_price_minor >= 0),
    currency text NOT NULL CHECK (currency = 'MXN'),
    source_cluster text NOT NULL CHECK (source_cluster IN ('mongo_1','mongo_2','mongo_3')),
    source_version bigint NOT NULL CHECK (source_version >= 1),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    deleted_at timestamptz
);

CREATE TABLE orders (
    order_id uuid PRIMARY KEY,
    order_reference text NOT NULL UNIQUE CHECK (btrim(order_reference) <> ''),
    customer_id uuid NOT NULL REFERENCES customers(customer_id),
    status text NOT NULL CHECK (status IN ('draft','confirmed','completed','cancelled')),
    currency text NOT NULL CHECK (currency = 'MXN'),
    source_cluster text NOT NULL CHECK (source_cluster IN ('mongo_1','mongo_2','mongo_3')),
    source_version bigint NOT NULL CHECK (source_version >= 1),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    deleted_at timestamptz
);

CREATE TABLE order_items (
    order_id uuid NOT NULL REFERENCES orders(order_id),
    product_id uuid NOT NULL REFERENCES products(product_id),
    quantity integer NOT NULL CHECK (quantity > 0),
    unit_price_minor bigint NOT NULL CHECK (unit_price_minor >= 0),
    PRIMARY KEY (order_id, product_id)
);

CREATE INDEX products_category_idx ON products(category_id);
CREATE INDEX orders_customer_idx ON orders(customer_id);
CREATE INDEX order_items_product_idx ON order_items(product_id);

-- Exercise relationships, a unique key, a check and a historical price.
SAVEPOINT sample_data;
DO $test$
DECLARE
    cat uuid := gen_random_uuid();
    customer uuid := gen_random_uuid();
    product uuid := gen_random_uuid();
    purchase uuid := gen_random_uuid();
BEGIN
    INSERT INTO categories (category_id, code, name, normalized_name, source_cluster, source_version, created_at, updated_at)
        VALUES (cat, 'TEST', 'Test', 'test', 'mongo_1', 1, now(), now());
    INSERT INTO customers (customer_id, name, email, normalized_email, source_cluster, source_version, created_at, updated_at)
        VALUES (customer, 'Test', 'test@example.org', 'test@example.org', 'mongo_2', 1, now(), now());
    INSERT INTO products (product_id, sku, name, category_id, unit_price_minor, currency, source_cluster, source_version, created_at, updated_at)
        VALUES (product, 'TEST-001', 'Test', cat, 24990, 'MXN', 'mongo_1', 1, now(), now());
    INSERT INTO orders (order_id, order_reference, customer_id, status, currency, source_cluster, source_version, created_at, updated_at)
        VALUES (purchase, 'TEST-001', customer, 'draft', 'MXN', 'mongo_3', 1, now(), now());
    INSERT INTO order_items VALUES (purchase, product, 2, 24990);
    UPDATE products SET unit_price_minor = 30000, source_version = 2 WHERE product_id = product;
    IF (SELECT quantity * unit_price_minor FROM order_items WHERE order_id = purchase) IS DISTINCT FROM 49980 THEN
        RAISE EXCEPTION 'Historical order price check failed';
    END IF;
    BEGIN
        INSERT INTO products (product_id, sku, name, category_id, unit_price_minor, currency, source_cluster, source_version, created_at, updated_at)
            VALUES (gen_random_uuid(), 'TEST-001', 'Duplicate SKU', cat, 100, 'MXN', 'mongo_2', 1, now(), now());
        RAISE EXCEPTION 'Duplicate SKU accepted';
    EXCEPTION WHEN unique_violation THEN NULL;
    END;
    BEGIN
        UPDATE order_items SET quantity = 0 WHERE order_id = purchase;
        RAISE EXCEPTION 'Zero quantity accepted';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
    BEGIN
        INSERT INTO order_items VALUES (purchase, gen_random_uuid(), 1, 100);
        RAISE EXCEPTION 'Missing product accepted';
    EXCEPTION WHEN foreign_key_violation THEN NULL;
    END;
END;
$test$;
ROLLBACK TO SAVEPOINT sample_data;
COMMIT;

SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'cloud_db' AND table_type = 'BASE TABLE'
ORDER BY table_name;
