-- Cloud DB | Migration 002 | Run the whole file in one PostgreSQL session.
-- Prerequisite: migration 001. Apply once as the owner of schema cloud_db.
BEGIN;

-- Check that all five tables resolve before creating anything.
SELECT 1 FROM cloud_db.categories, cloud_db.products, cloud_db.customers,
              cloud_db.orders, cloud_db.order_items LIMIT 0;

CREATE TABLE cloud_db.sync_runs (
    run_id uuid PRIMARY KEY,
    trigger_type text NOT NULL CHECK (trigger_type IN ('scheduled', 'manual')),
    status text NOT NULL CHECK (status IN
        ('running', 'succeeded', 'partial', 'failed', 'interrupted', 'skipped_overlap')),
    started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz,
    events_applied bigint NOT NULL DEFAULT 0 CHECK (events_applied >= 0),
    events_obsolete bigint NOT NULL DEFAULT 0 CHECK (events_obsolete >= 0),
    events_repeated bigint NOT NULL DEFAULT 0 CHECK (events_repeated >= 0),
    events_deferred bigint NOT NULL DEFAULT 0 CHECK (events_deferred >= 0),
    events_blocked bigint NOT NULL DEFAULT 0 CHECK (events_blocked >= 0),
    CONSTRAINT sync_runs_completion CHECK (
        (status = 'running' AND finished_at IS NULL)
        OR (status <> 'running' AND finished_at IS NOT NULL AND finished_at >= started_at)
    )
);
CREATE INDEX sync_runs_started_idx ON cloud_db.sync_runs (started_at DESC);

-- Retain the outcome of the first committed handling of each event.
-- A retry finding this receipt must compare the immutable event identity/content
-- and skip the business write. Conflicts and transient failures get no receipt.
CREATE TABLE cloud_db.sync_receipts (
    event_id uuid PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES cloud_db.sync_runs (run_id),
    source_cluster text NOT NULL CHECK (source_cluster IN ('mongo_1', 'mongo_2', 'mongo_3')),
    event_category text NOT NULL CHECK (event_category IN ('business', 'audit')),
    entity_type text NOT NULL,
    entity_id uuid NOT NULL,
    entity_version bigint,
    event_digest bytea NOT NULL CHECK (octet_length(event_digest) = 32),
    outcome text NOT NULL CHECK (outcome IN ('applied', 'obsolete', 'equivalent')),
    processed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT sync_receipts_target CHECK (
        (event_category = 'business' AND entity_type IN ('category','product','customer','order')
         AND entity_version IS NOT NULL AND entity_version >= 1)
        OR (event_category = 'audit' AND entity_type = 'audit_event'
            AND entity_version IS NULL AND outcome <> 'obsolete')
    )
);
CREATE INDEX sync_receipts_run_idx ON cloud_db.sync_receipts (run_id);

-- run_id may be absent for API requests and offline logs.
CREATE TABLE cloud_db.system_logs (
    log_id uuid PRIMARY KEY,
    occurred_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    event_category text NOT NULL CHECK (event_category IN ('technical', 'audit')),
    level text NOT NULL CHECK (level IN ('debug','info','warning','error','critical')),
    component text NOT NULL CHECK (btrim(component) <> ''),
    event_type text NOT NULL CHECK (btrim(event_type) <> ''),
    outcome text NOT NULL CHECK (btrim(outcome) <> ''),
    request_id uuid,
    operation_id uuid,
    sync_event_id uuid,
    run_id uuid REFERENCES cloud_db.sync_runs (run_id),
    bot_id text,
    session_id uuid,
    entity_type text,
    entity_id uuid,
    source_cluster text CHECK (source_cluster IN ('mongo_1','mongo_2','mongo_3')),
    duration_ms bigint CHECK (duration_ms >= 0),
    error_code text,
    message text NOT NULL CHECK (btrim(message) <> ''),
    details jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(details) = 'object')
);
CREATE INDEX system_logs_time_idx ON cloud_db.system_logs (occurred_at DESC);
CREATE INDEX system_logs_operation_idx ON cloud_db.system_logs (operation_id)
    WHERE operation_id IS NOT NULL;
CREATE INDEX system_logs_run_idx ON cloud_db.system_logs (run_id) WHERE run_id IS NOT NULL;

-- Replay a delivery and verify that its original receipt survives.
-- Everything after this savepoint is a test fixture up to ROLLBACK TO.
SAVEPOINT delivery_test;
INSERT INTO cloud_db.sync_runs (run_id, trigger_type, status)
VALUES ('00000000-0000-4000-8000-000000000001', 'manual', 'running');

INSERT INTO cloud_db.sync_receipts
    (event_id, run_id, source_cluster, event_category, entity_type, entity_id,
     entity_version, event_digest, outcome)
VALUES ('00000000-0000-4000-8000-000000000002', '00000000-0000-4000-8000-000000000001',
        'mongo_1', 'business', 'product', '00000000-0000-4000-8000-000000000003',
        1, decode(repeat('ab', 32), 'hex'), 'applied');

INSERT INTO cloud_db.sync_receipts
    (event_id, run_id, source_cluster, event_category, entity_type, entity_id,
     entity_version, event_digest, outcome)
SELECT event_id, run_id, source_cluster, event_category, entity_type, entity_id,
       entity_version, event_digest, 'equivalent'
FROM cloud_db.sync_receipts
WHERE event_id = '00000000-0000-4000-8000-000000000002'
ON CONFLICT (event_id) DO NOTHING;

INSERT INTO cloud_db.system_logs
    (log_id, occurred_at, event_category, level, component, event_type, outcome,
     run_id, sync_event_id, message)
VALUES ('00000000-0000-4000-8000-000000000004', clock_timestamp(), 'technical', 'info',
        'worker', 'delivery_retry', 'receipt_found', '00000000-0000-4000-8000-000000000001',
        '00000000-0000-4000-8000-000000000002', 'Existing receipt found');

CREATE TEMP TABLE migration_002_check (passed boolean NOT NULL CHECK (passed)) ON COMMIT DROP;
INSERT INTO migration_002_check
SELECT count(*) = 1 AND bool_and(r.outcome = 'applied')
FROM cloud_db.sync_receipts r
JOIN cloud_db.system_logs l ON l.sync_event_id = r.event_id AND l.run_id = r.run_id
WHERE r.event_id = '00000000-0000-4000-8000-000000000002';

ROLLBACK TO SAVEPOINT delivery_test;
COMMIT;

SELECT table_name FROM information_schema.tables
WHERE table_schema = 'cloud_db' AND table_type = 'BASE TABLE'
ORDER BY table_name;
