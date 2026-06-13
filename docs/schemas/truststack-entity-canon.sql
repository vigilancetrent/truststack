-- Schema for truststack-entity-canon entity stores.
--
-- Both backends create their own table lazily on first use, so you do NOT need
-- to run this by hand. It is reproduced here for reference, for code review, and
-- for operators who prefer to pre-provision tables under migration control
-- (e.g. Alembic / Flyway) rather than rely on lazy CREATE TABLE IF NOT EXISTS.
--
--   * SqliteEntityStore   (entity_canon.stores.SqliteEntityStore)   -> SQLite
--   * PostgresEntityStore (entity_canon.stores.PostgresEntityStore) -> PostgreSQL
--
-- The model is a single `entities` table: a stable id, a canonical display name,
-- and a JSON/JSONB list of aliases (alternate surface forms used for matching).
-- Writes are upserts keyed on `id`, which is how a "merge" persists an updated
-- alias list onto an existing row instead of inserting a duplicate.

-- ===========================================================================
-- SQLite  (entity_canon.stores.SqliteEntityStore)
-- ===========================================================================
-- Aliases are a JSON-encoded list[str] stored in a TEXT column (json.dumps /
-- json.loads at the application boundary). Writes use
-- INSERT OR REPLACE INTO entities (...) for idempotent upserts on `id`.

CREATE TABLE IF NOT EXISTS entities (
    id      TEXT PRIMARY KEY,            -- stable entity identifier (uuid4 hex)
    name    TEXT NOT NULL,               -- canonical display name (non-blank)
    aliases TEXT NOT NULL DEFAULT '[]'   -- JSON-encoded list[str] of aliases
);

-- ===========================================================================
-- PostgreSQL  (entity_canon.stores.PostgresEntityStore, asyncpg)
-- ===========================================================================
-- Aliases are stored as JSONB. The store applies the DDL below on first use.
--   * Writes:   INSERT INTO entities (...) ... ON CONFLICT (id) DO UPDATE
--               SET name = EXCLUDED.name, aliases = EXCLUDED.aliases
--               -> idempotent upserts; backs bulk_import merges.
--   * Reads:    SELECT id, name, aliases FROM entities                (all)
--               SELECT ... FROM entities WHERE id = $1                 (get)
--   * Deletes:  DELETE FROM entities WHERE id = $1                     (delete)
--               -> the asyncpg "DELETE <n>" command tag yields the row count
--                  that backs the REST DELETE /entities/{id} 204 vs 404.

CREATE TABLE IF NOT EXISTS entities (
    id      TEXT PRIMARY KEY,                    -- stable entity identifier (uuid4 hex)
    name    TEXT NOT NULL,                       -- canonical display name (non-blank)
    aliases JSONB NOT NULL DEFAULT '[]'::jsonb   -- list[str] of aliases
);

-- ---------------------------------------------------------------------------
-- Optional operational indexes (NOT created by the store; add if you need them)
-- ---------------------------------------------------------------------------
-- The PRIMARY KEY on `id` already covers get() and delete(). The two indexes
-- below are useful at scale and are safe, additive enhancements:
--
--   * a case-insensitive index on `name` to accelerate exact-name lookups /
--     reporting queries over the canonical surface form;
--   * a GIN index on the JSONB `aliases` to support containment queries such as
--     "which entity has alias X?" (aliases @> '["X"]'::jsonb).
--
-- CREATE INDEX IF NOT EXISTS entities_name_lower_idx
--     ON entities (lower(name));
--
-- CREATE INDEX IF NOT EXISTS entities_aliases_gin_idx
--     ON entities USING gin (aliases jsonb_path_ops);
