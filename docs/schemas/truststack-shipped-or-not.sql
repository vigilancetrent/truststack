-- Schema for SqliteAuditStore (truststack-shipped-or-not).
--
-- This file is the canonical reference for the durable audit trail. The DDL
-- below is mirrored verbatim by shipped_or_not.audit._SCHEMA and is created
-- lazily by SqliteAuditStore.initialize() / .connect() on first use. Both
-- statements are idempotent (IF NOT EXISTS), so re-running is safe.
--
-- Storage model
-- -------------
-- Each verification is stored as one immutable, append-only row. The full
-- audit report (VerificationResult.to_report(), JSON) lives in `payload`;
-- `url` and `verified_at` are denormalized out of that JSON into their own
-- columns purely to make per-URL, time-ordered history() lookups fast.
--
-- Chronology
-- ----------
-- `id` is a monotonically increasing AUTOINCREMENT key, so ORDER BY id ASC is
-- exactly insertion (= chronological) order. history(url) and all() both rely
-- on this rather than parsing `verified_at`.
--
-- Round-tripping
-- --------------
-- history()/all() rehydrate each row by validating `payload` back into a
-- VerificationResult (Pydantic coerces ISO-8601 strings to datetimes), so a
-- persisted result is recovered losslessly.

CREATE TABLE IF NOT EXISTS verifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,  -- monotonic insertion order (chronological)
    url         TEXT NOT NULL,                       -- the verified URL (denormalized from payload)
    verified_at TEXT NOT NULL,                       -- ISO-8601 UTC timestamp (denormalized from payload)
    payload     TEXT NOT NULL                        -- VerificationResult.to_report() as a JSON document
);

-- Speeds up per-URL history() lookups; results are returned ORDER BY id ASC
-- (chronological) after filtering on url.
CREATE INDEX IF NOT EXISTS idx_verifications_url ON verifications (url);

-- Reference queries (informational; the store issues parameterized equivalents)
-- ---------------------------------------------------------------------------
-- Record a verdict:
--   INSERT INTO verifications (url, verified_at, payload) VALUES (?, ?, ?);
-- Replay one URL's history (oldest first):
--   SELECT payload FROM verifications WHERE url = ? ORDER BY id ASC;
-- Replay everything (oldest first):
--   SELECT payload FROM verifications ORDER BY id ASC;
