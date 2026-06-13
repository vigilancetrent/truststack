-- Schema for the durable task stores (truststack-task-dedupe).
--
-- Both SqliteTaskStore and PostgresTaskStore are created lazily on first use;
-- the DDL is documented here for reference and for provisioning Postgres ahead
-- of time. The SQLite layout uses a TEXT payload (JSON); the Postgres layout
-- uses JSONB.

-- ---------------------------------------------------------------------------
-- SQLite (SqliteTaskStore) — stdlib sqlite3, payload as JSON text.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,   -- engine-assigned uuid4 hex
    fingerprint TEXT NOT NULL,      -- 16 hex-char intent fingerprint
    payload     TEXT NOT NULL       -- Task serialized as JSON (Task.model_dump_json)
);

-- Speeds up exact-fingerprint short-circuit lookups.
CREATE INDEX IF NOT EXISTS idx_tasks_fingerprint ON tasks (fingerprint);

-- ---------------------------------------------------------------------------
-- Postgres (PostgresTaskStore) — asyncpg, payload as JSONB.
-- The default table name is "tasks"; PostgresTaskStore(dsn, table=...) lets you
-- override it (the value is validated as a Python identifier).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,   -- engine-assigned uuid4 hex
    fingerprint TEXT NOT NULL,      -- 16 hex-char intent fingerprint
    payload     JSONB NOT NULL      -- Task serialized as JSON (Task.model_dump_json)
);

-- Speeds up exact-fingerprint short-circuit / has_fingerprint() lookups.
CREATE INDEX IF NOT EXISTS idx_tasks_fingerprint ON tasks (fingerprint);
