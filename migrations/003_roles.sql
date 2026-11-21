-- OPTIONAL administrator task. Review on a Neon development branch first.
-- No passwords are embedded. Set passwords interactively using psql \password.
-- Run after 001/002. This does not change object ownership or existing passwords.
BEGIN;
DO $roles$
DECLARE role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY['postgres_developer','postgres_user','dev1','usr1','cloud_db_runtime'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE', role_name);
        END IF;
    END LOOP;
END;
$roles$;
GRANT postgres_developer TO dev1;
GRANT postgres_user TO usr1;
DO $grant$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO postgres_developer, postgres_user, cloud_db_runtime', current_database());
    EXECUTE format('GRANT CREATE ON DATABASE %I TO postgres_developer', current_database());
END;
$grant$;
GRANT USAGE, CREATE ON SCHEMA cloud_db TO postgres_developer;
GRANT USAGE ON SCHEMA cloud_db TO postgres_user, cloud_db_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA cloud_db TO postgres_developer, postgres_user;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA cloud_db TO postgres_developer, postgres_user;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA cloud_db TO cloud_db_runtime;
GRANT DELETE ON cloud_db.order_items TO cloud_db_runtime;
-- Defaults below apply to future objects created by the current migration owner.
ALTER DEFAULT PRIVILEGES IN SCHEMA cloud_db GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO postgres_developer, postgres_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA cloud_db GRANT EXECUTE ON FUNCTIONS TO postgres_developer, postgres_user;
ALTER DEFAULT PRIVILEGES FOR ROLE dev1 IN SCHEMA cloud_db GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO postgres_user;
ALTER DEFAULT PRIVILEGES FOR ROLE dev1 IN SCHEMA cloud_db GRANT EXECUTE ON FUNCTIONS TO postgres_user;
COMMIT;
