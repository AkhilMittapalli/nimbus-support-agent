-- Idempotent schema. Applied on startup when AUTO_MIGRATE=true, or via `python -m app.cli migrate`.
-- The embedding dimension placeholder {{EMBEDDING_DIM}} is substituted from settings.

CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------------------------
-- Mock "system of record" used by the agent's tools
-- ---------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS customers (
    id                 TEXT PRIMARY KEY,
    name               TEXT NOT NULL,
    email              TEXT NOT NULL UNIQUE,
    pin_hash           TEXT NOT NULL,
    company            TEXT,
    tier               TEXT NOT NULL CHECK (tier IN ('free', 'starter', 'pro', 'enterprise')),
    contract_status    TEXT NOT NULL CHECK (contract_status IN ('active', 'past_due', 'suspended', 'cancelled')),
    contract_end       DATE,
    billing_email      TEXT NOT NULL,
    notification_pref  TEXT NOT NULL DEFAULT 'email',
    phone              TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS invoices (
    id              TEXT PRIMARY KEY,
    customer_id     TEXT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    description     TEXT NOT NULL,
    amount_cents    INTEGER NOT NULL CHECK (amount_cents >= 0),
    refunded_cents  INTEGER NOT NULL DEFAULT 0 CHECK (refunded_cents >= 0),
    issued_on       DATE NOT NULL,
    status          TEXT NOT NULL DEFAULT 'paid'
);
CREATE INDEX IF NOT EXISTS invoices_customer_idx ON invoices (customer_id);

CREATE TABLE IF NOT EXISTS refunds (
    id            TEXT PRIMARY KEY,
    invoice_id    TEXT NOT NULL REFERENCES invoices(id),
    customer_id   TEXT NOT NULL REFERENCES customers(id),
    session_id    UUID,
    amount_cents  INTEGER NOT NULL,
    reason        TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tickets (
    id           TEXT PRIMARY KEY,
    customer_id  TEXT REFERENCES customers(id),
    session_id   UUID,
    category     TEXT NOT NULL,
    priority     TEXT NOT NULL CHECK (priority IN ('low', 'medium', 'high', 'urgent')),
    reason       TEXT NOT NULL,
    summary      TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'open',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS account_changes (
    id           BIGSERIAL PRIMARY KEY,
    customer_id  TEXT NOT NULL REFERENCES customers(id),
    session_id   UUID,
    field        TEXT NOT NULL,
    old_value    TEXT,
    new_value    TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------------------
-- Conversation state (shared by chat and voice)
-- ---------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sessions (
    id                    UUID PRIMARY KEY,
    verified_customer_id  TEXT REFERENCES customers(id),
    status                TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'escalated', 'closed')),
    failed_verifications  INTEGER NOT NULL DEFAULT 0,
    turn_count            INTEGER NOT NULL DEFAULT 0,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS messages (
    id          BIGSERIAL PRIMARY KEY,
    session_id  UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn_id     TEXT NOT NULL,
    role        TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    mode        TEXT NOT NULL CHECK (mode IN ('text', 'voice')),
    content     TEXT NOT NULL,
    meta        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS messages_session_idx ON messages (session_id, id);

CREATE TABLE IF NOT EXISTS traces (
    id          BIGSERIAL PRIMARY KEY,
    session_id  UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn_id     TEXT NOT NULL,
    mode        TEXT NOT NULL,
    data        JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS traces_session_idx ON traces (session_id, id);

-- ---------------------------------------------------------------------------------------
-- Knowledge base (RAG)
-- ---------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kb_documents (
    doc_id      TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    category    TEXT NOT NULL,
    tiers       TEXT[] NOT NULL,
    checksum    TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS kb_chunks (
    id            BIGSERIAL PRIMARY KEY,
    doc_id        TEXT NOT NULL REFERENCES kb_documents(doc_id) ON DELETE CASCADE,
    chunk_index   INTEGER NOT NULL,
    section_path  TEXT NOT NULL,
    content       TEXT NOT NULL,
    tiers         TEXT[] NOT NULL,
    category      TEXT NOT NULL,
    token_count   INTEGER NOT NULL,
    embedding     vector({{EMBEDDING_DIM}}) NOT NULL,
    tsv           tsvector GENERATED ALWAYS AS (to_tsvector('english', section_path || ' ' || content)) STORED,
    UNIQUE (doc_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS kb_chunks_embedding_idx ON kb_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS kb_chunks_tsv_idx ON kb_chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS kb_chunks_tiers_idx ON kb_chunks USING gin (tiers);
