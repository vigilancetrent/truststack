-- =====================================================================
-- Schemas for meta_token_vault durable backends.
--
-- Token VALUES are always stored encrypted via the configured Encryptor
-- (FernetEncryptor in production; NoopEncryptor is DEV ONLY and leaves the
-- value in cleartext). All other columns are stored in cleartext.
--
-- Token shape (see meta_token_vault.models.Token):
--   id          uuid4 hex, primary key
--   value       secret material, ENCRYPTED at rest
--   app_id      Meta app id
--   scopes      granted scopes
--   issued_at   issue time (UTC)
--   expires_at  expiry (UTC), NULL = never expires
--
-- "Active" token selection is performed in Python, not SQL: of all tokens
-- for an app whose expires_at is NULL or in the future, the one with the
-- greatest issued_at is returned.
-- =====================================================================


-- =====================================================================
-- SqliteTokenStore (stdlib sqlite3). Created on construction.
-- Datetimes are ISO-8601 strings (UTC); scopes are comma-separated.
-- =====================================================================

CREATE TABLE IF NOT EXISTS tokens (
    id          TEXT PRIMARY KEY,   -- Token.id (uuid4 hex)
    app_id      TEXT NOT NULL,      -- Meta app id
    value       TEXT NOT NULL,      -- encrypted token secret (Encryptor.encrypt output)
    scopes      TEXT NOT NULL,      -- comma-separated scope list ("" when empty)
    issued_at   TEXT NOT NULL,      -- ISO-8601 UTC issue time
    expires_at  TEXT                -- ISO-8601 UTC expiry, NULL = never expires
);

-- Speeds up get_active/all lookups that filter by app_id.
CREATE INDEX IF NOT EXISTS idx_tokens_app_id ON tokens (app_id);

-- Upsert performed by put():
--   INSERT INTO tokens (id, app_id, value, scopes, issued_at, expires_at)
--   VALUES (?, ?, ?, ?, ?, ?)
--   ON CONFLICT(id) DO UPDATE SET
--       app_id=excluded.app_id, value=excluded.value, scopes=excluded.scopes,
--       issued_at=excluded.issued_at, expires_at=excluded.expires_at;


-- =====================================================================
-- PostgresTokenStore (asyncpg). Created automatically on first use; shown
-- here for reference / manual provisioning. Default table name is
-- meta_token_vault_tokens (configurable via the `table` argument; validated
-- as a Python identifier). Datetimes are TIMESTAMPTZ; scopes a text array.
-- =====================================================================

CREATE TABLE IF NOT EXISTS meta_token_vault_tokens (
    id          TEXT PRIMARY KEY,             -- Token.id (uuid4 hex)
    app_id      TEXT NOT NULL,                -- Meta app id
    value       TEXT NOT NULL,                -- encrypted token secret (Encryptor.encrypt output)
    scopes      TEXT[] NOT NULL DEFAULT '{}', -- granted scopes
    issued_at   TIMESTAMPTZ NOT NULL,         -- issue time (UTC)
    expires_at  TIMESTAMPTZ                   -- expiry, NULL = never expires
);

-- Speeds up get_active/all lookups that filter by app_id.
CREATE INDEX IF NOT EXISTS idx_meta_token_vault_tokens_app_id
    ON meta_token_vault_tokens (app_id);

-- Upsert performed by put():
--   INSERT INTO meta_token_vault_tokens (id, app_id, value, scopes, issued_at, expires_at)
--   VALUES ($1, $2, $3, $4, $5, $6)
--   ON CONFLICT (id) DO UPDATE SET
--       app_id=excluded.app_id, value=excluded.value, scopes=excluded.scopes,
--       issued_at=excluded.issued_at, expires_at=excluded.expires_at;


-- =====================================================================
-- Key-value / secret-manager backends (no SQL schema).
--
-- The following backends are schemaless from a SQL perspective; they store
-- each token as one encrypted, JSON-serialised document. Layout reference:
--
--   AwsSecretsManagerTokenStore
--     Secret name : {prefix}/{app_id}/{token_id}   (prefix default "meta-token-vault")
--     Value       : JSON {id, app_id, value(encrypted), scopes, issued_at, expires_at}
--     Tags        : meta_token_vault:app_id = <app_id>
--                   meta_token_vault:managed = "true"
--
--   AzureKeyVaultTokenStore
--     Secret name : {prefix}-{app_id}-{token_id}   (prefix default "mtv")
--     Value       : same JSON document as above
--     Tags        : meta_token_vault_app_id = <app_id>
--                   meta_token_vault_managed = "true"
--
--   HashiCorpVaultTokenStore (KV v2)
--     Path        : {mount_point}/{path_prefix}/{app_id}/{token_id}
--                   (mount_point default "secret", path_prefix default "meta-token-vault")
--     Data        : { "token": <same JSON document as above> }
--
-- In every case the JSON "value" field is the Encryptor.encrypt output, never
-- the plaintext token. issued_at / expires_at are ISO-8601 UTC strings.
-- =====================================================================
